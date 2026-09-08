"""MilvusStore 单测:fake MilvusClient 记录调用,验证 schema/索引/搜索参数。"""
from report_agent.knowledge.milvus_client import KnowledgeChunk, MilvusStore


class FakeMilvus:
    def __init__(self, **kw):
        self.collections = set()
        self.rows: list[dict] = []
        self.deleted_filters: list[str] = []
        self.searches: list[dict] = []

    def has_collection(self, name):
        return name in self.collections

    # 偏离(brief 原文缺此方法):store 通过 MilvusClient.create_schema 构建 schema,
    # 真实 pymilvus 有该方法;此处加最小录制桩,仅记录字段名。
    def create_schema(self, **kw):
        class _Field:
            def __init__(self, name):
                self.name = name

        class _Schema:
            def __init__(self):
                self.fields = []

            def add_field(self, name, dtype, **kw):
                self.fields.append(_Field(name))

            def add_function(self, *a, **kw):
                pass

        return _Schema()

    def create_collection(self, collection_name, schema=None, **kw):
        self.collections.add(collection_name)
        self.schema = schema

    def prepare_index_params(self):
        class P:
            def __init__(self):
                self.entries = []

            def add_index(self, **kw):
                self.entries.append(kw)

        return P()

    def create_index(self, name, index_params, **kw):
        self.index_entries = index_params.entries

    def load_collection(self, name):
        pass

    def insert(self, collection_name, data):
        self.rows.extend(data)
        self.flushed = False

    def flush(self, collection_name):
        self.flushed = True

    def delete(self, collection_name, filter):
        self.deleted_filters.append(filter)

    def search(self, collection_name, data, anns_field, limit, output_fields, search_params):
        self.searches.append(
            {
                "collection_name": collection_name, "data": data, "anns_field": anns_field,
                "limit": limit, "output_fields": output_fields, "search_params": search_params,
            }
        )
        hit = {"id": "c1", "distance": 0.9, "entity": {
            "chunk_id": "c1", "text": "空腹血糖相关释义", "entity_type": "indicator",
            "entity_id": "GLU", "title": "空腹血糖", "section_title": "指标说明",
            "chunk_index": 0, "total_chunks": 1, "parent_id": "indicator:GLU",
        }}
        return [[hit]]


def _chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id="c1", text="空腹血糖相关释义", entity_type="indicator", entity_id="GLU",
        title="空腹血糖", section_title="指标说明", chunk_index=0, total_chunks=1,
        parent_id="indicator:GLU",
    )


def test_ensure_collection_creates_dense_and_sparse_index(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    store.ensure_collection()
    fields = {f.name for f in fake.schema.fields}
    assert {"dense_vec", "sparse_vec", "text"} <= fields
    assert fake.index_entries  # 有索引定义
    types = {e["index_type"] for e in fake.index_entries}
    assert {"HNSW", "SPARSE_INVERTED_INDEX"} <= types
    # BM25 Function 输出字段的 metric 服务端强制为 BM25(真实 2.5.14 校验,IP 被拒)
    sparse_metric = {e["metric_type"] for e in fake.index_entries if e["field_name"] == "sparse_vec"}
    assert sparse_metric == {"BM25"}


def test_ensure_collection_idempotent(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    fake.collections.add("kb")
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    store.ensure_collection()  # 已存在 → 不报错不重建
    assert fake.collections == {"kb"}


def test_upsert_and_delete_entity(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    fake.collections.add("kb")
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    store.upsert_chunks([_chunk()], dense_embeddings=[[0.1] * 1024])
    assert fake.rows[0]["chunk_id"] == "c1"
    assert "sparse_vec" not in fake.rows[0]  # 稀疏向量由 BM25 Function 自动生成,不手工传
    assert fake.flushed  # insert 后必须 flush,否则 Bounded 一致性下 search 看不到新数据
    store.delete_entity("indicator", "GLU")
    assert fake.deleted_filters[0] == "entity_type == 'indicator' and entity_id == 'GLU'"


def test_delete_entity_type(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    fake.collections.add("kb")
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    store.delete_entity_type("condition")
    assert fake.deleted_filters[0] == "entity_type == 'condition'"
    assert fake.flushed  # 删除后必须 flush,后续 insert 才不会被 Bounded 一致性掩盖旧数据


def test_search_dense_and_fulltext(monkeypatch):
    import report_agent.knowledge.milvus_client as m

    fake = FakeMilvus()
    fake.collections.add("kb")
    monkeypatch.setattr(m, "MilvusClient", lambda **kw: fake)
    store = MilvusStore(uri="http://x", collection="kb", embedding_dim=1024)
    r1 = store.search_dense([0.1] * 1024, top_k=5)
    assert r1[0].search_method == "dense" and r1[0].chunk.entity_id == "GLU"
    r2 = store.search_fulltext("血糖", top_k=5)
    assert r2[0].search_method == "fulltext"
    assert fake.searches[0]["anns_field"] == "dense_vec"
    assert fake.searches[1]["anns_field"] == "sparse_vec"
    assert fake.searches[1]["data"] == ["血糖"]  # 稀疏检索直接传原始查询文本
