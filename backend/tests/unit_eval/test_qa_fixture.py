"""QA fixture 镜像纯函数单测(无 infra;评测升级 spec §7.4)。"""
from report_agent.eval.real_case import RealCaseGT, RealGTItem
from report_agent.eval.runner import qa_fixture_gt, qa_fixture_gt3, qa_fixture_parts


def test_qa_fixture_gt3_keeps_only_coded_items():
    data = {
        "meta": {"sex": "male", "age": 45},
        "raw_items": [
            {"name": "空腹血糖", "value_text": "13.8", "value_num": 13.8,
             "unit": "mmol/L", "ref_range_text": "3.9~6.1", "abnormal_flag": "↑",
             "section": "检验"},
            {"name": "身高", "value_text": "170", "value_num": 170.0, "unit": "cm"},
        ],
        "gt_codes": {"空腹血糖": "GLU", "身高": None},
    }
    raws, gt3 = qa_fixture_gt3(data)
    assert [r.name for r in raws] == ["空腹血糖"]
    assert "section" not in raws[0].__dict__, "section 键已从 schema 删除,必须剥离"
    assert gt3[0].indicator_code == "GLU"


def test_qa_fixture_gt_real_case():
    gt = RealCaseGT(meta={"sex": "male", "age": None}, items=[
        RealGTItem(name="甘油三酯", code="TG", status="high", value_text="3.10",
                   value_num=3.1, unit="mmol/L", ref_range_text="0.45-1.81"),
        RealGTItem(name="身高", code=None, status="unmapped"),
    ])
    raws, gt3 = qa_fixture_gt(gt)
    assert [r.name for r in raws] == ["甘油三酯"]
    assert gt3[0].indicator_code == "TG" and gt3[0].range_from == "report"


def test_qa_fixture_parts_dispatch():
    gt = RealCaseGT(meta={"sex": "male", "age": None}, items=[
        RealGTItem(name="甘油三酯", code="TG", status="high", value_text="3.10",
                   value_num=3.1, unit="mmol/L", ref_range_text="0.45-1.81")])
    meta, raws, _gt3 = qa_fixture_parts({"real_gt": gt})
    assert meta.sex == "male" and meta.age is None
    assert raws[0].name == "甘油三酯"

    data = {"meta": {"sex": "female", "age": 30},
            "raw_items": [{"name": "空腹血糖", "value_text": "13.8", "value_num": 13.8,
                           "unit": "mmol/L", "ref_range_text": "3.9~6.1"}],
            "gt_codes": {"空腹血糖": "GLU"}}
    meta2, raws2, _ = qa_fixture_parts(data)
    assert meta2.sex == "female" and raws2[0].name == "空腹血糖"
