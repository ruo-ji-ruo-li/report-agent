"""Milvus 存储客户端:dense(HNSW)+ sparse(BM25/jieba)双索引,单 collection。

所有方法同步;调用方用 asyncio.to_thread 包装。稀疏向量由 text 字段上的
BM25 Function 自动生成,应用层只维护 text(spec §4.3)。
"""
from dataclasses import dataclass

from pymilvus import DataType, Function, FunctionType, MilvusClient

from report_agent.observability import get_logger

log = get_logger(__name__)


@dataclass
class KnowledgeChunk:
    chunk_id: str
    text: str
    entity_type: str  # indicator/condition/cluster/pattern
    entity_id: str    # 指向 KG 节点(code/name),增量删除键
    title: str
    section_title: str | None
    chunk_index: int
    total_chunks: int
    parent_id: str


@dataclass
class ScoredChunk:
    chunk: KnowledgeChunk
    score: float
    search_method: str  # dense / fulltext


_OUTPUT_FIELDS = [
    "chunk_id", "text", "entity_type", "entity_id", "title",
    "section_title", "chunk_index", "total_chunks", "parent_id",
]


def check_milvus(uri: str) -> bool:
    client = MilvusClient(uri=uri, timeout=3)
    client.list_collections()
    return True


class MilvusStore:
    def __init__(self, uri: str, collection: str, embedding_dim: int):
        self._client = MilvusClient(uri=uri)
        self.collection = collection
        self.dim = embedding_dim

    def ensure_collection(self) -> None:
        """幂等创建 collection + 双索引(验证项 1 的核心代码)。"""
        if self._client.has_collection(self.collection):
            return
        schema = self._client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field(
            "text", DataType.VARCHAR, max_length=65535,
            enable_analyzer=True, analyzer_params={"tokenizer": "jieba"},
        )
        schema.add_field("sparse_vec", DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field("dense_vec", DataType.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("entity_type", DataType.VARCHAR, max_length=64)
        schema.add_field("entity_id", DataType.VARCHAR, max_length=128)
        schema.add_field("title", DataType.VARCHAR, max_length=256)
        schema.add_field("section_title", DataType.VARCHAR, max_length=256)
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("total_chunks", DataType.INT64)
        schema.add_field("parent_id", DataType.VARCHAR, max_length=128)
        bm25 = Function(
            name="bm25_fn", function_type=FunctionType.BM25,
            input_field_names=["text"], output_field_names="sparse_vec",
        )
        schema.add_function(bm25)
        self._client.create_collection(self.collection, schema=schema)

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vec", index_type="HNSW", metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        index_params.add_index(
            # BM25 Function 输出字段的 metric 服务端强制为 BM25(真实 2.5.14 校验,IP 被拒 1100)
            field_name="sparse_vec", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25",
        )
        self._client.create_index(self.collection, index_params=index_params)
        self._client.load_collection(self.collection)
        log.info("milvus_collection_ready", collection=self.collection)

    def upsert_chunks(self, chunks: list[KnowledgeChunk], dense_embeddings: list[list[float]]) -> None:
        rows = [
            {
                "chunk_id": c.chunk_id,
                "text": c.text,
                "dense_vec": emb,
                "entity_type": c.entity_type,
                "entity_id": c.entity_id,
                "title": c.title,
                # Milvus 2.5 VARCHAR 字段不接受 None(nil),空值落空串(读侧 get 兼容)
                "section_title": c.section_title or "",
                "chunk_index": c.chunk_index,
                "total_chunks": c.total_chunks,
                "parent_id": c.parent_id,
            }
            for c, emb in zip(chunks, dense_embeddings, strict=True)
        ]
        self._client.insert(self.collection, rows)
        # flush 保证数据持久化并对 search 立即可见(Bounded 一致性下未 flush 的新数据检索不到,
        # 真实 2.5.14 验证确认);增量更新后立即可查是验收标准 5 的必要语义。
        self._client.flush(self.collection)
        log.info("milvus_upsert", count=len(rows))

    def delete_entity(self, entity_type: str, entity_id: str) -> None:
        for v in (entity_type, entity_id):
            assert "'" not in v, f"非法 entity 标识(含单引号): {v}"
        self._client.delete(
            self.collection, filter=f"entity_type == '{entity_type}' and entity_id == '{entity_id}'"
        )
        self._client.flush(self.collection)

    def search_dense(self, embedding: list[float], top_k: int) -> list[ScoredChunk]:
        res = self._client.search(
            collection_name=self.collection, data=[embedding], anns_field="dense_vec",
            limit=top_k, output_fields=_OUTPUT_FIELDS,
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
        )
        return [self._to_scored(hit, "dense") for hit in (res[0] if res else [])]

    def search_fulltext(self, query_text: str, top_k: int) -> list[ScoredChunk]:
        res = self._client.search(
            collection_name=self.collection, data=[query_text], anns_field="sparse_vec",
            limit=top_k, output_fields=_OUTPUT_FIELDS,
            search_params={"params": {"drop_ratio_search": 0.2}},
        )
        return [self._to_scored(hit, "fulltext") for hit in (res[0] if res else [])]

    def query_chunks_by_entity(self, entity_type: str, entity_id: str) -> list[KnowledgeChunk]:
        """评测与调试用:取某实体的全部 chunk。"""
        rows = self._client.query(
            collection_name=self.collection,
            filter=f"entity_type == '{entity_type}' and entity_id == '{entity_id}'",
            output_fields=_OUTPUT_FIELDS, limit=1000,
        )
        return [self._row_to_chunk(r) for r in rows]

    def _to_scored(self, hit: dict, method: str) -> ScoredChunk:
        return ScoredChunk(
            chunk=self._row_to_chunk(hit.get("entity", {})), score=hit.get("distance", 0.0),
            search_method=method,
        )

    @staticmethod
    def _row_to_chunk(r: dict) -> KnowledgeChunk:
        return KnowledgeChunk(
            chunk_id=r["chunk_id"], text=r["text"], entity_type=r["entity_type"],
            entity_id=r["entity_id"], title=r["title"], section_title=r.get("section_title"),
            chunk_index=r["chunk_index"], total_chunks=r["total_chunks"], parent_id=r["parent_id"],
        )
