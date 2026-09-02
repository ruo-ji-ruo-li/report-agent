"""chat API 端点单测(app.state 注入 fake,不触 DB/真 LLM)。

brief Step 8 逐字覆盖:创建会话、会话不存在 404、历史记录契约。
补充(评审 Important-2 端点契约): POST /messages 的 SSE 直答场景用本地 stub
OpenAI 端点(无 key)驱动——事件流(token/done)与 user 先写、assistant 后写、
history 端点一致性;含工具/达限的真模型语义仍由 smoke 兜底。
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi.testclient import TestClient

from report_agent.api.app import create_app

_STUB_ANSWER = "您的空腹血糖为6.5mmol/L,高于参考上限6.1mmol/L,属升高,建议咨询医生。"


class _StubHandler(BaseHTTPRequestHandler):
    """任何 chat.completions 请求直接流式返回 _STUB_ANSWER(不请求工具)。"""

    def log_message(self, *args):
        pass

    def _send_json(self, payload: dict):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        stream = bool(body.get("stream"))
        model = body.get("model", "stub")
        if not stream:
            self._send_json({
                "id": "a1", "object": "chat.completion", "created": 1, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": _STUB_ANSWER},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        mid = len(_STUB_ANSWER) // 2
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for c in [
            {"id": "a1", "object": "chat.completion.chunk", "created": 1, "model": model,
             "choices": [{"index": 0, "delta": {"role": "assistant",
                                                "content": _STUB_ANSWER[:mid]},
                          "finish_reason": None}]},
            {"id": "a2", "object": "chat.completion.chunk", "created": 1, "model": model,
             "choices": [{"index": 0, "delta": {"content": _STUB_ANSWER[mid:]},
                          "finish_reason": "stop"}]},
        ]:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class FakeDBA:
    def __init__(self):
        self.sessions = {"s1": {"id": "s1", "report_id": "r1"}}
        self.messages = []

    async def get_report_detail(self, report_id):
        # normalized 带数值:让 _STUB_ANSWER 的 6.5/6.1 通过数字一致性护栏(PASS)
        return {"meta": {"sex": "male", "age": 40}, "items": [], "normalized": [
            {"item_name": "空腹血糖", "indicator_code": "GLU", "value_num": 6.5,
             "unit": "mmol/L", "status": "high", "ref_low": 3.9, "ref_high": 6.1,
             "critical": False, "section": "生化"}]}

    async def create_session(self, report_id):
        sid = "s1"
        self.sessions[sid] = {"id": sid, "report_id": report_id}
        return sid

    async def get_session(self, session_id):
        return self.sessions.get(session_id)

    async def get_messages(self, session_id):
        return [m for m in self.messages if m["session_id"] == session_id]

    async def add_message(self, session_id, role, content, tool_calls=None,
                          evidence_ids=None, guardrail_flags=None):
        self.messages.append({"session_id": session_id, "role": role, "content": content})


def _client():
    app = create_app(deps_builder=lambda settings: object())
    app.state.db_access = FakeDBA()
    app.state.deps = type("D", (), {
        "db": app.state.db_access,
        "session_factory": None,
        "llms": None,
        "settings": type("S", (), {"chat_model": "x", "deepseek_base_url": "http://x",
                                   "deepseek_api_key": "", "agent_max_tool_rounds": 8})(),
    })()
    return TestClient(app), app


def test_create_chat_session():
    client, _ = _client()
    resp = client.post("/api/reports/r1/chat/sessions")
    assert resp.status_code == 200
    assert resp.json()["session_id"] == "s1"


def test_chat_session_not_found():
    client, _ = _client()
    assert client.post("/api/chat/sessions/nope/messages",
                       json={"content": "hi"}).status_code == 404


def test_history_empty_and_saved():
    client, app = _client()
    assert client.get("/api/chat/sessions/s1/history").json() == {"messages": []}
    assert app.state.db_access.messages == []


def test_post_message_sse_persists_user_first_and_history():
    """端点契约(评审 Important-2):stub 驱动 POST /messages SSE——
    token/done 事件、user 先写 assistant 后写、单遍答案、history 端点一致。"""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        app = create_app(deps_builder=lambda settings: object())
        db = FakeDBA()
        app.state.db_access = db
        app.state.deps = type("D", (), {
            "db": db,
            "session_factory": None,
            "llms": None,
            "settings": type("S", (), {
                "chat_model": "stub",
                "deepseek_base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1",
                "deepseek_api_key": "stub-key",
                "agent_max_tool_rounds": 8})(),
        })()
        client = TestClient(app)
        resp = client.post("/api/chat/sessions/s1/messages",
                           json={"content": "我的空腹血糖正常吗"})
        assert resp.status_code == 200
        body = resp.text
        assert "event: token" in body
        assert "event: done" in body
        assert "event: error" not in body
        deadline = time.time() + 3
        while len(db.messages) < 2 and time.time() < deadline:
            time.sleep(0.02)
        assert [m["role"] for m in db.messages] == ["user", "assistant"]
        assert db.messages[0]["content"] == "我的空腹血糖正常吗"
        assert db.messages[1]["content"] == _STUB_ANSWER  # 单遍,无 final 双答拼接
        hist = client.get("/api/chat/sessions/s1/history").json()
        assert [m["role"] for m in hist["messages"]] == ["user", "assistant"]
    finally:
        srv.shutdown()
