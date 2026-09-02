"""逐项解读 + 总评 + 降级版单测(零 DB/零网络,FakeLLM 双方法)。

锁定语义(brief):
- interpret_item: LLM JSON → ItemInterpretation 字段映射;LLMError → 模板解读
  ("建议线下咨询"),不抛异常
- generate_summary: LLMError → template_summary 规则总评
- build_advice_summary: 代码排序 urgent > specialist > recheck > lifestyle,危急置顶
- build_degraded_interpretation: 四段式 doc,degraded=True,含免责声明
"""
import asyncio

from report_agent.knowledge.kg_client import ConditionFact, IndicatorContext
from report_agent.llm.client import LLMError
from report_agent.pipeline.interpret import (
    DISCLAIMER,
    build_advice_summary,
    build_degraded_interpretation,
    generate_summary,
    interpret_item,
    template_summary,
)
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus


def _judgment(status=ItemStatus.HIGH, code="GLU", value_num=7.2, critical=False) -> ItemJudgment:
    return ItemJudgment(indicator_code=code, name="空腹血糖", value_num=value_num, value_text=None,
                        unit="mmol/L", status=status, ref_low=3.9, ref_high=6.1,
                        critical=critical, range_source="report")


def _ctx() -> IndicatorContext:
    return IndicatorContext(code="GLU", name="空腹血糖", high_suggests=[
        ConditionFact(name="糖尿病风险", description=None, strength="strong", note="空腹受损")])


class FakeLLM:
    """interpret_item 走 complete_json,generate_summary 走 chat —— 两个方法都要提供。"""

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


def test_interpret_item_maps_llm_json():
    llm = FakeLLM(result={
        "meaning": "血糖偏高", "risks": ["糖尿病风险"], "advice_level": "recheck",
        "advice": "复查空腹血糖", "evidence_ids": ["e0"],
    })
    out = asyncio.run(interpret_item(_judgment(), _ctx(), [], llm))
    assert out.status == "high" and out.advice_level == "recheck"
    assert out.evidence_ids == ["e0"]


def test_interpret_item_llm_failure_falls_back_to_template():
    out = asyncio.run(interpret_item(_judgment(), _ctx(), [], FakeLLM(error=True)))
    assert "线下咨询" in out.advice
    assert out.meaning != ""


def test_interpret_item_non_dict_json_falls_back_to_template():
    """合法 JSON 但非 dict(如 [])→ 视同 LLM 失败走模板解读,不得冒泡 AttributeError。"""
    out = asyncio.run(interpret_item(_judgment(), _ctx(), [], FakeLLM(result=[])))
    assert "线下咨询" in out.advice
    assert out.meaning != ""


def test_summary_llm_failure_uses_template():
    assert "2 项异常" in template_summary(2, 1, ["代谢综合征倾向"], 0)
    assert "危急" in template_summary(2, 1, [], 0)
    out = asyncio.run(generate_summary([], [], 0, FakeLLM(error=True)))
    assert "异常" in out


def test_advice_summary_orders_urgent_first():
    from report_agent.pipeline.interpret import ItemInterpretation

    items = [
        ItemInterpretation(indicator_code="TC", name="总胆固醇", status="high", value_text="6.0",
                           meaning="", risks=[], advice_level="lifestyle", advice="生活方式", evidence_ids=[]),
        ItemInterpretation(indicator_code="GLU", name="空腹血糖", status="critical_high",
                           value_text="25", meaning="", risks=[], advice_level="urgent",
                           advice="尽快就医", evidence_ids=[]),
        ItemInterpretation(indicator_code="ALT", name="谷丙转氨酶", status="high", value_text="80",
                           meaning="", risks=[], advice_level="recheck", advice="复查", evidence_ids=[]),
    ]
    text = build_advice_summary(items)
    assert text.index("尽快就医") < text.index("复查") < text.index("生活方式")


def test_degraded_interpretation_has_table_and_disclaimer():
    doc = build_degraded_interpretation([_judgment(critical=True)])
    assert doc["degraded"] is True
    assert "空腹血糖" in doc["summary"] and "线下就医" in doc["summary"]
    assert DISCLAIMER in doc["disclaimer"]
