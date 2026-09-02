import asyncio

from report_agent.knowledge.kg_client import IndicatorEntry
from report_agent.llm.client import LLMError
from report_agent.parsing.normalizer import Normalizer, convert_value, match_indicator
from report_agent.parsing.schemas import RawReportItem

GLU = IndicatorEntry(code="GLU", name="空腹血糖", aliases=["血糖", "FBG"], unit="mmol/L",
                     unit_conversions={"mg/dL": 0.0555})
WBC = IndicatorEntry(code="WBC", name="白细胞计数", aliases=["白细胞", "WBC计数"], unit="10^9/L")
ENTRIES = [GLU, WBC]


def test_match_exact_alias_and_normalized_variants():
    assert match_indicator("空腹血糖", ENTRIES) == "GLU"
    assert match_indicator("血糖", ENTRIES) == "GLU"
    assert match_indicator("白细胞计数", ENTRIES) == "WBC"
    # 全角/空格/大小写变体
    assert match_indicator("ＦＢＧ", ENTRIES) == "GLU"
    assert match_indicator("wbc计数", ENTRIES) == "WBC"
    # 括号变体:"空腹葡萄糖(空腹血糖)" 应命中
    assert match_indicator("空腹葡萄糖(空腹血糖)", ENTRIES) == "GLU"
    assert match_indicator("不存在的指标", ENTRIES) is None


def test_convert_value():
    v, u = convert_value(110.0, "mg/dL", GLU)
    assert abs(v - 6.105) < 0.01 and u == "mmol/L"
    v2, u2 = convert_value(6.1, "mmol/L", GLU)  # 已是标准单位
    assert v2 == 6.1 and u2 == "mmol/L"
    v3, u3 = convert_value(6.1, None, GLU)  # 无单位原样
    assert v3 == 6.1 and u3 is None
    v4, u4 = convert_value(110.0, "mg/dL", WBC)  # 无换算表
    assert v4 == 110.0 and u4 == "mg/dL"


def test_normalize_keeps_raw_value_for_report_range():
    # 数值 110 mg/dL 换算为 6.105 mmol/L,但报告区间 "70-110" 是 mg/dL 单位:
    # 判定必须用 raw_value_num 对报告区间比(见 Task 7),标准值只对 KG 区间比
    n = Normalizer(ENTRIES)
    raws = [RawReportItem(section="糖代谢", name="空腹血糖", value_text="110", value_num=110.0,
                          unit="mg/dL", ref_range_text="70-110", abnormal_flag=None)]
    out = asyncio.run(n.normalize(raws))
    assert abs(out[0].value_num - 6.105) < 0.01
    assert out[0].unit == "mmol/L"
    assert out[0].raw_value_num == 110.0
    assert out[0].raw_unit == "mg/dL"


def test_normalize_marks_unmapped_and_keeps_flow():
    n = Normalizer(ENTRIES)
    raws = [
        RawReportItem(section="糖代谢", name="空腹血糖", value_text="6.2", value_num=6.2,
                      unit="mmol/L", ref_range_text="3.9-6.1", abnormal_flag="↑"),
        RawReportItem(section="其他", name="神秘指标X", value_text="1", value_num=1.0,
                      unit="u", ref_range_text=None, abnormal_flag=None),
    ]
    out = asyncio.run(n.normalize(raws))  # llm=None:不做 LLM 映射
    assert out[0].indicator_code == "GLU"
    assert out[0].range_from == "report"
    assert out[1].indicator_code is None  # unmapped 保留继续
    assert out[1].name == "神秘指标X"


def test_normalize_llm_map_fallback_keeps_unknown():
    class FakeLLM:
        async def complete_json(self, messages, retry_feedback=True):
            raise LLMError("down")

    n = Normalizer(ENTRIES)
    raws = [RawReportItem(section=None, name="神秘指标X", value_text="1", value_num=1.0,
                          unit=None, ref_range_text=None, abnormal_flag=None)]
    out = asyncio.run(n.normalize(raws, llm=FakeLLM()))
    assert out[0].indicator_code is None  # LLM 挂 → 规则兜底 unknown
