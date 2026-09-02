"""chat API 端点单测(app.state 注入 fake,不触 DB/LLM)。

brief Step 8 逐字。覆盖:创建会话、会话不存在 404、历史记录契约。
POST messages 的 SSE 全链路需真模型(依赖本机无 LLM key),由 smoke 兜底。
"""
from fastapi.testclient import TestClient

from report_agent.api.app import create_app


class FakeDBA:
    def __init__(self):
        self.sessions = {"s1": {"id": "s1", "report_id": "r1"}}
        self.messages = []

    async def get_report_detail(self, report_id):
        return {"meta": {"sex": "male", "age": 40}, "items": [], "normalized": []}

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


class FakeLLMs:
    class _Chat:
        @property
        def chat(self):
            return self

        async def complete_json(self, messages, retry_feedback=True):
            return {"passed": True, "issues": []}

    async def get(self):
        return type("LLMClients", (), {"chat": FakeLLMs._Chat()})()


def _client():
    app = create_app(deps_builder=lambda settings: object())
    app.state.db_access = FakeDBA()
    app.state.deps = type("D", (), {
        "db": app.state.db_access,
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
