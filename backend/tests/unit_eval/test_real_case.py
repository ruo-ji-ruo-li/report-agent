"""real_case 纯函数单测(无 infra;评测升级 spec §7.2/§7.3)。"""
import json

from report_agent.eval.real_case import (
    AlignResult,
    RealCaseGT,
    RealGTItem,
    align_parsed,
    compute_parse_f1,
    gt_statuses,
    gt_to_normalized,
    load_real_case,
    normalized_pair_codes,
)
from report_agent.parsing.schemas import NormalizedItem, RawReportItem


def _mk_parsed(name, value_text="52", value_num=52.0):
    return RawReportItem(name=name, value_text=value_text, value_num=value_num)


def _mk_gt(name, code=None, status="unmapped"):
    return RealGTItem(name=name, code=code, status=status)


def test_load_real_case_missing_dir_returns_none(tmp_path):
    assert load_real_case(tmp_path) is None


def test_load_real_case_parses_md_and_gt(tmp_path):
    (tmp_path / "r_real.md").write_text("# 表", encoding="utf-8")
    (tmp_path / "gt.json").write_text(json.dumps(
        {"meta": {"sex": "male", "age": None},
         "items": [{"name": "ALT", "code": "ALT", "status": "high"}]},
        ensure_ascii=False), encoding="utf-8")
    md, gt = load_real_case(tmp_path)
    assert md == "# 表"
    assert gt.meta["sex"] == "male" and gt.meta["age"] is None
    assert gt.items[0].code == "ALT" and gt.items[0].status == "high"


def test_align_parsed_matches_by_name_and_lists_unmatched():
    parsed = [_mk_parsed("ALT"), _mk_parsed("身高"), _mk_parsed("ALT")]
    gt = [_mk_gt("ALT", "ALT", "high"), _mk_gt("体重")]
    a = align_parsed(parsed, gt)
    assert len(a.pairs) == 1 and a.pairs[0][1].status == "high"
    assert [p.name for p in a.unmatched_parsed] == ["身高", "ALT"]
    assert [g.name for g in a.unmatched_gt] == ["体重"]


def test_parse_f1_formula():
    a = AlignResult(pairs=[(None, None)] * 4,
                    unmatched_parsed=[None] * 2, unmatched_gt=[None] * 1)
    # p = 4/6, r = 4/5 → f1 = 2*0.667*0.8/1.467 ≈ 0.727
    assert 0.72 < compute_parse_f1(a) < 0.73
    assert compute_parse_f1(AlignResult()) == 0.0


def test_gt_to_normalized_and_statuses():
    gt = RealCaseGT(meta={"sex": "male", "age": None}, items=[
        _mk_gt("ALT", "ALT", "high"), _mk_gt("身高", None, "unmapped")])
    norms = gt_to_normalized(gt)
    assert norms[0].indicator_code == "ALT" and norms[0].range_from == "report"
    assert norms[1].indicator_code is None
    assert gt_statuses(gt) == {"ALT": "high"}


def test_normalized_pair_codes_aligns_codes():
    parsed = [_mk_parsed("ALT"), _mk_parsed("身高")]
    gt_items = [_mk_gt("ALT", "ALT", "high")]
    align = align_parsed(parsed, gt_items)
    norm_by_name = {"ALT": NormalizedItem(raw_index=0, name="ALT", indicator_code="ALT",
                                          value_text="52", value_num=52.0, unit="U/L",
                                          raw_value_num=52.0, raw_unit="U/L",
                                          ref_range_text="9—50", range_from="report")}
    pred, gt_codes = normalized_pair_codes(align, norm_by_name)
    assert pred == ["ALT"] and gt_codes == ["ALT"]


def test_llm_case_specs_includes_real_case(tmp_path):
    """LLM 维度 case 清单:合成前 N 份 + 真实 case(spec §4:真实 case 恒定纳入)。"""
    import json as _json

    from report_agent.eval.runner import llm_case_specs
    from report_agent.parsing.schemas import ReportMeta

    r01 = tmp_path / "r01.json"
    r01.write_text(_json.dumps({
        "meta": {"sex": "male", "age": 45},
        "raw_items": [{"name": "空腹血糖", "value_text": "13.8", "value_num": 13.8,
                       "unit": "mmol/L", "ref_range_text": "3.9~6.1"}],
        "gt_codes": {"空腹血糖": "GLU"},
        "gt_statuses": {"GLU": "high"},
    }, ensure_ascii=False), encoding="utf-8")
    real = (r01.read_text(encoding="utf-8"), RealCaseGT(
        meta={"sex": "male", "age": None},
        items=[_mk_gt("甘油三酯", "TG", "high")]))
    specs = llm_case_specs([r01], 5, real)
    assert [s.case_id for s in specs] == ["r01", "r_real"]
    assert specs[1].meta == ReportMeta(sex="male", age=None)
    assert specs[1].gt_items[0].indicator_code == "TG"


def test_allowed_numbers_from_gt_items():
    from report_agent.eval.runner import allowed_numbers_from
    from report_agent.parsing.schemas import NormalizedItem, ReportMeta

    items = [NormalizedItem(raw_index=0, name="ALT", indicator_code="ALT",
                            value_text="52", value_num=52.0, unit="U/L",
                            raw_value_num=52.0, raw_unit="U/L",
                            ref_range_text="9—50", range_from="report")]
    allowed = allowed_numbers_from(items, ReportMeta(sex="male", age=40))
    assert allowed == [52.0, 9.0, 50.0, 40.0]
