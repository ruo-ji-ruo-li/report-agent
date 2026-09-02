"""三路检索(KG + Milvus dense + Milvus fulltext-BM25)+ RRF 融合 + 证据预算。

RRF 融合移植自 C9 hybrid_retrieval.py:629-714,改造点:
- 去重键 = (source, entity_id 或文本 md5)(C9 用 node_id)
- 保留 rrf_score/rrf_sources 溯源元数据
- 同源同文档多 chunk 只计最佳 rank 一次(C9 同)
- KG 事实非分块"文档":同指标多条事实(entity_id 相同)按文本指纹各自独立成键,不被共享 entity_id 误去重
任一路失败 → 该路返回空列表,由 RRF 吸收(spec §11)。
"""
import asyncio
import hashlib
from dataclasses import dataclass, field

from report_agent.config import Settings
from report_agent.knowledge.kg_client import KGClient
from report_agent.knowledge.milvus_client import MilvusStore, ScoredChunk
from report_agent.observability import get_logger

log = get_logger(__name__)
_RRF_K = 60


@dataclass
class RetrievalQuery:
    text: str
    indicator_code: str | None = None  # KG 路参数(有则查子图)
    direction: str | None = None       # high / low(过滤疾病提示方向)


@dataclass
class Evidence:
    text: str
    source: str  # kg / dense / fulltext
    entity_type: str | None = None
    entity_id: str | None = None
    title: str | None = None
    score: float | None = None
    rrf_score: float | None = None
    rrf_sources: list[str] = field(default_factory=list)


def _evidence_key(e: Evidence) -> tuple:
    """Milvus chunk 按 (source, entity_id) 去重(同文档多 chunk 只计最佳 rank);
    KG 事实同指标共享 entity_id,改用文本指纹,每条事实独立成键。"""
    return (e.source, e.entity_id if (e.source != "kg" and e.entity_id)
            else hashlib.md5(e.text[:100].encode("utf-8")).hexdigest())


def rrf_merge(ranked_lists: list[list[Evidence]], top_k: int, k: int = _RRF_K) -> list[Evidence]:
    """纯排名融合:score(d) = Σ_lists 1/(k + best_rank_l(d))。不修改输入列表;返回项为输入对象并附融合元数据。"""
    scores: dict[tuple, float] = {}
    best_rank: dict[tuple, int] = {}
    canonical: dict[tuple, Evidence] = {}
    sources: dict[tuple, list[str]] = {}
    for lst in ranked_lists:
        seen: set[tuple] = set()
        for rank, ev in enumerate(lst):
            key = _evidence_key(ev)
            if key in seen:  # 同源同文档多 chunk 只计最佳 rank
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in best_rank or rank < best_rank[key]:
                best_rank[key] = rank
                canonical[key] = ev
            if ev.source not in sources.setdefault(key, []):
                sources[key].append(ev.source)
    merged = []
    for key, score in scores.items():
        ev = canonical[key]
        ev.rrf_score = score
        ev.rrf_sources = sources[key]
        merged.append(ev)
    merged.sort(key=lambda e: e.rrf_score, reverse=True)
    return merged[:top_k]


def _trim_budget(evidences: list[Evidence], budget: int) -> list[Evidence]:
    """证据字符预算:按 RRF 序累加,超预算的丢弃(spec §4.3)。"""
    kept, total = [], 0
    for e in evidences:
        if kept and total + len(e.text) > budget:
            continue
        kept.append(e)
        total += len(e.text)
    return kept


class HybridRetriever:
    def __init__(
        self, milvus: MilvusStore, kg: KGClient, embedder, settings: Settings,
    ):
        self._milvus = milvus
        self._kg = kg
        self._embedder = embedder
        self._s = settings

    async def search(self, q: RetrievalQuery) -> list[Evidence]:
        top_k = self._s.retrieval_top_k
        kg_ev = await asyncio.to_thread(self._kg_path, q)
        dense_ev, ft_ev = [], []
        try:
            emb = await self._embedder.embed_text(q.text)
            dense = await asyncio.to_thread(self._milvus.search_dense, emb, top_k * 2)
            ft = await asyncio.to_thread(self._milvus.search_fulltext, q.text, top_k * 2)
            dense_ev = [self._chunk_evidence(s) for s in dense]
            ft_ev = [self._chunk_evidence(s) for s in ft]
        except Exception as e:  # noqa: BLE001 —— Milvus 不可用:dense+BM25 一并降级(spec §2 决策)
            log.error("milvus_retrieval_failed", error=str(e))
        merged = rrf_merge([kg_ev, dense_ev, ft_ev], top_k)
        result = _trim_budget(merged, self._s.evidence_budget_chars)
        log.info("retrieval_done", sources=[e.source for e in result], count=len(result))
        return result

    def _kg_path(self, q: RetrievalQuery) -> list[Evidence]:
        """KG 路 = 直接 Cypher 子图查询(code 已知,无 LLM 查询理解)。失败 → []。"""
        if not q.indicator_code:
            return []
        try:
            ctx = self._kg.indicator_context(q.indicator_code)
        except Exception as e:  # noqa: BLE001
            log.error("kg_retrieval_failed", error=str(e))
            return []
        out = []
        suggests = []
        if q.direction != "low":
            suggests += [(f"{ctx.name}升高可能提示:{f.name}", f.strength, f.note)
                         for f in ctx.high_suggests]
        if q.direction != "high":
            suggests += [(f"{ctx.name}降低可能提示:{f.name}", f.strength, f.note)
                         for f in ctx.low_suggests]
        for text, strength, note in suggests:
            body = text + (f"(强度:{strength})" if strength else "")
            if note:
                body += f"。{note}"
            out.append(Evidence(text=body, source="kg", entity_type="indicator",
                                entity_id=q.indicator_code, title=f"{ctx.name} 知识图谱"))
        for iv in ctx.interventions:
            tf = f",时间窗: {iv.timeframe}" if iv.timeframe else ""
            out.append(Evidence(
                text=f"[{iv.level}] 建议: {iv.text}{tf}", source="kg",
                entity_type="indicator", entity_id=q.indicator_code,
                title=f"{ctx.name} 知识图谱",
            ))
        if ctx.clusters:
            out.append(Evidence(text=f"所属指标簇: {'、'.join(ctx.clusters)}", source="kg",
                                entity_type="indicator", entity_id=q.indicator_code,
                                title=f"{ctx.name} 知识图谱"))
        return out

    def _chunk_evidence(self, s: ScoredChunk) -> Evidence:
        return Evidence(
            text=s.chunk.text, source=s.search_method, entity_type=s.chunk.entity_type,
            entity_id=s.chunk.entity_id, title=s.chunk.title, score=s.score,
        )
