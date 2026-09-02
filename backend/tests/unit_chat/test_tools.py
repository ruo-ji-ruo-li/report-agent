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
    def __init__(self):
        self.kg = FakeKG()
        self.db = FakeDB()
        self.retriever = FakeRetriever()


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
