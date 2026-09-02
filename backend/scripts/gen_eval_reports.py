"""LLM 合成评测报告集(人工校对后冻结基线)。运行:
  uv run python scripts/gen_eval_reports.py --count 20
产物: eval/reports/r01.json ... rNN.json。评测 JSON 结构:
{
  "id": "r01",
  "meta": {"sex": "male|female", "age": 45},
  "raw_items": [{name, value_text, value_num, unit, ref_range_text, abnormal_flag, section}],
  "gt_codes": {"报告项目名": "标准code"},
  "gt_statuses": {"标准code": "normal|high|low|critical_high|critical_low|unknown|unmapped"}
}
"""
import argparse
import asyncio
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "eval" / "reports"

TEMPLATE = """你是体检报告数据合成器。基于以下真实指标目录,合成一份虚构体检报告的部分检验结果,
用于评测一个报告解读系统。直接输出 JSON,不要解释。

指标目录(每行: code|标准名|单位|成人参考区间):
{catalog}

合成要求:
1. 性别随机 male/female,年龄 25~65。
2. 8~15 个检验项;其中 40%~70% 异常(偏高/偏低/危急值各有一些),保留 1~2 个正常项,
   可包含 1 个目录外项目(unknown)与 1 个别名变体(如"谷丙转氨酶"代替标准名)。
3. 参考区间优先给报告口径区间;危急值数值要显著越过区间。
4. gt_codes: 每个项目名对应的标准 code;目录外项目映射为 null 并在 gt_statuses 标 unmapped。
5. gt_statuses 按给定区间严格判定: normal/high/low/critical_high/critical_low/unknown/unmapped。

输出 JSON 结构:
{{"id": "{rid}", "meta": {{"sex": "...", "age": 45}},
  "raw_items": [{{"name": "...", "value_text": "...", "value_num": 6.2, "unit": "...",
                  "ref_range_text": "...", "abnormal_flag": "↑或null", "section": "..."}}],
  "gt_codes": {{"项目名": "code或null"}},
  "gt_statuses": {{"code": "status"}}}}
"""


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()

    import yaml

    from report_agent.llm.client import llm_factory

    catalog = yaml.safe_load((ROOT / "knowledge_seeds" / "indicator_list.yaml").read_text("utf-8"))
    catalog_text = "\n".join(
        f"{c['code']}|{c['name']}|{'、'.join(c.get('aliases') or [])}" for c in catalog
    )
    clients = await llm_factory.get()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(1, args.count + 1):
        rid = f"r{i:02d}"
        out = OUT_DIR / f"{rid}.json"
        if out.exists():
            print(f"[skip] {rid}")
            continue
        data = await clients.chat.complete_json(
            [{"role": "user", "content": TEMPLATE.format(catalog=catalog_text, rid=rid)}]
        )
        out.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        print(f"[done] {rid}")


if __name__ == "__main__":
    asyncio.run(main())
