"""eval/real/gt.json 静态校验(无 infra;评测升级 spec §7.2 标注口径)。"""
from pathlib import Path

from report_agent.eval.real_case import load_real_case

CATALOG_CODES = {"GLU", "HBA1C", "TC", "TG", "HDL_C", "LDL_C", "ALT", "AST", "GGT",
                 "ALP", "TBIL", "ALB", "CREA", "UREA", "UA", "WBC", "RBC", "HGB",
                 "PLT", "NEUT", "TSH", "FT3", "FT4", "UP"}
VALID_STATUSES = {"normal", "high", "low", "critical_high", "critical_low",
                  "unknown", "unmapped"}


def test_real_gt_json_sanity():
    backend = Path(__file__).resolve().parents[2]
    loaded = load_real_case(backend / "eval" / "real")
    assert loaded is not None, "缺 eval/real/r_real.md 或 gt.json"
    md, gt = loaded
    assert "受检者" in md and "舒俊杰" not in md, "脱敏副本含真实姓名"
    assert gt.meta["sex"] == "male" and gt.meta["age"] is None
    names = [i.name for i in gt.items]
    assert len(names) == len(set(names)), "gt name 重复"
    # 预期 76 项:dry-run(scripts/dry_run_real.py,2026-09-09)实测 [parsed] 77 项;failed_htmls 0 个
    # = draft 75 项 + 初步意见(心电图室/前列腺彩超两表各一条同名行,gt 名字唯一口径只承载一条,
    #   spec §7.3 重名解析项只对齐首个);详见 gt.json 初步意见项 note。
    assert len(names) == 76, f"预期 76 项(一般检查 5+眼科 4+血常规 23+尿常规 10+呼气 1+实验室 24+心电图 1+初步意见 1+彩超 5+前列腺 1+CT 1),实际 {len(names)}"
    for it in gt.items:
        assert it.name and it.name == it.name.strip(), "name 非法"
        assert it.status in VALID_STATUSES, f"{it.name}: 非法 status {it.status}"
        if it.code:
            assert it.code in CATALOG_CODES, f"{it.name}: 目录外 code {it.code}"
            assert it.status != "unmapped", f"{it.name}: coded 项 status 不应为 unmapped"
            assert it.value_num is not None or it.value_text, f"{it.name}: coded 项缺数值/文本"
            assert it.note, f"{it.name}: coded 项缺溯源说明"
        else:
            assert it.status == "unmapped", f"{it.name}: 无 code 项 status 应为 unmapped"
    by_name = {i.name: i for i in gt.items}
    # 真实报告中必须覆盖的已知异常项(name 为 dry-run 解析清洗形态,LaTeX 反斜杠保留)
    for n, code, status in [("丙氨酸氨基转移酶", "ALT", "high"),
                            ("\\gamma-谷氨酰转移酶", "GGT", "high"),
                            ("甘油三酯", "TG", "high")]:
        assert by_name[n].code == code and by_name[n].status == status, f"{n} 标注异常"
