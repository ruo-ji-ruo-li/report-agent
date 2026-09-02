"""enforce_guardrail 闭环单测(spec §4.2/§5.5):PASS 直通 / suspect+审核放行 /
suspect 审核失败→重生成 1 次 / block→重生成→降级 / 事件入审计。零 DB/零网络(Fake 注入)。"""
import asyncio

from report_agent.guardrails.enforce import enforce_guardrail
from report_agent.guardrails.rules import GuardrailContext


def _ctx(**kw) -> GuardrailContext:
    base = {"allowed_numbers": [6.2, 3.9, 6.1], "require_disclaimer": True,
            "require_critical_warning": False}
    base.update(kw)
    return GuardrailContext(**base)


class FakeReviewLLM:
    def __init__(self, passed=True):
        self.passed = passed

    async def complete_json(self, messages, retry_feedback=True):
        return {"passed": self.passed, "issues": [] if self.passed else ["语义越界"]}


class FakeAudit:
    def __init__(self):
        self.events = []

    async def log(self, event_type, payload=None, report_id=None, task_id=None, session_id=None):
        self.events.append(event_type)


def _regen(feedback):
    async def fn(feedback):
        return "重新生成的安全内容。\n本内容不构成医学诊断。"
    return fn


def _degrade():
    async def fn():
        return "降级版:仅数值对照。\n本内容不构成医学诊断。"
    return fn


def test_enforce_pass_through():
    async def go():
        text, degraded = await enforce_guardrail(
            "您的血糖 6.2,略高于 6.1。\n本内容不构成医学诊断。", _ctx(),
            _regen(None), _degrade(), FakeReviewLLM(), FakeAudit())
        return text, degraded
    text, degraded = asyncio.run(go())
    assert not degraded and "6.2" in text


def test_enforce_suspect_review_pass_releases():
    async def go():
        text, degraded = await enforce_guardrail(
            "您的血糖 9.9 mmol/L。\n本内容不构成医学诊断。", _ctx(),
            _regen(None), _degrade(), FakeReviewLLM(passed=True), FakeAudit())
        return text, degraded
    text, degraded = asyncio.run(go())
    assert not degraded and "9.9" in text


def test_enforce_suspect_review_fail_regen_once_then_degrade():
    audit = FakeAudit()
    calls = []

    async def regen(feedback):
        calls.append(feedback)
        return "您被确诊为糖尿病。\n本内容不构成医学诊断。"  # 重生成仍违规

    async def degrade():
        return "降级版:仅数值对照。\n本内容不构成医学诊断。"

    async def go():
        text, degraded = await enforce_guardrail(
            "您的血糖 9.9 mmol/L。\n本内容不构成医学诊断。", _ctx(),
            regen, degrade, FakeReviewLLM(passed=False), audit)
        return text, degraded
    text, degraded = asyncio.run(go())
    assert degraded and text.startswith("降级版")
    assert len(calls) == 1  # 只重生成一次
    assert "review_failed" in audit.events and "degraded_output" in audit.events


def test_enforce_block_regen_then_degrade_and_suspect_regen_recovery():
    """补 spec §4.2 闭环两分支:block→重生成仍违规→降级版(不经 LLM 审核);
    suspect 审核不通过→重生成(规则通过)→放行不降级。"""
    # (1) block 直通重生成:重生成 1 次仍违规 → 降级版;审计含 guardrail_block/degraded_output
    audit = FakeAudit()
    calls = []

    async def regen_block(feedback):
        calls.append(feedback)
        return "建议每日服用阿司匹林 100mg。\n本内容不构成医学诊断。"  # 重生成仍违规

    async def degrade():
        return "降级版:仅数值对照。\n本内容不构成医学诊断。"

    async def go_block():
        text, degraded = await enforce_guardrail(
            "您被确诊为高血压,建议药物治疗。\n本内容不构成医学诊断。", _ctx(),
            regen_block, degrade, FakeReviewLLM(passed=False), audit)
        return text, degraded
    text, degraded = asyncio.run(go_block())
    assert degraded and text.startswith("降级版")
    assert len(calls) == 1  # 只重生成一次
    assert "guardrail_block" in audit.events and "degraded_output" in audit.events
    assert "review_failed" not in audit.events  # block 路径不触发二次 LLM 审核

    # (2) suspect 审核不通过 → 重生成(规则通过)→ 放行,不降级、无 degraded_output
    audit2 = FakeAudit()
    regen_calls = []

    async def regen_pass(feedback):
        regen_calls.append(feedback)
        return "您的血糖数值较参考区间略高,建议定期监测并复查。\n本内容不构成医学诊断。"

    async def go_suspect():
        text, degraded = await enforce_guardrail(
            "您的血糖 9.9 mmol/L。\n本内容不构成医学诊断。", _ctx(),
            regen_pass, degrade, FakeReviewLLM(passed=False), audit2)
        return text, degraded
    text2, degraded2 = asyncio.run(go_suspect())
    assert not degraded2 and "定期监测" in text2 and "9.9" not in text2
    assert len(regen_calls) == 1  # 只重生成一次
    assert "guardrail_suspect" in audit2.events and "review_failed" in audit2.events
    assert "degraded_output" not in audit2.events
