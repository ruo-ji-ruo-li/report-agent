"""护栏执行流程:规则 → (suspect: LLM 审核)→ 重生成 1 次 → 安全降级版。spec §5.5。"""
from report_agent.guardrails.reviewer import review_output
from report_agent.guardrails.rules import GuardrailContext, Verdict, rule_guardrail


async def enforce_guardrail(text, ctx: GuardrailContext, regen_fn, degrade_fn, llm, audit) -> tuple[str, bool]:
    """返回 (最终文本, 是否降级)。重生成最多 1 次,仍不通过 → degrade_fn。"""
    result = rule_guardrail(text, ctx)
    if result.verdict == Verdict.PASS:
        return text, False

    if result.verdict == Verdict.SUSPECT:
        await audit.log("guardrail_suspect", {"findings": result.findings})
        review = await review_output(text, f"规则护栏疑似问题: {result.findings}", llm)
        if review.passed:
            return text, False
        await audit.log("review_failed", {"issues": review.issues, "findings": result.findings})
        feedback = result.findings + review.issues
    else:
        await audit.log("guardrail_block", {"findings": result.findings})
        feedback = result.findings

    try:
        regenerated = await regen_fn(feedback=feedback)
    except Exception as e:  # noqa: BLE001, F841 —— 重生成失败直接降级
        regenerated = None
    if regenerated is not None:
        again = rule_guardrail(regenerated, ctx)
        if again.verdict == Verdict.PASS:
            return regenerated, False
        if again.verdict == Verdict.SUSPECT:
            review2 = await review_output(regenerated, f"规则护栏疑似问题: {again.findings}", llm)
            if review2.passed:
                return regenerated, False

    degraded = await degrade_fn()
    await audit.log("degraded_output", {"reason": "guardrail_not_passed"})
    return degraded, True
