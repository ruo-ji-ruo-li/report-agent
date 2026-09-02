import asyncio

from report_agent.config import Settings
from report_agent.knowledge.kg_client import ConditionFact, IndicatorContext
from report_agent.knowledge.milvus_client import KnowledgeChunk, ScoredChunk
from report_agent.retrieval.hybrid import Evidence, HybridRetriever, RetrievalQuery, rrf_merge


def _ev(text: str, source: str, eid: str | None = None) -> Evidence:
    return Evidence(text=text, source=source, entity_type=None, entity_id=eid, title=None, score=None)


def test_rrf_merge_sums_ranks_and_dedups():
    a = _ev("文本A", "dense", "e1")
    b = _ev("文本B", "fulltext", "e2")
    c = _ev("文本C", "dense", "e3")
    d = _ev("文本A重复", "fulltext", "e1")  # 与 a 同文档不同源 → 两路各计一次分
    merged = rrf_merge([[a, c], [d, b]], top_k=5, k=60)
    scores = {m.text: m.rrf_score for m in merged}
    assert abs(scores["文本A"] - 1 / 60) < 1e-9  # e1 在 dense 路 rank0
    assert abs(scores["文本A重复"] - 1 / 60) < 1e-9  # e1 在 fulltext 路 rank0(跨源各计分)
    assert abs(scores["文本B"] - 1 / 61) < 1e-9  # rank1
    assert abs(scores["文本C"] - 1 / 61) < 1e-9  # rank1
    # 同文档 e1 两路 rank0 → 贡献合计 1/60 + 1/60(计两次分)
    assert abs(sum(m.rrf_score for m in merged if m.entity_id == "e1") - (1 / 60 + 1 / 60)) < 1e-9
    assert merged[0].rrf_sources == ["dense"]  # 去重键含 source:单路命中项溯源只列该路


def test_rrf_merge_same_source_multi_chunk_counts_once():
    x1 = _ev("chunk1", "dense", "e1")
    x2 = _ev("chunk2", "dense", "e1")  # 同源同文档另一 chunk
    merged = rrf_merge([[x1, x2]], top_k=5)
    assert len(merged) == 1  # 只计最佳 rank 一次
    assert merged[0].text == "chunk1"


def _fake_store():
    class S:
        def __init__(self):
            self.fail = False

        def search_dense(self, emb, top_k):
            if self.fail:
                raise RuntimeError("milvus down")
            return [ScoredChunk(chunk=KnowledgeChunk(
                chunk_id="c1", text="空腹血糖升高与糖尿病风险相关", entity_type="indicator",
                entity_id="GLU", title="空腹血糖", section_title="升高提示",
                chunk_index=0, total_chunks=1, parent_id="indicator:GLU",
            ), score=0.9, search_method="dense")]

        def search_fulltext(self, q, top_k):
            if self.fail:
                raise RuntimeError("milvus down")
            return []

    return S()


def _fake_kg(fail=False):
    class K:
        def indicator_context(self, code):
            if fail:
                raise RuntimeError("kg down")
            return IndicatorContext(code=code, name="空腹血糖", high_suggests=[
                ConditionFact(name="糖尿病风险", description=None, strength="strong", note=None)],
                clusters=["糖代谢"])
    return K()


def _fake_embedder():
    class E:
        async def embed_text(self, text):
            return [0.1] * 1024
    return E()


def _retriever(store, kg, settings=None) -> HybridRetriever:
    return HybridRetriever(milvus=store, kg=kg, embedder=_fake_embedder(),
                           settings=settings or Settings())


def test_search_merges_three_paths():
    r = _retriever(_fake_store(), _fake_kg())
    evs = asyncio.run(r.search(RetrievalQuery(text="空腹血糖升高", indicator_code="GLU", direction="high")))
    sources = {e.source for e in evs}
    assert sources == {"kg", "dense"}
    assert any(e.rrf_score is not None for e in evs)


def test_search_kg_down_still_returns_milvus():
    r = _retriever(_fake_store(), _fake_kg(fail=True))
    evs = asyncio.run(r.search(RetrievalQuery(text="空腹血糖升高", indicator_code="GLU")))
    assert evs and all(e.source == "dense" for e in evs)  # KG 挂 → Milvus 路继续


def test_search_milvus_down_still_returns_kg():
    store = _fake_store()
    store.fail = True
    r = _retriever(store, _fake_kg())
    evs = asyncio.run(r.search(RetrievalQuery(text="空腹血糖升高", indicator_code="GLU")))
    assert evs and all(e.source == "kg" for e in evs)


def test_search_everything_down_returns_empty():
    store = _fake_store()
    store.fail = True
    r = _retriever(store, _fake_kg(fail=True))
    assert asyncio.run(r.search(RetrievalQuery(text="x"))) == []  # 全挂 → 空,由上层占位证据处理
