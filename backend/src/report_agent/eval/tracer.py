"""评测全链路 trace 落盘(评测升级 spec §6)。

一次评测运行一个目录 eval/traces/<run_id>/:
  run.json          # 运行元信息:时间/git commit/参数/指标快照
  case_<id>.json    # 每 case 阶段 I/O(解析→归一化→判定→证据→解读→护栏→评分)
  llm_calls.jsonl   # 每次 LLM 调用:purpose/prompt/response/耗时/状态
  judge.jsonl       # 每次评分调用与结果
  qa.jsonl          # 每条 QA:问题/回答/规则命中/评分
长文本字段截断预算 TRUNCATE_CHARS;运行结束 prune 保留最近 TRACE_KEEP_RUNS 次。
"""
import json
import time
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

TRACE_KEEP_RUNS = 5
TRUNCATE_CHARS = 2000


def _trunc(v: Any) -> Any:
    """长文本截断(评测升级 spec §6.1):str 超限截断并附原长标记;容器递归。"""
    if isinstance(v, str) and len(v) > TRUNCATE_CHARS:
        return v[:TRUNCATE_CHARS] + f"...[截断,原长 {len(v)}]"
    if isinstance(v, list):
        return [_trunc(x) for x in v]
    if isinstance(v, dict):
        return {k: _trunc(x) for k, x in v.items()}
    return v


class TraceSink:
    """评测运行目录写入器(纯文件 IO,无 infra 依赖)。"""

    def __init__(self, traces_root: Path, run_id: str):
        self.run_dir = traces_root / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def log_case(self, case_id: str, stage: str, payload: dict) -> None:
        """case_<id>.json 的阶段快照;同 stage 覆盖(调用方按 case 聚合后一次写入)。"""
        path = self.run_dir / f"case_{case_id}.json"
        data = (json.loads(path.read_text("utf-8")) if path.exists()
                else {"case_id": case_id, "stages": {}})
        data["stages"][stage] = _trunc(payload)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

    def log_jsonl(self, name: str, record: dict) -> None:
        with (self.run_dir / name).open("a", encoding="utf-8") as f:
            f.write(json.dumps(_trunc(record), ensure_ascii=False) + "\n")

    def log_llm(self, purpose: str, messages: Any, response: str, meta: dict) -> None:
        self.log_jsonl("llm_calls.jsonl",
                       {"purpose": purpose, "messages": messages, "response": response, **meta})

    def log_judge(self, record: dict) -> None:
        self.log_jsonl("judge.jsonl", record)

    def log_qa(self, record: dict) -> None:
        self.log_jsonl("qa.jsonl", record)

    def finish(self, meta: dict) -> None:
        (self.run_dir / "run.json").write_text(
            json.dumps(_trunc(meta), ensure_ascii=False, indent=2), "utf-8")

    @staticmethod
    def prune(traces_root: Path, keep: int = TRACE_KEEP_RUNS) -> None:
        """保留最近 keep 个运行目录(评测升级 spec §6.3)。"""
        if not traces_root.exists():
            return
        dirs = sorted([p for p in traces_root.iterdir() if p.is_dir()], reverse=True)
        for old in dirs[keep:]:
            for f in old.iterdir():
                f.unlink()
            old.rmdir()


class TracedLLM:
    """包装 DeepSeekClient:委托调用并把 I/O 落 llm_calls.jsonl(评测升级 spec §6.2)。

    只用于评测运行期(解读/总评/judge 等直接经 deps.llms 的调用),不改生产代码。
    chat QA 的 agent 自建 ChatOpenAI 不经 deps.llms,走 QATraceCallback 捕获。
    """

    def __init__(self, inner, sink: TraceSink, purpose: str):
        self._inner = inner
        self._sink = sink
        self.purpose = purpose

    async def chat(self, messages, temperature: float = 0.1, max_tokens: int = 2048) -> str:
        t0 = time.perf_counter()
        try:
            out = await self._inner.chat(messages, temperature=temperature, max_tokens=max_tokens)
            self._sink.log_llm(self.purpose, messages, out,
                               {"seconds": round(time.perf_counter() - t0, 2), "ok": True})
            return out
        except Exception as e:  # 记录后原样抛出,由调用方按既有语义处理
            self._sink.log_llm(self.purpose, messages, "",
                               {"seconds": round(time.perf_counter() - t0, 2),
                                "ok": False, "error": str(e)})
            raise

    async def chat_stream(self, messages, temperature: float = 0.1, max_tokens: int = 2048):
        # 评测路径不使用流式;仅委托,不落盘
        async for chunk in self._inner.chat_stream(messages, temperature=temperature,
                                                   max_tokens=max_tokens):
            yield chunk

    async def complete_json(self, messages, retry_feedback: bool = True,
                            max_tokens: int = 4096) -> dict:
        t0 = time.perf_counter()
        try:
            out = await self._inner.complete_json(messages, retry_feedback=retry_feedback,
                                                  max_tokens=max_tokens)
            self._sink.log_llm(self.purpose, messages, json.dumps(out, ensure_ascii=False),
                               {"seconds": round(time.perf_counter() - t0, 2), "ok": True})
            return out
        except Exception as e:
            self._sink.log_llm(self.purpose, messages, "",
                               {"seconds": round(time.perf_counter() - t0, 2),
                                "ok": False, "error": str(e)})
            raise


class QATraceCallback(BaseCallbackHandler):
    """chat QA 的 LLM/工具调用捕获(langchain callback 注入,评测升级 spec §6.2 修正:
    chat/agent.py 自建 ChatOpenAI 不经 deps.llms,包装法覆盖不到;经
    graph.ainvoke(config={"callbacks": [...]}) 注入,零生产改动)。"""

    def __init__(self, sink: TraceSink):
        super().__init__()
        self._sink = sink

    def _fmt_msg(self, m) -> dict:
        d = m.dict() if hasattr(m, "dict") else {"content": str(m)}
        return {"role": d.get("type") or d.get("role"), "content": d.get("content"),
                "tool_calls": d.get("tool_calls")}

    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        for p in prompts:
            self._sink.log_llm("qa_agent", [self._fmt_msg(m) for m in p], "", {"phase": "start"})

    def on_llm_end(self, response, **kwargs) -> None:
        texts = [g[0].text for g in response.generations if g]
        self._sink.log_llm("qa_agent", [], " | ".join(texts), {"phase": "end"})

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        self._sink.log_jsonl("qa_tools.jsonl",
                             {"name": serialized.get("name"), "input": input_str, "phase": "start"})

    def on_tool_end(self, output, **kwargs) -> None:
        self._sink.log_jsonl("qa_tools.jsonl", {"output": str(output), "phase": "end"})
