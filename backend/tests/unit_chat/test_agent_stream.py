"""LangGraph 1.x 实际 API 对接回归 + 评审 Important-1/2/3 回归。

本地 stub OpenAI 兼容端点(127.0.0.1 随机端口,零外部依赖、零 key)驱动真实编译图。

覆盖:
- 版本漂移(⚠️5): 1.x 将 async 工具包装为仅异步 StructuredTool,brief 的
  `tool_node.invoke(...)` 不可用,已最小适配为 async tools_node + ainvoke(agent.py);
- Important-1: final 双答修复回归——直答路径模型只调 1 次、token 文本单遍;
  达限(带 tool_calls)路径仍正常收敛调用(单遍);
- Important-2: user 消息流前持久化——正常与报错路径均先落 user;
- Important-3: search_knowledge 结束产出 evidence 事件;assistant 持久化携带
  tool_calls([{name, args_summary}]) 与 evidence_ids(evidence payload 列表)。
"""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from report_agent.chat.agent import build_chat_agent
from report_agent.chat.sse import sse_stream
from report_agent.chat.tools import REFUSAL_TEXT
from report_agent.knowledge.kg_client import IndicatorContext, IndicatorEntry, RangeSpec
from report_agent.retrieval.hybrid import Evidence

ANSWER = "您的空腹血糖为6.5mmol/L,高于参考上限6.1mmol/L,属升高,建议咨询医生。"

EVIDENCE_OUTPUT = "[e0](来源:dense,标题:空腹血糖)\n空腹血糖升高与糖尿病风险相关"


def _make_server(scenario: str):
    """scenario: answer(直接文本作答)/ tools_then_answer(先并行 2 工具再作答)/
    limit_loop(带工具即请求工具,不带工具才作答——用于达限收敛)。返回 (server, counter)。"""
    counter = {"requests": 0}

    class Handler(BaseHTTPRequestHandler):
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

        def _answer(self, stream: bool, model: str):
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

        def _tool_calls_reply(self, stream: bool, model: str, calls: list[dict]):
            if stream:
                self._send_sse([
                    {"id": "c1", "object": "chat.completion.chunk", "created": 1, "model": model,
                     "choices": [{"index": 0,
                                  "delta": {"role": "assistant", "tool_calls": calls},
                                  "finish_reason": None}]},
                    {"id": "c2", "object": "chat.completion.chunk", "created": 1, "model": model,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                ])
            else:
                self._send_json({
                    "id": "c1", "object": "chat.completion", "created": 1, "model": model,
                    "choices": [{"index": 0,
                                 "message": {"role": "assistant", "content": None,
                                             "tool_calls": calls},
                                 "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

        def do_POST(self):
            counter["requests"] += 1
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n))
            msgs = body.get("messages", [])
            stream = bool(body.get("stream"))
            has_tools = bool(body.get("tools"))
            model = body.get("model", "stub")
            prev_tool_calls = [m for m in msgs
                               if m.get("role") == "assistant" and m.get("tool_calls")]
            if scenario == "answer":
                self._answer(stream, model)
            elif scenario == "limit_loop":
                if has_tools:
                    self._tool_calls_reply(stream, model, [
                        {"id": f"call_{counter['requests']}", "type": "function",
                         "function": {"name": "get_my_report", "arguments": "{}"}}])
                else:
                    self._answer(stream, model)
            else:  # tools_then_answer
                if prev_tool_calls:
                    self._answer(stream, model)
                else:
                    self._tool_calls_reply(stream, model, [
                        {"id": "call_1", "type": "function",
                         "function": {"name": "get_my_report", "arguments": "{}"}},
                        {"id": "call_2", "type": "function",
                         "function": {"name": "search_knowledge",
                                      "arguments": '{"query": "空腹血糖"}'}},
                    ])

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, counter


class _FakeKG:
    def __init__(self):
        self.entries = [IndicatorEntry(code="GLU", name="空腹血糖", aliases=["血糖"], unit="mmol/L")]
        self.specs = [RangeSpec(sex="any", age_min=18, age_max=100, low=3.9, high=6.1,
                                critical_low=2.8, critical_high=22.0, unit="mmol/L", source_note=None)]

    def find_indicator(self, query):
        return next((e for e in self.entries
                     if query in {e.name, *e.aliases}), None)

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
                           "tool_calls": tool_calls, "evidence_ids": evidence_ids,
                           "guardrail_flags": guardrail_flags})


class _FakeRetriever:
    async def search(self, q):
        return [Evidence(text="空腹血糖升高与糖尿病风险相关", source="dense", title="空腹血糖")]


class _FakeDeps:
    def __init__(self, srv, max_rounds: int = 8):
        self.db = _FakeDB()
        self.kg = _FakeKG()
        self.retriever = _FakeRetriever()
        self.session_factory = None
        self.llms = None  # 断言通过规则护栏(PASS),SUSPECT 分支不触发
        self.settings = type("S", (), {
            "chat_model": "stub-model",
            "deepseek_base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1",
            "deepseek_api_key": "stub-key",
            "agent_max_tool_rounds": max_rounds,
        })()


def _run(coro):
    return asyncio.run(coro)


async def _collect(graph, deps, user_message="我的空腹血糖正常吗"):
    out = []
    async for item in sse_stream(graph, deps, "s1", user_message, "r1"):
        out.append(item)
    await asyncio.sleep(0.1)  # 等 _persist_user/_persist 后台 task
    return out


def _kinds(events):
    return [e["event"] for e in events]


def test_two_tools_chain_events_evidence_single_answer():
    """并行 2 工具(报告+知识检索)→ 直答:证据事件、tool_calls/evidence_ids 双写、
    Important-1 单遍答案。"""
    srv, counter = _make_server("tools_then_answer")
    try:
        deps = _FakeDeps(srv)
        events = _run(_collect(build_chat_agent(deps, "r1"), deps))
    finally:
        srv.shutdown()

    kinds = _kinds(events)
    assert kinds.count("tool_call") == 4  # 2 工具 × start/end
    assert "token" in kinds and kinds[-1] == "done"
    # evidence 事件:search_knowledge 输出文本(已含 [eN] 编号与来源)
    ev_events = [e["data"] for e in events if e["event"] == "evidence"]
    assert ev_events == [EVIDENCE_OUTPUT]
    idx_ev = next(i for i, e in enumerate(events) if e["event"] == "evidence")
    idx_start = next(i for i, e in enumerate(events)
                     if e["event"] == "tool_call"
                     and e["data"] == {"name": "search_knowledge", "status": "start"})
    assert idx_ev > idx_start
    # Important-1: token 文本 = 单遍 ANSWER(此前为两遍拼接)
    text = "".join(e["data"] for e in events if e["event"] == "token")
    assert text == ANSWER
    assert counter["requests"] == 2  # 1 次工具请求 + 1 次作答;final 短路不再调模型
    # 持久化: user 先写,assistant 带 tool_calls/evidence_ids
    assert [m["role"] for m in deps.db.saved] == ["user", "assistant"]
    user, assistant = deps.db.saved
    assert user["content"] == "我的空腹血糖正常吗"
    assert assistant["content"] == ANSWER
    assert assistant["guardrail_flags"] is None
    assert {tuple(sorted((k, str(v)) for k, v in t.items()))
            for t in assistant["tool_calls"]} == {
        (("args_summary", "{}"), ("name", "get_my_report")),
        (("args_summary", '{"query": "空腹血糖"}'), ("name", "search_knowledge")),
    }
    assert assistant["evidence_ids"] == [EVIDENCE_OUTPUT]
    assert events[-1] == {"event": "done",
                          "data": {"session_id": "s1", "guardrail": "pass"}}


def test_direct_answer_single_model_call():
    """Important-1 回归:无工具调用路径 final_node 短路——模型只调 1 次、
    token 单遍、持久化内容即答案。"""
    srv, counter = _make_server("answer")
    try:
        deps = _FakeDeps(srv)
        events = _run(_collect(build_chat_agent(deps, "r1"), deps))
    finally:
        srv.shutdown()

    assert counter["requests"] == 1  # final 短路:无第二次模型调用
    text = "".join(e["data"] for e in events if e["event"] == "token")
    assert text == ANSWER
    assert [m["role"] for m in deps.db.saved] == ["user", "assistant"]
    assert deps.db.saved[1]["content"] == ANSWER
    assert deps.db.saved[1]["tool_calls"] is None
    assert deps.db.saved[1]["evidence_ids"] is None


def test_converge_at_round_limit_still_single_answer():
    """达限收敛(最后消息带 tool_calls → final 收敛调用)正常且答案仍单遍。"""
    srv, counter = _make_server("limit_loop")
    try:
        deps = _FakeDeps(srv, max_rounds=3)
        events = _run(_collect(build_chat_agent(deps, "r1"), deps))
    finally:
        srv.shutdown()

    assert counter["requests"] == 5  # 3 次工具请求 + 1 次达限工具请求 + 1 次收敛
    kinds = _kinds(events)
    assert kinds.count("tool_call") == 6  # 3 轮工具执行 × start/end
    text = "".join(e["data"] for e in events if e["event"] == "token")
    assert text == ANSWER  # 收敛调用产出,单遍
    assert events[-1]["event"] == "done"


def test_user_message_persisted_on_stream_error():
    """Important-2 回归:图报错路径(user 消息流前已持久化,assistant 不写)。"""
    class BoomGraph:
        async def astream_events(self, *args, **kwargs):
            raise RuntimeError("boom")
            yield  # pragma: no cover —— async generator,首次迭代即抛

    deps = type("D", (), {"db": _FakeDB(), "session_factory": None})()  # 报错路径只用 db.add_message

    events = _run(_collect(BoomGraph(), deps))
    assert events == [{"event": "error", "data": "生成失败,请稍后重试"}]
    assert [m["role"] for m in deps.db.saved] == ["user"]
    assert deps.db.saved[0]["content"] == "我的空腹血糖正常吗"


class _MemAuditSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class _MemAuditFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        s = _MemAuditSession()
        self.sessions.append(s)
        return s

    @property
    def events(self) -> list:
        return [e for s in self.sessions for e in s.added]


def test_tool_rounds_reset_per_user_turn_checkpointer_memory():
    """F4:checkpointer 按 thread_id 记忆 state 时,每次提问输入必须显式 tool_rounds=0 ——
    否则轮数跨提问会话级累计,8 轮后该会话永久丧失工具。第二轮必须仍从 0 起算。"""

    class MemGraph:
        """模拟带 checkpointer 的真实图:跨调用累计 tool_rounds;输入缺省键继承记忆。"""

        def __init__(self):
            self.memory_tool_rounds = 0
            self.seen_turns = []

        async def astream_events(self, state, config=None, version=None):
            if False:
                yield
            # LangGraph 合并语义:输入显式键覆盖记忆,缺省键继承 checkpoint 值
            merged = state.get("tool_rounds", self.memory_tool_rounds)
            self.seen_turns.append(merged)
            self.memory_tool_rounds = merged + 1  # 模拟 tools_node 每轮 +1

    deps = type("D", (), {"db": _FakeDB(), "session_factory": None})()
    graph = MemGraph()
    for _ in range(2):  # 同一会话两轮提问(共享同一 graph/记忆)
        events = _run(_collect(graph, deps))
        assert events[-1]["event"] == "done"
    # 第一轮累计后(模拟已用 1 轮),第二轮输入仍必须从 0 起算,而非继承记忆值 1
    assert graph.seen_turns == [0, 0]
    # messages 仍经 add_messages 追加历史:两轮 user 消息都在
    assert [m["content"] for m in deps.db.saved if m["role"] == "user"] == \
        ["我的空腹血糖正常吗", "我的空腹血糖正常吗"]


def test_chat_refusal_emits_audit_event():
    """F6(d):search_knowledge 拒答(空结果/低于 RRF 阈值,输出即 REFUSAL_TEXT)→
    audit chat_refusal(带截断 query 与 session 关联)。"""
    class RefusalGraph:
        async def astream_events(self, state, config=None, version=None):
            yield {
                "event": "on_tool_start", "name": "search_knowledge", "run_id": "rid-1",
                "data": {"input": {"query": "语料外的问题"}},
            }
            yield {
                "event": "on_tool_end", "name": "search_knowledge", "run_id": "rid-1",
                "data": {"output": REFUSAL_TEXT},
            }

    factory = _MemAuditFactory()
    deps = type("D", (), {"db": _FakeDB(), "session_factory": factory})()
    events = _run(_collect(RefusalGraph(), deps))
    # 拒答文本不是证据:不发 evidence 事件
    assert all(e["event"] != "evidence" for e in events)
    assert [e["event"] for e in events] == ["tool_call", "tool_call", "done"]
    assert [e.event_type for e in factory.events] == ["chat_refusal"]
    ev = factory.events[0]
    assert ev.payload["query"] == "语料外的问题"
    assert ev.session_id == "s1"
    assert len(ev.payload["query"]) <= 200  # query 截断
