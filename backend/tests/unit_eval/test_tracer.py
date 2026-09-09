"""tracer 纯文件 IO 单测(无 infra;评测升级 spec §6)。"""
import json

from report_agent.eval.tracer import QATraceCallback, TracedLLM, TraceSink


def test_sink_writes_case_jsonl_and_truncates(tmp_path):
    sink = TraceSink(tmp_path, "run1")
    sink.log_case("r01", "normalize", {"pred": ["GLU"], "long": "x" * 5000})
    sink.log_jsonl("llm_calls.jsonl", {"purpose": "judge", "ok": True})
    sink.finish({"metrics": {"f1": 1.0}})
    case = json.loads((tmp_path / "run1" / "case_r01.json").read_text("utf-8"))
    assert case["stages"]["normalize"]["pred"] == ["GLU"]
    assert "截断" in case["stages"]["normalize"]["long"]
    lines = (tmp_path / "run1" / "llm_calls.jsonl").read_text("utf-8").splitlines()
    assert json.loads(lines[0])["ok"] is True
    run_meta = json.loads((tmp_path / "run1" / "run.json").read_text("utf-8"))
    assert run_meta["metrics"]["f1"] == 1.0


def test_log_case_merges_stages(tmp_path):
    sink = TraceSink(tmp_path, "run1")
    sink.log_case("r01", "normalize", {"a": 1})
    sink.log_case("r01", "rule", {"b": 2})
    case = json.loads((tmp_path / "run1" / "case_r01.json").read_text("utf-8"))
    assert set(case["stages"]) == {"normalize", "rule"}


def test_prune_keeps_last_n(tmp_path):
    for i in range(7):
        d = tmp_path / f"run{i}"
        d.mkdir()
        (d / "run.json").write_text("{}", encoding="utf-8")
    TraceSink.prune(tmp_path, keep=3)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["run4", "run5", "run6"]


class _FakeInner:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, temperature=0.1, max_tokens=2048):
        self.calls += 1
        return "ok"

    async def complete_json(self, messages, retry_feedback=True, max_tokens=4096):
        return {"a": 1}

    async def chat_stream(self, messages, temperature=0.1, max_tokens=2048):
        yield "x"


async def test_traced_llm_delegates_and_logs(tmp_path):
    sink = TraceSink(tmp_path, "r")
    inner = _FakeInner()
    out = await TracedLLM(inner, sink, purpose="judge").chat(
        [{"role": "user", "content": "hi"}])
    assert out == "ok" and inner.calls == 1
    rec = json.loads((tmp_path / "r" / "llm_calls.jsonl").read_text("utf-8").splitlines()[0])
    assert rec["purpose"] == "judge" and rec["ok"] is True and rec["response"] == "ok"


async def test_traced_llm_logs_failure_and_reraises(tmp_path):
    class Boom:
        async def chat(self, messages, temperature=0.1, max_tokens=2048):
            raise RuntimeError("down")

    sink = TraceSink(tmp_path, "r")
    try:
        await TracedLLM(Boom(), sink, purpose="interpret").chat([])
        raise AssertionError("应原样抛出")
    except RuntimeError:
        pass
    rec = json.loads((tmp_path / "r" / "llm_calls.jsonl").read_text("utf-8").splitlines()[0])
    assert rec["ok"] is False and "down" in rec["error"]


async def test_traced_llm_complete_json_logs_dict(tmp_path):
    sink = TraceSink(tmp_path, "r")
    out = await TracedLLM(_FakeInner(), sink, purpose="judge").complete_json([])
    assert out == {"a": 1}
    rec = json.loads((tmp_path / "r" / "llm_calls.jsonl").read_text("utf-8").splitlines()[0])
    assert rec["ok"] is True


def test_qa_callback_logs_llm_and_tools(tmp_path):
    from langchain_core.messages import HumanMessage

    sink = TraceSink(tmp_path, "r")
    cb = QATraceCallback(sink)
    cb.on_llm_start({}, [[HumanMessage(content="问题")]])
    cb.on_llm_end(type("R", (), {"generations": [[type("G", (), {"text": "回答"})()]]})())
    cb.on_tool_start({"name": "get_my_report"}, '{"report_id": "x"}')
    cb.on_tool_end("[{name: 甘油三酯}]")
    lines = (tmp_path / "r" / "llm_calls.jsonl").read_text("utf-8").splitlines()
    recs = [json.loads(l) for l in lines]
    assert recs[0]["purpose"] == "qa_agent" and recs[0]["phase"] == "start"
    assert recs[1]["response"] == "回答" and recs[1]["phase"] == "end"
    tools = (tmp_path / "r" / "qa_tools.jsonl").read_text("utf-8").splitlines()
    trecs = [json.loads(l) for l in tools]
    assert trecs[0]["name"] == "get_my_report" and trecs[1]["phase"] == "end"


def test_qa_callback_chat_model_start_real_dispatch(tmp_path):
    """走真实派发路径(评审 T3-Important):chat 模型实际触发的是
    on_chat_model_start,消息须保真为完整 content,而非拍平后逐字符切分。"""
    from langchain_core.callbacks.manager import CallbackManager
    from langchain_core.messages import HumanMessage

    sink = TraceSink(tmp_path, "r")
    manager = CallbackManager(handlers=[QATraceCallback(sink)])
    manager.on_chat_model_start({}, [[HumanMessage(content="你好呀")]])
    recs = [json.loads(l) for l in (tmp_path / "r" / "llm_calls.jsonl")
            .read_text("utf-8").splitlines()]
    assert len(recs) == 1 and recs[0]["phase"] == "start"
    assert isinstance(recs[0]["messages"], list) and len(recs[0]["messages"]) == 1
    assert recs[0]["messages"][0]["content"] == "你好呀"


def test_log_jsonl_concurrent_writes_do_not_interleave(tmp_path):
    """并发写回归(评审 T11-Important):LangChain sync 工具在线程池中执行,
    QATraceCallback 的 on_tool_start/on_tool_end 会并发触发;log_jsonl 若不加锁,
    Windows 下逐记录 open("a") 追加写会互相穿插,产生非法 JSON 行(真实运行
    qa_tools.jsonl 198 物理行仅 190 条可解析)。本测试断言物理行数 = 记录数,
    且每行均可 json.loads。"""
    import threading

    sink = TraceSink(tmp_path, "run")
    n_threads, n_calls = 16, 25

    def worker(tid: int) -> None:
        for i in range(n_calls):
            sink.log_jsonl("concurrent.jsonl",
                           {"i": i, "tid": tid, "payload": "x" * 200})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / "run" / "concurrent.jsonl").read_text("utf-8").splitlines()
    assert len(lines) == n_threads * n_calls
    recs = [json.loads(line) for line in lines]  # 任一行穿插即 JSONDecodeError
    assert len(recs) == n_threads * n_calls
