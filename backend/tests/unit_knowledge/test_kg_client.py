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


def test_range_specs_maps_fields(monkeypatch):
    # RETURN r 返回整个 RangeSpec 节点;r["r"] 是其属性 map(real Node 亦支持 .get)。
    rec = FakeRecord({
        "r": {
            "sex": "male", "age_min": 18.0, "age_max": 60.0, "low": 3.9, "high": 6.1,
            "critical_low": None, "critical_high": None, "unit": "mmol/L",
            "source_note": "检验科参考",
        },
    })
    c = _client(FakeDriver({"all": [rec]}), monkeypatch)
    specs = c.range_specs("GLU")
    assert len(specs) == 1
    r = specs[0]
    assert r.sex == "male"
    assert (r.age_min, r.age_max) == (18.0, 60.0)
    assert (r.low, r.high) == (3.9, 6.1)
    # critical 阈值为 None 时须原样透传,不得被默认值覆盖
    assert r.critical_low is None
    assert r.critical_high is None
    assert r.unit == "mmol/L"
    assert r.source_note == "检验科参考"


def test_all_patterns_maps_criteria(monkeypatch):
    # criteria 是 COLLECT 产出的嵌套 dict 列表;description 缺失时回落为空串。
    recs = [
        FakeRecord({
            "name": "高TG高GLU", "description": "两项同时升高",
            "criteria": [{"code": "TG", "direction": "high"},
                         {"code": "GLU", "direction": "high"}],
        }),
        FakeRecord({"name": "空模式", "description": None, "criteria": []}),
    ]
    c = _client(FakeDriver({"all": recs}), monkeypatch)
    pats = c.all_patterns()
    assert [p.name for p in pats] == ["高TG高GLU", "空模式"]
    p0 = pats[0]
    assert p0.description == "两项同时升高"
    assert [(pc.indicator_code, pc.direction) for pc in p0.criteria] == [("TG", "high"),
                                                                          ("GLU", "high")]
    assert pats[1].description == ""
    assert pats[1].criteria == []


def test_indicator_context_collects_clusters(monkeypatch):
    c = _client(FakeDriver({
        "ctx": [], "clusters": [FakeRecord({"name": "糖代谢"}), FakeRecord({"name": "血脂"})],
        "iv": [],
    }), monkeypatch)
    ctx = c.indicator_context("GLU")
    assert ctx.clusters == ["糖代谢", "血脂"]
    assert ctx.high_suggests == [] and ctx.low_suggests == []
    assert ctx.interventions == [] and ctx.departments == []


def test_indicator_context_assembles_interventions_and_departments(monkeypatch):
    ivs = [
        FakeRecord({"level": "lifestyle", "text": "控制饮食", "timeframe": "3个月",
                    "deps": ["endo", "cardio", None]}),
        FakeRecord({"level": "urgent", "text": "立即就诊", "timeframe": None,
                    "deps": [None, "endo", "neuro"]}),
    ]
    c = _client(FakeDriver({"ctx": [], "clusters": [], "iv": ivs}), monkeypatch)
    ctx = c.indicator_context("GLU")
    # deps 里的 null(OPTIONAL MATCH 无科室)须被过滤
    assert ctx.interventions[0].departments == ["endo", "cardio"]
    assert ctx.interventions[1].departments == ["endo", "neuro"]
    assert ctx.interventions[0].timeframe == "3个月"
    assert ctx.interventions[1].timeframe is None
    # departments 全局去重(endo 出现两次)+ 排序(跨干预聚合)
    assert ctx.departments == ["cardio", "endo", "neuro"]
    assert [(iv.level, iv.text) for iv in ctx.interventions] == [
        ("lifestyle", "控制饮食"), ("urgent", "立即就诊"),
    ]


def test_indicator_context_falls_back_name_to_code(monkeypatch):
    # 主查询无记录(name 从 "all" 取)→ name 回退为 code,不抛 IndexError。
    c = _client(FakeDriver({"ctx": [], "clusters": [], "iv": []}), monkeypatch)
    ctx = c.indicator_context("GLU")
    assert ctx.code == "GLU"
    assert ctx.name == "GLU"
