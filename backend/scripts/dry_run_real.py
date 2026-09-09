"""真实 case 解析/归一化/判定对照表(评测升级 spec §10 V2/V3;gt 标注校标用)。
运行: uv run python scripts/dry_run_real.py
输出: 每项 name / code 列 / 解析值 / 归一化 code / 规则判定 / failed 表数量。
需要 docker compose infra(Neo4j 指标目录;Milvus 连接失败仅告警不影响本脚本)。
"""
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def main() -> None:
    from report_agent.config import get_settings
    from report_agent.parsing.schemas import ReportMeta
    from report_agent.parsing.table_extractor import parse_merged_tables
    from report_agent.pipeline.deps import build_deps
    from report_agent.pipeline.rule_compare import judge_all

    settings = get_settings()
    deps = await build_deps(settings)
    merged = (ROOT / "eval" / "real" / "r_real.md").read_text("utf-8")
    raws, failed = parse_merged_tables(merged)
    norms = await deps.normalizer.normalize(raws)
    specs: dict[str, list] = {}
    for n in norms:
        if n.indicator_code and n.indicator_code not in specs:
            specs[n.indicator_code] = deps.kg.range_specs(n.indicator_code)
    judgments = judge_all(norms, specs, ReportMeta(sex="male", age=None))
    print(f"[parsed] {len(raws)} 项;failed_htmls {len(failed)} 个")
    for raw, n, j in zip(raws, norms, judgments, strict=True):
        print(json.dumps({
            "name": raw.name, "code_col": raw.code, "value_text": raw.value_text,
            "value_num": raw.value_num, "unit": raw.unit, "ref": raw.ref_range_text,
            "flag": raw.abnormal_flag, "norm_code": n.indicator_code,
            "status": j.status.value,
        }, ensure_ascii=False))
    if failed:
        print("[failed_htmls]")
        for h in failed:
            print(h[:200].replace("\n", " "))


if __name__ == "__main__":
    asyncio.run(main())
