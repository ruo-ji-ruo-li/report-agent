"""LLM 起草种子 YAML(断点续传)。运行:
  uv run python scripts/seed_draft.py --all          # 起草清单中缺失的全部
  uv run python scripts/seed_draft.py --force GLU    # 强制重起草单个
产物: knowledge_seeds/indicators/{CODE}.yaml(人工校对后由 seed_import 入库)。
"""
import argparse
import asyncio
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEEDS_DIR = ROOT / "knowledge_seeds" / "indicators"
PROGRESS = ROOT / "knowledge_seeds" / ".draft_progress.json"


async def draft_one(name: str, code: str, category: str, aliases: list[str]) -> dict:
    from report_agent.llm.client import llm_factory
    from report_agent.llm.prompts import load_prompt

    clients = await llm_factory.get()
    prompt = (
        load_prompt("seed_draft")
        .replace("{{name}}", name)
        .replace("{{code}}", code)
        .replace("{{category}}", category or "")
        .replace("{{aliases}}", json.dumps(aliases, ensure_ascii=False))
    )
    data = await clients.chat.complete_json([{"role": "user", "content": prompt}])
    if data.get("code") != code:
        data["code"] = code
    return data


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("codes", nargs="*")
    args = parser.parse_args()

    catalog = yaml.safe_load((ROOT / "knowledge_seeds" / "indicator_list.yaml").read_text("utf-8"))
    progress = json.loads(PROGRESS.read_text("utf-8")) if PROGRESS.exists() else {}
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    for item in catalog:
        code = item["code"]
        target = SEEDS_DIR / f"{code}.yaml"
        if not args.all and code not in args.codes:
            continue
        if target.exists() and not args.force:
            print(f"[skip] {code} 已存在,人工校对后重跑 --force 覆盖")
            continue
        data = await draft_one(item["name"], code, item.get("category"), item.get("aliases") or [])
        target.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), "utf-8")
        progress[code] = "drafted"
        PROGRESS.write_text(json.dumps(progress, ensure_ascii=False, indent=2), "utf-8")
        print(f"[done] {code} -> {target.name}")


if __name__ == "__main__":
    asyncio.run(main())
