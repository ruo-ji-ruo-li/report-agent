"""复查计划单测(零 DB/零网络,FakeLLM)。

锁定语义(brief):
- build_followup_items: 危急值 → 立即就医条目(timeframe=立即);普通异常 → 复查条目
- smooth_basis: LLM 输出 schema 不符/LLMError → None,回退模板原文(不抛)
- build_followup_plan: degraded 标志 = LLM 润色是否发生;危急值条目必含"立即"
"""
import asyncio

from report_agent.knowledge.kg_client import IndicatorContext
from report_agent.llm.client import LLMError
from report_agent.pipeline.followup_plan import (
    FollowupPlanDoc,
    build_followup_items,
    build_followup_plan,
)
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus


def _judgment(status=ItemStatus.HIGH, code="GLU", value_num=7.2, critical=False) -> ItemJudgment:
    return ItemJudgment(indicator_code=code, name="空腹血糖", value_num=value_num, value_text=None,
                        unit="mmol/L", status=status, ref_low=3.9, ref_high=6.1,
                        critical=critical, range_source="report")


class FakeLLM:
    def __init__(self, result=None, error=False):
        self.result = result
        self.error = error
        self.calls = []

    async def complete_json(self, messages, retry_feedback=True):
        self.calls.append(messages)
        if self.error:
            raise LLMError("down")
        return self.result

    async def chat(self, messages, temperature=0.1, **kw):
        self.calls.append(messages)
        if self.error:
            raise LLMError("down")
        return self.result


def test_followup_items_critical_and_recheck():
    j_crit = _judgment(status=ItemStatus.CRITICAL_HIGH, value_num=25, critical=True)
    j_high = _judgment()
    ctx = IndicatorContext(code="GLU", name="空腹血糖")
    items = build_followup_items([j_crit, j_high], [], {"GLU": ctx})
    texts = " | ".join(f"{i.item}/{i.timeframe}/{i.department}" for i in items)
    assert "立即" in texts  # 危急 → 立即就医条目
    assert "空腹血糖" in texts
    assert items[0].timeframe == "立即"


def test_build_followup_plan_smooth_llm_error_degraded_keeps_template():
    """LLM 润色失败 → degraded=True,条目内容保持规则模板原文。"""
    j_high = _judgment()
    ctx = IndicatorContext(code="GLU", name="空腹血糖")
    doc = asyncio.run(build_followup_plan([j_high], [], {"GLU": ctx}, FakeLLM(error=True)))
    assert doc.degraded is True
    assert len(doc.items) == 1
    assert "复查" in doc.items[0].basis and doc.items[0].timeframe == "遵医嘱"


def test_build_followup_plan_schema_mismatch_degraded():
    """smooth_basis 输出键集不符 → None 回退(不抛),doc.degraded=True。"""
    j_high = _judgment()
    ctx = IndicatorContext(code="GLU", name="空腹血糖")
    llm = FakeLLM(result={"0": "仅一条", "wrong": "键集不符"})
    doc = asyncio.run(build_followup_plan([j_high], [], {"GLU": ctx}, llm))
    assert doc.degraded is True
    assert doc.items[0].basis == "空腹血糖 判定升高,建议复查"


def test_build_followup_plan_smooth_ok_not_degraded():
    """LLM 润色成功(键集完整)→ 依据被替换,degraded=False。"""
    j_high = _judgment()
    ctx = IndicatorContext(code="GLU", name="空腹血糖")
    llm = FakeLLM(result={"0": "血糖偏高,建议门诊复查以确认趋势"})
    doc = asyncio.run(build_followup_plan([j_high], [], {"GLU": ctx}, llm))
    assert doc.degraded is False
    assert doc.items[0].basis == "血糖偏高,建议门诊复查以确认趋势"


def test_build_followup_plan_empty_no_items_no_llm_calls():
    """无异常项 → 空复查单;items 为空时不再调 LLM。"""
    doc = asyncio.run(build_followup_plan([], [], {}, FakeLLM(result=None)))
    assert isinstance(doc, FollowupPlanDoc)
    assert doc.items == []
