from report_agent.knowledge.kg_client import PatternCriterion, PatternSpec, RangeSpec
from report_agent.parsing.schemas import NormalizedItem, ReportMeta
from report_agent.pipeline.rule_compare import (
    ItemJudgment,
    ItemStatus,
    is_critical,
    judge_all,
    judge_item,
    match_patterns,
    parse_range_text,
    select_range,
)

GLU_SPEC = RangeSpec(sex="any", age_min=18, age_max=100, low=3.9, high=6.1,
                     critical_low=2.8, critical_high=22.0, unit="mmol/L", source_note=None)
FEMALE_ONLY = RangeSpec(sex="female", age_min=18, age_max=60, low=4.0, high=6.0,
                        critical_low=None, critical_high=None, unit="mmol/L", source_note=None)
CHILD = RangeSpec(sex="any", age_min=0, age_max=18, low=3.5, high=5.5,
                  critical_low=None, critical_high=None, unit="mmol/L", source_note=None)


def _item(**kw) -> NormalizedItem:
    base = {"raw_index": 0, "section": None, "name": "空腹血糖", "indicator_code": "GLU",
            "value_text": None, "value_num": 5.0, "unit": "mmol/L", "raw_value_num": 5.0,
            "raw_unit": "mmol/L", "ref_range_text": None, "range_from": None}
    base.update(kw)
    return NormalizedItem(**base)


# ---------- parse_range_text ----------
def test_parse_range_interval_and_tilde():
    assert parse_range_text("3.5-9.5") == (3.5, 9.5)
    assert parse_range_text("3.5~9.5") == (3.5, 9.5)
    assert parse_range_text("3.5-9.5×10^9/L") == (3.5, 9.5)  # 带单位
    assert parse_range_text("0-10") == (0.0, 10.0)


def test_parse_range_directional():
    assert parse_range_text("<5.0") == (None, 5.0)
    assert parse_range_text(">1.0") == (1.0, None)
    assert parse_range_text("≤2.0") == (None, 2.0)


def test_parse_range_garbage():
    assert parse_range_text("阴性") == (None, None)
    assert parse_range_text(None) == (None, None)
    assert parse_range_text("") == (None, None)


# ---------- select_range ----------
def test_select_range_prefers_sex_exact_and_narrowest():
    specs = [GLU_SPEC, FEMALE_ONLY, CHILD]
    assert select_range(specs, "female", 40) == FEMALE_ONLY  # sex 精确优先
    assert select_range(specs, "male", 40) == GLU_SPEC  # any 兜底
    assert select_range(specs, "male", 10) == CHILD  # 年龄窗口
    assert select_range(specs, "female", 70) == GLU_SPEC  # 女 70 超出 FEMALE_ONLY 窗口 → any
    assert select_range(specs, None, 40) == GLU_SPEC  # sex None → 只用 any
    assert select_range(specs, "male", 200) is None  # 无匹配


def test_select_range_empty_and_age_none():
    assert select_range([], "male", 40) is None
    assert select_range([GLU_SPEC], "male", None) == GLU_SPEC  # age None 跳过年龄过滤


# ---------- judge_item ----------
def test_judge_unmapped():
    j = judge_item(_item(indicator_code=None), GLU_SPEC)
    assert j.status == ItemStatus.UNMAPPED


def test_judge_qualitative():
    assert judge_item(_item(value_num=None, value_text="阴性", raw_value_num=None),
                      GLU_SPEC).status == ItemStatus.NORMAL
    assert judge_item(_item(value_num=None, value_text="阳性", raw_value_num=None),
                      GLU_SPEC).status == ItemStatus.HIGH
    assert judge_item(_item(value_num=None, value_text="弱阳性", raw_value_num=None),
                      GLU_SPEC).status == ItemStatus.UNKNOWN


def test_judge_report_range_first():
    # raw 110 mg/dL(≈6.1 mmol/L,Task 6 已换算):报告区间在原单位域对 raw 比 → NORMAL;
    # 标准值 6.1 不触碰 KG 危急阈值(2.8/22 mmol/L),否则按单位语义必报危急。
    j = judge_item(_item(value_num=6.1, raw_value_num=110.0, raw_unit="mg/dL",
                         ref_range_text="70-110"), GLU_SPEC)
    assert j.status == ItemStatus.NORMAL and j.range_source == "report"


def test_judge_kg_range_fallback_and_bounds():
    j = judge_item(_item(value_num=7.0, raw_value_num=7.0), GLU_SPEC)
    assert j.status == ItemStatus.HIGH and j.range_source == "kg"
    assert (j.ref_low, j.ref_high) == (3.9, 6.1)
    # 3.0 低于 kg low(3.9)但高于 critical_low(2.8)→ LOW;2.0 ≤ critical_low 应为 CRITICAL_LOW,
    # 归 test_judge_critical_beats_report_range 覆盖,此处不混入。
    j2 = judge_item(_item(value_num=3.0, raw_value_num=3.0), GLU_SPEC)
    assert j2.status == ItemStatus.LOW


def test_judge_critical_beats_report_range():
    # 危急值永远来自 KG:即使报告区间内正常,KG critical 也触发
    j = judge_item(_item(value_num=23.0, raw_value_num=23.0, ref_range_text="20-25"), GLU_SPEC)
    assert j.status == ItemStatus.CRITICAL_HIGH and j.critical
    j2 = judge_item(_item(value_num=1.5, raw_value_num=1.5, ref_range_text="1.0-25"), GLU_SPEC)
    assert j2.status == ItemStatus.CRITICAL_LOW and j2.critical


def test_judge_no_bounds_unknown():
    spec = RangeSpec(sex="any", age_min=0, age_max=150, low=None, high=None,
                     critical_low=None, critical_high=None, unit=None, source_note=None)
    assert judge_item(_item(value_num=5.0, raw_value_num=5.0), spec).status == ItemStatus.UNKNOWN


# ---------- judge_all / patterns ----------
def test_judge_all_and_match_patterns_with_direction():
    items = [
        # TG 3.0 > 报告高限 1.7 → HIGH;又高于 GLU_SPEC.critical_low(2.8),
        # 2.5 会先被判 CRITICAL_LOW 而破坏 high 方向,故取 3.0。
        _item(indicator_code="TG", value_num=3.0, raw_value_num=3.0, ref_range_text="0.4-1.7"),
        # HDL_C 0.8 ≤ critical_low → CRITICAL_LOW,方向 low 仍命中(危急计入方向)。
        _item(indicator_code="HDL_C", value_num=0.8, raw_value_num=0.8, ref_range_text="1.0-2.0"),
        _item(indicator_code="GLU", value_num=6.5, raw_value_num=6.5, ref_range_text="3.9-6.1"),
    ]
    meta = ReportMeta(sex="male", age=45)
    specs = {"TG": [GLU_SPEC], "HDL_C": [GLU_SPEC], "GLU": [GLU_SPEC]}
    judgments = judge_all(items, specs, meta)
    pattern = PatternSpec(name="代谢综合征", description="d", criteria=[
        PatternCriterion(indicator_code="TG", direction="high"),
        PatternCriterion(indicator_code="HDL_C", direction="low"),
        PatternCriterion(indicator_code="GLU", direction="high"),
    ])
    matched = match_patterns(judgments, [pattern])
    assert len(matched) == 1 and matched[0][0].name == "代谢综合征"


def test_match_patterns_direction_mismatch_and_partial():
    j_high_tg = ItemJudgment(indicator_code="TG", name="甘油三酯", value_num=2.5, value_text=None,
                             unit="mmol/L", status=ItemStatus.HIGH, ref_low=None, ref_high=None,
                             critical=False, range_source="report")
    j_low_hdl = ItemJudgment(indicator_code="HDL_C", name="高密度脂蛋白", value_num=0.8,
                             value_text=None, unit="mmol/L", status=ItemStatus.LOW, ref_low=None,
                             ref_high=None, critical=False, range_source="report")
    j_crit = ItemJudgment(indicator_code="GLU", name="空腹血糖", value_num=25.0, value_text=None,
                          unit="mmol/L", status=ItemStatus.CRITICAL_HIGH, ref_low=None,
                          ref_high=None, critical=True, range_source="kg")
    pattern = PatternSpec(name="P", description="d", criteria=[
        PatternCriterion(indicator_code="TG", direction="low"),  # 方向不对 → 不匹配
    ])
    assert match_patterns([j_high_tg], [pattern]) == []
    # 危急值计入方向(CRITICAL_HIGH 算 high)
    p2 = PatternSpec(name="P2", description="d", criteria=[
        PatternCriterion(indicator_code="TG", direction="high"),
        PatternCriterion(indicator_code="HDL_C", direction="low"),
        PatternCriterion(indicator_code="GLU", direction="high"),
    ])
    assert len(match_patterns([j_high_tg, j_low_hdl, j_crit], [p2])) == 1
    assert not is_critical(j_high_tg) and is_critical(j_crit)


# ---------- 补测:分支全覆盖 ----------
def test_parse_range_marker_without_digits_and_broken_interval():
    # 方向标记但无数字(if not m → (None, None)):线 52
    assert parse_range_text("<") == (None, None)
    # 切出两段但任一侧无数值(m1 and m2 失败 → (None, None)):线 58→61
    assert parse_range_text("a-b") == (None, None)


def test_judge_spec_none_report_fallback_and_unknown():
    # spec=None + 报告区间 → 报告边界比较,跳过 KG 危急值段(线 119 False 弧)
    j = judge_item(_item(value_num=7.0, raw_value_num=7.0, ref_range_text="3.9-6.1"), None)
    assert j.status == ItemStatus.HIGH and j.range_source == "report" and not j.critical
    # spec=None 且无报告区间 → 无界 UNKNOWN,range_source 保持 None
    j2 = judge_item(_item(value_num=7.0, raw_value_num=7.0), None)
    assert j2.status == ItemStatus.UNKNOWN and j2.range_source is None


def test_select_range_narrowest_window_overlap():
    wide = RangeSpec(sex="any", age_min=0, age_max=100, low=None, high=None,
                     critical_low=None, critical_high=None, unit=None, source_note=None)
    narrow = RangeSpec(sex="any", age_min=20, age_max=50, low=None, high=None,
                       critical_low=None, critical_high=None, unit=None, source_note=None)
    # 两条 any 同时命中 30 岁 → 取最窄年龄窗(min 在多候选上真正生效)
    assert select_range([wide, narrow], None, 30) == narrow
