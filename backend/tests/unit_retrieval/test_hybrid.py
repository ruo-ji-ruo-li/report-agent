import asyncio

from report_agent.config import Settings
from report_agent.knowledge.kg_client import ConditionFact, IndicatorContext, InterventionFact
from report_agent.knowledge.milvus_client import KnowledgeChunk, ScoredChunk
from report_agent.retrieval.hybrid import (
    Evidence,
    HybridRetriever,
    RetrievalQuery,
    build_item_query,
    rrf_merge,
)


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


def test_search_kg_keeps_distinct_facts():
    """I1 回归:同指标多条 KG 事实(提示 + 干预)各自独立成键,不坍缩为 1 条。"""

    class K:
        def indicator_context(self, code):
            return IndicatorContext(code=code, name="空腹血糖", high_suggests=[
                ConditionFact(name="糖尿病风险", description=None, strength="strong", note=None),
                ConditionFact(name="应激性高血糖", description=None, strength="medium", note=None),
            ], low_suggests=[
                ConditionFact(name="低血糖风险", description=None, strength="medium", note=None),
            ], interventions=[
                InterventionFact(level="lifestyle", text="控制饮食并规律运动", timeframe="持续"),
            ])

    r = _retriever(_fake_store(), K())
    evs = asyncio.run(r.search(RetrievalQuery(text="空腹血糖升高", indicator_code="GLU", direction="high")))
    kg_evs = [e for e in evs if e.source == "kg"]
    assert len(kg_evs) >= 2  # 修复前同键坍缩只剩 1 条
    assert len({e.rrf_score for e in kg_evs}) == len(kg_evs)  # 每条事实各自独立计分


def test_build_item_query_matches_pipeline_caliber():
    """口径锚:异常项检索 query 的唯一构造来源(管线 retrieve_stage 与评测 runner 共用)。

    text/direction 逐字锁定 —— test_stages.py::test_retrieve_stage_payload_placeholder_when_no_hits
    断言同一字面量,两侧任一处漂移即红。
    """
    q = build_item_query("空腹血糖", "high", "GLU")
    assert (q.text, q.direction, q.indicator_code) == ("空腹血糖 high 健康风险", "high", "GLU")
    assert build_item_query("血钙", "low", "CA").direction == "low"
    # critical_* 归并到同向(direction 只做 KG 路方向过滤,见 _kg_path)
    assert build_item_query("血钾", "critical_high", "K").direction == "high"
    assert build_item_query("血钾", "critical_low", "K").direction == "low"
    # 目录外项(code=None)透传,文本仍带项名与方向
    q_unknown = build_item_query("某未知项", "high", None)
    assert (q_unknown.text, q_unknown.indicator_code) == ("某未知项 high 健康风险", None)
