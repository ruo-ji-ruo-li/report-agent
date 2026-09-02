"""astream_events → SSE 事件。流完后:规则护栏 + 消息持久化 + done/safety 事件。

brief 最小修正: GuardrailResult 参与 SUSPECT→BLOCK 升级(SUSPECT + 审核不通过),
brief 漏 import,补入 rules 导入行。
"""
import asyncio

from langchain_core.messages import HumanMessage

from report_agent.guardrails.audit import AuditLog
from report_agent.guardrails.reviewer import review_output
from report_agent.guardrails.rules import GuardrailContext, GuardrailResult, Verdict, rule_guardrail
from report_agent.observability import get_logger

log = get_logger(__name__)

SAFETY_TEXT = "本条回答未通过内容安全校验,已停止展示。请咨询医生获取专业意见。"


async def sse_stream(graph, deps, session_id: str, user_message: str, report_id: str):
    """产出 {event, data} 事件字典,供 EventSourceResponse 序列化。"""
    config = {"configurable": {"thread_id": session_id}}
    full_text = ""
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
                yield {"event": "tool_call", "data": {"name": ev.get("name"), "status": "start"}}
            elif kind == "on_tool_end":
                yield {"event": "tool_call", "data": {"name": ev.get("name"), "status": "end"}}
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

    # ---- 持久化(异步,失败只告警)——通过独立 task 写,不阻塞事件流关闭
    asyncio.create_task(_persist(deps, session_id, user_message, full_text, guardrail_flags))
    yield {"event": "done", "data": {"session_id": session_id,
                                     "guardrail": result.verdict.value}}


async def _persist(deps, session_id, user_message, full_text, guardrail_flags) -> None:
    try:
        await deps.db.add_message(session_id, "user", user_message)
        await deps.db.add_message(session_id, "assistant", full_text,
                                  guardrail_flags=guardrail_flags or None)
    except Exception as e:  # noqa: BLE001 —— 写失败告警不阻塞(spec §4.3)
        log.error("chat_message_persist_failed", error=str(e))
