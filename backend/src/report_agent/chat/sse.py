"""astream_events → SSE 事件。事件: token/tool_call/evidence/done/safety/error。

时序与持久化:
- user 消息在流开始前异步持久化(独立 task,失败只告警)——报错/断连不丢用户消息,
  生成期间 GET /history 即含本轮提问(评审 Important-2);
- 流中采集 tool_calls(执行的工具名+参数摘要)与 evidence(search_knowledge 输出的
  [eN] 编号证据文本,评审 Important-3 最小落地,不扩 schema);
- 流完后跑规则护栏(完整回答组装后);BLOCK 或 SUSPECT+审核不通过 → safety 事件 +
  安全话术持久化;assistant 消息连同 tool_calls/evidence_ids/guardrail_flags 异步持久化。
"""
import asyncio
import json

from langchain_core.messages import HumanMessage

from report_agent.guardrails.audit import AuditLog
from report_agent.guardrails.reviewer import review_output
from report_agent.guardrails.rules import GuardrailContext, GuardrailResult, Verdict, rule_guardrail
from report_agent.observability import get_logger

log = get_logger(__name__)

SAFETY_TEXT = "本条回答未通过内容安全校验,已停止展示。请咨询医生获取专业意见。"

EVIDENCE_TOOL = "search_knowledge"  # 唯一产出可引用证据的工具
EVIDENCE_MARK = "[e"  # 工具输出中的证据编号标记,如 [e0](来源:...)
ARGS_SUMMARY_MAX = 200


def _args_summary(ev: dict) -> str:
    """on_tool_start → 工具调用参数摘要(截断,供 tool_calls 持久化)。"""
    inp = (ev.get("data") or {}).get("input")
    if inp is None:
        return ""
    try:
        s = json.dumps(inp, ensure_ascii=False)
    except TypeError:
        s = str(inp)
    return s[:ARGS_SUMMARY_MAX]


def _tool_output_text(ev: dict) -> str:
    """on_tool_end → 工具输出文本(data.output 为 ToolMessage,取 .content)。"""
    out = (ev.get("data") or {}).get("output")
    if out is None:
        return ""
    if isinstance(out, str):
        return out
    content = getattr(out, "content", None)
    if isinstance(content, str):
        return content
    return str(content or "")


async def sse_stream(graph, deps, session_id: str, user_message: str, report_id: str):
    """产出 {event, data} 事件字典,供 EventSourceResponse 序列化。"""
    # 评审 Important-2: user 消息流前异步持久化(独立 task,失败只告警)
    asyncio.create_task(_persist_user(deps, session_id, user_message))
    config = {"configurable": {"thread_id": session_id}}
    full_text = ""
    tool_calls: list[dict] = []
    evidence: list[str] = []
    try:
        async for ev in graph.astream_events(
            {"messages": [HumanMessage(content=user_message)]}, config=config, version="v2"
        ):
            kind = ev.get("event")
            if kind == "on_chat_model_stream":
                delta = ev["data"]["chunk"].content or ""
                if delta:
                    full_text += delta
                    yield {"event": "token", "data": delta}
            elif kind == "on_tool_start":
                name = ev.get("name")
                tool_calls.append({"name": name, "args_summary": _args_summary(ev)})
                yield {"event": "tool_call", "data": {"name": name, "status": "start"}}
            elif kind == "on_tool_end":
                name = ev.get("name")
                yield {"event": "tool_call", "data": {"name": name, "status": "end"}}
                if name == EVIDENCE_TOOL:
                    output = _tool_output_text(ev)
                    if EVIDENCE_MARK in output:  # 空检索拒答文本不构成证据,不发事件
                        evidence.append(output)
                        yield {"event": "evidence", "data": output}
    except Exception as e:  # noqa: BLE001
        log.error("chat_stream_error", error=str(e))
        yield {"event": "error", "data": "生成失败,请稍后重试"}
        return

    # ---- 流式护栏(完整回答组装后)----
    detail = await deps.db.get_report_detail(report_id)
    allowed = []
    if detail:
        for n in detail["normalized"]:
            allowed.extend([x for x in (n.get("value_num"), n.get("ref_low"), n.get("ref_high"))
                            if x is not None])
        if detail["meta"].get("age") is not None:
            allowed.append(float(detail["meta"]["age"]))
    gctx = GuardrailContext(allowed_numbers=allowed, require_disclaimer=False)
    audit = AuditLog(deps.session_factory)
    result = rule_guardrail(full_text, gctx)
    guardrail_flags = result.findings
    if result.verdict == Verdict.SUSPECT:
        await audit.log("guardrail_suspect", {"findings": result.findings}, session_id=session_id)
        review = await review_output(full_text, f"规则护栏疑似问题: {result.findings}", deps.llms.chat)
        if not review.passed:
            guardrail_flags += review.issues
            result = GuardrailResult(Verdict.BLOCK, guardrail_flags)
    if result.verdict == Verdict.BLOCK:
        await audit.log("guardrail_block", {"findings": guardrail_flags}, session_id=session_id)
        full_text = SAFETY_TEXT
        yield {"event": "safety", "data": SAFETY_TEXT}

    # ---- assistant 持久化(异步,失败只告警)——独立 task,不阻塞事件流关闭
    asyncio.create_task(_persist(deps, session_id, full_text, guardrail_flags,
                                 tool_calls, evidence))
    yield {"event": "done", "data": {"session_id": session_id,
                                     "guardrail": result.verdict.value}}


async def _persist_user(deps, session_id, user_message) -> None:
    try:
        await deps.db.add_message(session_id, "user", user_message)
    except Exception as e:  # noqa: BLE001 —— 写失败告警不阻塞(spec §4.3)
        log.error("chat_message_persist_failed", role="user", error=str(e))


async def _persist(deps, session_id, full_text, guardrail_flags, tool_calls, evidence) -> None:
    try:
        await deps.db.add_message(session_id, "assistant", full_text,
                                  tool_calls=tool_calls or None,
                                  evidence_ids=evidence or None,
                                  guardrail_flags=guardrail_flags or None)
    except Exception as e:  # noqa: BLE001 —— 写失败告警不阻塞(spec §4.3)
        log.error("chat_message_persist_failed", role="assistant", error=str(e))
