"""LangGraph 1.x 实际 API 对接回归(brief 范围外补充,理由见下)。

brief 的单测只覆盖纯函数(route_after_agent)与假件工具逻辑,无法发现
langgraph 1.2.11 的真实行为偏差——实测已抓到一例:
- 版本漂移(⚠️5): 1.x 将 async 工具包装为仅异步 StructuredTool,brief 的
  `tool_node.invoke(...)` 抛 "StructuredTool does not support sync invocation",
  已最小适配为 async tools_node + `await tool_node.ainvoke(...)`(agent.py)。
- 本文件用本地 stub OpenAI 兼容端点(无需 LLM key)驱动完整编译图,
  锁定: token/tool_call/done 事件契约、工具执行、流式护栏与持久化。
若 Controller 认为超出单测范围可删除,stub 模式下不触任何外部服务。
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from report_agent.chat.agent import build_chat_agent
from report_agent.chat.sse import sse_stream
from report_agent.knowledge.kg_client import IndicatorContext, IndicatorEntry, RangeSpec

ANSWER = "您的空腹血糖为6.5mmol/L,高于参考上限6.1mmol/L,属升高,建议咨询医生。"


class _StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send_json(self, payload: dict):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, chunks: list[dict]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        msgs = body.get("messages", [])
        stream = bool(body.get("stream"))
        model = body.get("model", "stub")
        prior_tool_calls = [m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
        if not prior_tool_calls:
            # 第一轮: 要求调用 get_my_report
            tc = {"id": "call_1", "type": "function",
                  "function": {"name": "get_my_report", "arguments": "{}"}}
            if stream:
                self._send_sse([
                    {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": model,
                     "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [tc]},
                                  "finish_reason": None}]},
                    {"id": "c2", "object": "chat.completion.chunk", "created": 1, "model": model,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                ])
            else:
                self._send_json({
                    "id": "c1", "object": "chat.completion", "created": 1, "model": model,
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": None,
                                             "tool_calls": [tc]},
                                 "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
            return
        # 之后: 文本回答,分两个 chunk 流式返回
        mid = len(ANSWER) // 2
        if stream:
            self._send_sse([
                {"id": "a1", "object": "chat.completion.chunk", "created": 1, "model": model,
                 "choices": [{"index": 0, "delta": {"role": "assistant", "content": ANSWER[:mid]},
                              "finish_reason": None}]},
                {"id": "a2", "object": "chat.completion.chunk", "created": 1, "model": model,
                 "choices": [{"index": 0, "delta": {"content": ANSWER[mid:]},
                              "finish_reason": "stop"}]},
            ])
        else:
            self._send_json({
                "id": "a1", "object": "chat.completion", "created": 1, "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": ANSWER},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


class _StubDeps:
    def __init__(self, port: int):
        self.db = _FakeDB()
        self.kg = _FakeKG()
        self.retriever = _FakeRetriever()
        self.session_factory = None
        # 断言回答通过规则护栏(PASS),SUSPECT 分支(deps.llms.chat)不会触发
        self.llms = None
        self.settings = type("S", (), {
            "chat_model": "stub-model",
            "deepseek_base_url": f"http://127.0.0.1:{port}/v1",
            "deepseek_api_key": "stub-key",
            "agent_max_tool_rounds": 8,
        })()


class _FakeKG:
    def __init__(self):
        self.entries = [IndicatorEntry(code="GLU", name="空腹血糖", aliases=["血糖"], unit="mmol/L")]
        self.specs = [RangeSpec(sex="any", age_min=18, age_max=100, low=3.9, high=6.1,
                                critical_low=2.8, critical_high=22.0, unit="mmol/L", source_note=None)]

    def list_indicators(self):
        return self.entries

    def indicator_context(self, code):
        return IndicatorContext(code=code, name="空腹血糖")

    def range_specs(self, code):
        return self.specs if code == "GLU" else []


class _FakeDB:
    def __init__(self):
        self.saved = []

    async def get_report_detail(self, report_id):
        return {"meta": {"sex": "male", "age": 40}, "items": [], "normalized": [
            {"item_name": "空腹血糖", "indicator_code": "GLU", "value_num": 6.5,
             "unit": "mmol/L", "status": "high", "ref_low": 3.9, "ref_high": 6.1,
             "critical": False, "section": "生化"}]}

    async def add_message(self, session_id, role, content, tool_calls=None,
                          evidence_ids=None, guardrail_flags=None):
        self.saved.append({"session_id": session_id, "role": role, "content": content,
                           "guardrail_flags": guardrail_flags})


class _FakeRetriever:
    async def search(self, q):
        return []


def test_agent_sse_full_chain_with_stub_openai():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        deps = _StubDeps(srv.server_address[1])
        graph = build_chat_agent(deps, "r1")
        events = asyncio.run(_collect(graph, deps))
    finally:
        srv.shutdown()

    kinds = [e["event"] for e in events]
    assert "tool_call" in kinds  # 工具调用事件
    assert "token" in kinds and "done" in kinds  # token 流 + done
    text = "".join(e["data"] for e in events if e["event"] == "token")
    assert "6.5" in text and "6.1" in text  # 回答确实流过
    tool_events = [e["data"] for e in events if e["event"] == "tool_call"]
    assert tool_events[0] == {"name": "get_my_report", "status": "start"}
    assert tool_events[1] == {"name": "get_my_report", "status": "end"}
    assert events[-1] == {"event": "done",
                          "data": {"session_id": "s1", "guardrail": "pass"}}
    # 持久化(user + assistant 各一条;PASS → guardrail_flags None)
    assert [m["role"] for m in deps.db.saved] == ["user", "assistant"]
    assert all(m["guardrail_flags"] is None for m in deps.db.saved)


async def _collect(graph, deps):
    out = []
    async for item in sse_stream(graph, deps, "s1", "我的空腹血糖正常吗", "r1"):
        out.append(item)
    await asyncio.sleep(0.1)  # 等 _persist 后台 task
    return out
