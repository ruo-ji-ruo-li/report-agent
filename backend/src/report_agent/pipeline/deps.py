"""管线依赖容器:Task 14 在 app 启动时 build 一次,注入 runner 与各阶段。"""
from dataclasses import dataclass

from report_agent.knowledge.kg_client import KGClient
from report_agent.knowledge.milvus_client import MilvusStore
from report_agent.parsing.normalizer import Normalizer
from report_agent.retrieval.hybrid import HybridRetriever


@dataclass
class PipelineDeps:
    settings: object
    llms: object
    kg: KGClient
    store: MilvusStore
    retriever: HybridRetriever
    normalizer: Normalizer
    session_factory: object
    db: object  # DataAccess —— API/追问/评测共用同一数据访问层


async def build_deps(settings) -> PipelineDeps:
    from report_agent.llm.client import llm_factory

    llms = await llm_factory.get()
    kg = KGClient(uri=settings.neo4j_uri, user=settings.neo4j_user,
                  password=settings.neo4j_password, database=settings.neo4j_database)
    store = MilvusStore(uri=settings.milvus_uri, collection=settings.milvus_collection,
                        embedding_dim=settings.embedding_dim)
    import asyncio

    await asyncio.to_thread(store.ensure_collection)
    entries = await asyncio.to_thread(kg.indicator_catalog)
    normalizer = Normalizer(entries)
    retriever = HybridRetriever(milvus=store, kg=kg, embedder=llms.embedding, settings=settings)
    from report_agent.db.session import session_factory
    from report_agent.pipeline.db_access import DataAccess

    return PipelineDeps(settings=settings, llms=llms, kg=kg, store=store, retriever=retriever,
                        normalizer=normalizer, session_factory=session_factory,
                        db=DataAccess(session_factory))
