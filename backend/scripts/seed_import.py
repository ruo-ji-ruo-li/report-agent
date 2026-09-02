"""种子 YAML → Neo4j(MERGE 实体级)→ 派生文档 → 切块 → embedding → Milvus(按 entity 增量)。

运行:
  uv run python scripts/seed_import.py --all              # 全量(幂等,重复跑安全)
  uv run python scripts/seed_import.py --entity GLU       # 单个指标增量(验收演示)
先决条件: docker compose 服务已启动,指标 YAML 已经人工校对。
"""
import argparse
import asyncio
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SEEDS_DIR = ROOT / "knowledge_seeds" / "indicators"
PATTERNS_DIR = ROOT / "knowledge_seeds" / "patterns"


def upsert_indicator(session, seed) -> None:
    session.run(
        "MERGE (i:Indicator {code: $code}) SET i.name=$name, i.aliases=$aliases, i.unit=$unit, "
        "i.unit_conversions=$conv, i.category=$category, i.description=$description",
        code=seed.code, name=seed.name, aliases=seed.aliases, unit=seed.unit,
        conv=seed.unit_conversions, category=seed.category, description=seed.description,
    )
    # 出边收敛(I1):种子 YAML 删除/改名某项后重跑本实体,旧提示/簇/建议边全部移除再重建。
    # 只删 Indicator 的出边;Condition/Cluster/Intervention/Department 是共享节点,不删。
    session.run(
        "MATCH (i:Indicator {code: $code})"
        "-[r:HIGH_SUGGESTS|LOW_SUGGESTS|PART_OF|DEFAULT_INTERVENTION]->() DELETE r",
        code=seed.code,
    )
    session.run(
        "MATCH (i:Indicator {code: $code})-[r:HAS_RANGE]->(rs:RangeSpec) DETACH DELETE rs",
        code=seed.code,
    )
    for r in seed.ranges:
        session.run(
            "MATCH (i:Indicator {code: $code}) CREATE (i)-[:HAS_RANGE]->(:RangeSpec {"
            "sex: $sex, age_min: $amin, age_max: $amax, low: $low, high: $high, "
            "critical_low: $clo, critical_high: $chi, unit: $u, source_note: $note})",
            code=seed.code, sex=r.sex, amin=r.age_min, amax=r.age_max, low=r.low,
            high=r.high, clo=r.critical_low, chi=r.critical_high, u=r.unit, note=r.source_note,
        )
    for direction, rel in (("high", "HIGH_SUGGESTS"), ("low", "LOW_SUGGESTS")):
        for s in getattr(seed, f"{direction}_suggests"):
            session.run(
                f"MERGE (c:Condition {{name: $cond}}) ON CREATE SET c.description=$desc "
                f"WITH c MATCH (i:Indicator {{code: $code}}) "
                f"MERGE (i)-[r:{rel}]->(c) SET r.strength=$strength, r.note=$note",
                cond=s.condition, desc=s.description, code=seed.code, strength=s.strength, note=s.note,
            )
    for cl in seed.clusters:
        session.run(
            "MERGE (cl:IndicatorCluster {name: $name}) WITH cl "
            "MATCH (i:Indicator {code: $code}) MERGE (i)-[:PART_OF]->(cl)",
            name=cl, code=seed.code,
        )
    for iv in seed.interventions:
        key = f"{seed.code}|{iv.level}|{iv.text}"
        session.run(
            "MERGE (iv:Intervention {key: $key}) SET iv.level=$level, iv.text=$text, "
            "iv.timeframe=$tf WITH iv MATCH (i:Indicator {code: $code}) "
            "MERGE (i)-[:DEFAULT_INTERVENTION]->(iv)",
            key=key, level=iv.level, text=iv.text, tf=iv.timeframe, code=seed.code,
        )
        for d in iv.departments:
            session.run(
                "MERGE (dep:Department {name: $dep}) WITH dep "
                "MATCH (iv:Intervention {key: $key}) MERGE (iv)-[:REFER_TO]->(dep)",
                dep=d, key=key,
            )
    if seed.departments:
        _attach_departments(session, seed)
    # 孤儿清扫(I1):重跑后无主节点全局清扫(实体级重建已防新孤儿,此处兜底历史遗留)。
    # RangeSpec 无出边,plain DELETE 足够;Intervention 可带 REFER_TO 出边,
    # 必须 DETACH(plain DELETE 会在节点仍有关系时报错)。
    session.run("MATCH (rs:RangeSpec) WHERE NOT (rs)<--() DELETE rs")
    session.run("MATCH (iv:Intervention) WHERE NOT (iv)<--() DETACH DELETE iv")


def _attach_departments(session, seed) -> None:
    """指标级 departments: 并入首个 specialist 级建议;没有则创建合成 specialist 建议。"""
    specialist = next((iv for iv in seed.interventions if iv.level == "specialist"), None)
    key = (
        f"{seed.code}|specialist|{specialist.text}"
        if specialist
        else f"{seed.code}|specialist|建议专科门诊咨询"
    )
    if not specialist:
        session.run(
            "MERGE (iv:Intervention {key: $key}) SET iv.level='specialist', iv.text='建议专科门诊咨询' "
            "WITH iv MATCH (i:Indicator {code: $code}) MERGE (i)-[:DEFAULT_INTERVENTION]->(iv)",
            key=key, code=seed.code,
        )
    for d in seed.departments:
        session.run(
            "MERGE (dep:Department {name: $dep}) WITH dep "
            "MATCH (iv:Intervention {key: $key}) MERGE (iv)-[:REFER_TO]->(dep)",
            dep=d, key=key,
        )


def upsert_pattern(session, seed) -> None:
    session.run(
        "MERGE (p:Pattern {name: $name}) SET p.description=$desc", name=seed.name, desc=seed.description
    )
    session.run(
        "MATCH (p:Pattern {name: $name})-[r:REQUIRES]->(:Indicator) DELETE r", name=seed.name
    )
    for c in seed.criteria:
        session.run(
            "MATCH (p:Pattern {name: $name}), (i:Indicator {code: $code}) "
            "MERGE (p)-[:REQUIRES {direction: $dir}]->(i)",
            name=seed.name, code=c["indicator_code"], dir=c["direction"],
        )


async def import_entity(session, store, llms, settings, seed) -> None:
    from report_agent.knowledge.doc_builder import build_indicator_doc, chunk_documents

    await asyncio.to_thread(upsert_indicator, session, seed)
    doc = build_indicator_doc(seed)
    chunks = chunk_documents([doc], settings)
    texts = [c.text for c in chunks]
    embs = []
    for i in range(0, len(texts), 100):
        embs.extend(await llms.embedding.embed_texts(texts[i:i + 100]))
    await asyncio.to_thread(store.delete_entity, "indicator", seed.code)
    await asyncio.to_thread(store.upsert_chunks, chunks, embs)
    print(f"[done] indicator {seed.code}: {len(chunks)} chunks")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--entity", nargs="*")
    args = parser.parse_args()

    from neo4j import GraphDatabase

    from report_agent.config import get_settings
    from report_agent.knowledge.doc_builder import build_condition_docs, chunk_documents
    from report_agent.knowledge.milvus_client import MilvusStore
    from report_agent.knowledge.seed_schemas import IndicatorSeed, PatternSeed
    from report_agent.llm.client import llm_factory

    s = get_settings()
    llms = await llm_factory.get()
    store = MilvusStore(uri=s.milvus_uri, collection=s.milvus_collection, embedding_dim=s.embedding_dim)
    await asyncio.to_thread(store.ensure_collection)

    driver = GraphDatabase.driver(s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password))
    seeds: list[IndicatorSeed] = []
    try:
        for path in sorted(SEEDS_DIR.glob("*.yaml")):
            code = path.stem
            if not args.all and code not in (args.entity or []):
                continue
            seed = IndicatorSeed.model_validate(yaml.safe_load(path.read_text("utf-8")))
            with driver.session(database=s.neo4j_database) as session:
                await import_entity(session, store, llms, s, seed)
            seeds.append(seed)

        for path in sorted(PATTERNS_DIR.glob("*.yaml")):
            pseed = PatternSeed.model_validate(yaml.safe_load(path.read_text("utf-8")))
            with driver.session(database=s.neo4j_database) as session:
                await asyncio.to_thread(upsert_pattern, session, pseed)
            print(f"[done] pattern {pseed.name}")

        if args.all:
            cond_docs = build_condition_docs(seeds)
            cond_chunks = chunk_documents(cond_docs, s)
            texts = [c.text for c in cond_chunks]
            embs = []
            for i in range(0, len(texts), 100):
                embs.extend(await llms.embedding.embed_texts(texts[i:i + 100]))
            await asyncio.to_thread(store.upsert_chunks, cond_chunks, embs)
            print(f"[done] condition docs: {len(cond_chunks)} chunks")
    finally:
        driver.close()


if __name__ == "__main__":
    asyncio.run(main())
