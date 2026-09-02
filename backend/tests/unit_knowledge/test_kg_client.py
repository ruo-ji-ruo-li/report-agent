from neo4j.exceptions import ServiceUnavailable

from report_agent.knowledge import kg_client


class FakeRecord(dict):
    def get(self, key, default=None):
        return dict.__getitem__(self, key) if key in self else default


class FakeCursor:
    def __init__(self, records):
        self._records = records

    def data(self, *keys):
        return [tuple(r.get(k) for k in keys) for r in self._records]

    def __iter__(self):
        # 真实 neo4j Result 可迭代(逐个产出 Record);KGClient._query 用
        # `[dict(r) for r in result]`,fake cursor 需补齐 __iter__ 才能驱动实现。
        return iter(self._records)


class FakeSession:
    def __init__(self, records_by_query):
        self.records_by_query = records_by_query

    def run(self, cypher, **params):
        self.last = (cypher, params)
        # 按 cypher 特征路由到不同记录集,模拟 indicator_context 的多次查询;
        # 不匹配时回落到 "all"(list_indicators / range_specs 场景)。
        if "HIGH_SUGGESTS" in cypher:
            key = "ctx"
        elif "PART_OF" in cypher:
            key = "clusters"
        elif "DEFAULT_INTERVENTION" in cypher:
            key = "iv"
        else:
            key = "all"
        return FakeCursor(self.records_by_query.get(key, []))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, records_by_query):
        self.records_by_query = records_by_query

    def session(self, **kw):
        return FakeSession(self.records_by_query)


def _client(driver, monkeypatch):
    # 真实 GraphDatabase.driver(uri, auth=...) 以位置参数传 uri;lambda 需兼容。
    monkeypatch.setattr(kg_client, "GraphDatabase", type("G", (), {"driver": lambda *a, **kw: driver}))
    return kg_client.KGClient(uri="bolt://x", user="u", password="p", database="neo4j")


def test_list_indicators_maps_fields(monkeypatch):
    rec = FakeRecord(
        {"code": "GLU", "name": "空腹血糖", "aliases": ["血糖", "FBG"], "unit": "mmol/L",
         "unit_conversions": {"mg/dL": 0.0555}, "category": "糖代谢", "description": "desc"}
    )
    c = _client(FakeDriver({"all": [rec]}), monkeypatch)
    entries = c.list_indicators()
    assert len(entries) == 1
    e = entries[0]
    assert e.code == "GLU" and e.aliases == ["血糖", "FBG"]
    assert e.unit_conversions == {"mg/dL": 0.0555}


def test_indicator_context_assembles_directions(monkeypatch):
    recs = [
        # dir 是 cypher 里 type(rel) 的返回值(完整关系类型),须与实现比较值一致。
        FakeRecord({"dir": "HIGH_SUGGESTS", "cond": "糖尿病风险", "cdesc": "c1", "strength": "strong", "note": "n1"}),
        FakeRecord({"dir": "LOW_SUGGESTS", "cond": "低血糖", "cdesc": "c2", "strength": "strong", "note": None}),
    ]
    c = _client(FakeDriver({"ctx": recs, "clusters": [], "iv": []}), monkeypatch)
    ctx = c.indicator_context("GLU")
    assert [f.name for f in ctx.high_suggests] == ["糖尿病风险"]
    assert [f.name for f in ctx.low_suggests] == ["低血糖"]


def test_driver_down_degrades_to_empty(monkeypatch):
    class Broken:
        def session(self, **kw):
            raise ServiceUnavailable("down")

    c = _client(Broken(), monkeypatch)
    assert c.list_indicators() == []  # 不抛异常,降级为空
    assert c.indicator_context("GLU").code == "GLU"
