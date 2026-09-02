"""验证项 1:真实 Milvus 上验证 jieba analyzer + BM25 Function 组合。

运行: uv run python scripts/verify_milvus_bm25.py
通过标准: 查询"血糖"能命中含"空腹血糖"的文本;dense 与 sparse 双路均返回结果。
失败兜底: 若 analyzer 报错或中文检索无效,记录输出,改用 spec §15 的兜底方案
(Postgres FTS + zhparser),并回来改设计文档与 Task 9。
"""
from report_agent.config import get_settings
from report_agent.knowledge.milvus_client import KnowledgeChunk, MilvusStore


def main() -> None:
    s = get_settings()
    store = MilvusStore(uri=s.milvus_uri, collection="verify_bm25", embedding_dim=8)
    try:
        store.ensure_collection()
        chunks = [
            KnowledgeChunk(
                chunk_id="v1", text="空腹血糖是指隔夜禁食8小时后的血糖浓度", entity_type="indicator",
                entity_id="GLU", title="空腹血糖", section_title="指标说明", chunk_index=0,
                total_chunks=1, parent_id="indicator:GLU",
            ),
            KnowledgeChunk(
                chunk_id="v2", text="白细胞计数反映机体感染与炎症状态", entity_type="indicator",
                entity_id="WBC", title="白细胞计数", section_title="指标说明", chunk_index=0,
                total_chunks=1, parent_id="indicator:WBC",
            ),
        ]
        store.upsert_chunks(chunks, dense_embeddings=[[0.1] * 8, [0.2] * 8])
        hits = store.search_fulltext("血糖", top_k=3)
        assert hits and any("血糖" in h.chunk.text for h in hits), "BM25 中文检索未命中"
        print(f"[PASS] fulltext('血糖') -> {[h.chunk.title for h in hits]}")
        hits2 = store.search_dense([0.1] * 8, top_k=3)
        assert hits2, "dense 检索无结果"
        print("[PASS] dense search OK")
    finally:
        # 清理验证集合
        from pymilvus import MilvusClient

        MilvusClient(uri=s.milvus_uri).drop_collection("verify_bm25")


if __name__ == "__main__":
    main()
