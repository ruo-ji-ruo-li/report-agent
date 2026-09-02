"""chat 工具单测。brief Step 2 逐字 + 一处最小修正: FakeKG 补 list_indicators()
(brief 的 query_indicator_knowledge/compute_reference_range/search_knowledge 都经
match_indicator(indicator, deps.kg.list_indicators()) 取指标目录,FakeKG 缺该方法
会 AttributeError;实现与 brief 一致,补在测试假件上)。"""
import asyncio

from report_agent.chat.tools import make_tools
from report_agent.knowledge.kg_client import IndicatorContext, IndicatorEntry, RangeSpec
from report_agent.retrieval.hybrid import Evidence


class FakeKG:
    def __init__(self):
        self.entries = [IndicatorEntry(code="GLU", name="空腹血糖", aliases=["血糖"], unit="mmol/L")]
        self.specs = [RangeSpec(sex="any", age_min=18, age_max=100, low=3.9, high=6.1,
                                critical_low=2.8, critical_high=22.0, unit="mmol/L", source_note=None)]

    def list_indicators(self):
        return self.entries

    def indicator_context(self, code):
        return IndicatorContext(code=code, name="空腹血糖")

    def range_specs(self, code):
        return self.specs if code == "GLU" else []


class FakeDB:
    async def get_report_detail(self, report_id):
        return {"meta": {"sex": "male", "age": 40}, "items": [], "normalized": [
            {"item_name": "空腹血糖", "indicator_code": "GLU", "value_num": 6.5,
             "unit": "mmol/L", "status": "high", "ref_low": 3.9, "ref_high": 6.1, "critical": False}]}


class FakeRetriever:
    async def search(self, q):
        return [Evidence(text="空腹血糖升高与糖尿病风险相关", source="dense", title="空腹血糖")]


class FakeDeps:
    def __init__(self, threshold: float = 0.0):
        self.kg = FakeKG()
        self.db = FakeDB()
        self.retriever = FakeRetriever()
        self.settings = type("S", (), {"refusal_rrf_threshold": threshold})()


def test_get_my_report_contains_values():
    tools = {t.__name__: t for t in make_tools(FakeDeps(), "r1")}
    out = asyncio.run(tools["get_my_report"]())
    assert "空腹血糖" in out and "6.5" in out


def test_compute_reference_range_judges_value():
    tools = {t.__name__: t for t in make_tools(FakeDeps(), "r1")}
    out = asyncio.run(tools["compute_reference_range"](indicator="血糖", value=7.0))
    assert "7.0" in out and "3.9" in out and "6.1" in out
    assert "升高" in out or "high" in out


def test_query_indicator_knowledge_unmatched_returns_candidates():
    tools = {t.__name__: t for t in make_tools(FakeDeps(), "r1")}
    out = asyncio.run(tools["query_indicator_knowledge"](indicator="不存在指标"))
    assert "候选" in out or "匹配" in out  # 给出候选列表让 agent 澄清


def test_search_knowledge_empty_hints_refusal():
    class EmptyRetriever:
        async def search(self, q):
            return []

    deps = FakeDeps()
    deps.retriever = EmptyRetriever()
    tools = {t.__name__: t for t in make_tools(deps, "r1")}
    out = asyncio.run(tools["search_knowledge"](query="量子物理"))
    assert "未检索到" in out


class _LowScoreRetriever:
    def __init__(self, rrf_score):
        self.rrf_score = rrf_score

    async def search(self, q):
        return [Evidence(text="近邻文本", source="dense", rrf_score=self.rrf_score)]


def test_search_knowledge_below_rrf_threshold_refuses_same_text():
    """F5:检索结果非空但 top 证据 rrf_score < REFUSAL_RRF_THRESHOLD → 与空结果
    同文案拒答(Milvus top-k 恒返回近邻,语料外拒答的确定性兜底)。"""
    deps = FakeDeps(threshold=0.05)
    deps.retriever = _LowScoreRetriever(rrf_score=0.01)
    tools = {t.__name__: t for t in make_tools(deps, "r1")}
    out = asyncio.run(tools["search_knowledge"](query="语料外的罕见病"))
    assert "未检索到" in out and "[e0]" not in out


def test_search_knowledge_at_or_above_threshold_returns_evidence():
    """F5:阈值 0.05 时 rrf_score=0.05(等于阈值)不算低于 → 正常返回证据(非拒答)。"""
    deps = FakeDeps(threshold=0.05)
    deps.retriever = _LowScoreRetriever(rrf_score=0.05)
    tools = {t.__name__: t for t in make_tools(deps, "r1")}
    out = asyncio.run(tools["search_knowledge"](query="空腹血糖"))
    assert "[e0]" in out and "未检索到" not in out
