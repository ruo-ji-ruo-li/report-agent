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
        elif "Alias" in cypher:
            key = "alias"
        elif "Indicator {code: $code}) RETURN i.code AS code" in cypher:
            key = "code"
        else:
            key = "all"
        records = self.records_by_query.get(key, [])
        if callable(records):
            records = records(params)
        return FakeCursor(records)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, records_by_query):
        self.records_by_query = records_by_query
        self.sessions = []

    def session(self, **kw):
        s = FakeSession(self.records_by_query)
        self.sessions.append(s)
        return s


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
    assert c.find_indicator("GLU") is None  # 降级为空结果,不抛异常


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


def test_patterns_for_maps_criteria_and_passes_codes(monkeypatch):
    # criteria 是 COLLECT 产出的嵌套 dict 列表;description 缺失时回落为空串。
    recs = [
        FakeRecord({
            "name": "高TG高GLU", "description": "两项同时升高",
            "criteria": [{"code": "TG", "direction": "high"},
                         {"code": "GLU", "direction": "high"}],
        }),
        FakeRecord({"name": "空模式", "description": None, "criteria": []}),
    ]
    driver = FakeDriver({"all": recs})
    c = _client(driver, monkeypatch)
    pats = c.patterns_for({"TG", "GLU"})
    # codes 参数透传(list 序列化,顺序无关)
    assert sorted(driver.sessions[-1].last[1]["codes"]) == ["GLU", "TG"]
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


def test_parse_conversions_handles_str_dict_and_garbage():
    from report_agent.knowledge.kg_client import _parse_conversions

    assert _parse_conversions('{"mg/dL": 0.0555}') == {"mg/dL": 0.0555}  # Neo4j JSON 字符串
    assert _parse_conversions({"mg/dL": 0.0555}) == {"mg/dL": 0.0555}  # 旧 dict 数据直读
    assert _parse_conversions("not json") == {}
    assert _parse_conversions(None) == {}
    assert _parse_conversions("[]") == {}  # 合法 JSON 但非 dict


def test_patterns_for_skips_incomplete_criteria(monkeypatch):
    # KG 点查设计 §4.1:criteria 数与 criteria_json 基准不一致(部分取回/导入未就绪)
    # → 静默跳过,防单条件误命中。criteria_json 缺失(旧数据)不校验。
    recs = [
        FakeRecord({"name": "代谢综合征倾向", "description": "d",
                    "criteria_json": '[{"indicator_code": "TG", "direction": "high"}, '
                                     '{"indicator_code": "HDL_C", "direction": "low"}, '
                                     '{"indicator_code": "GLU", "direction": "high"}]',
                    "criteria": [{"code": "GLU", "direction": "high"}]}),  # 3 基准 vs 1 边 → 跳过
        FakeRecord({"name": "完整模式", "description": "d", "criteria_json": None,
                    "criteria": [{"code": "GLU", "direction": "high"}]}),  # 无基准 → 不校验
        FakeRecord({"name": "空模式", "description": "d", "criteria_json": "[]",
                    "criteria": []}),  # 基准 0 == 边 0 → 保留
    ]
    c = _client(FakeDriver({"all": recs}), monkeypatch)
    assert [p.name for p in c.patterns_for({"GLU"})] == ["完整模式", "空模式"]


def test_patterns_for_returns_empty_when_no_rows(monkeypatch):
    c = _client(FakeDriver({}), monkeypatch)
    assert c.patterns_for({"TG"}) == []
    assert c.patterns_for(set()) == []


# ---------- 变体生成与 find_indicator(KG 点查设计 §5.3)----------
def _glu_rec():
    return FakeRecord({
        "code": "GLU", "name": "空腹血糖", "aliases": ["血糖", "FBG"],
        "unit": "mmol/L", "unit_conversions": '{"mg/dL": 0.0555}',
        "category": "糖代谢", "description": "desc",
    })


def test_query_variants_normalization_and_case():
    from report_agent.knowledge.kg_client import _query_variants

    assert _query_variants("空腹 血糖") == ["空腹血糖"]          # 去空白
    assert "GLU" in _query_variants("glu")                     # 大写变体
    assert "FBG" in _query_variants("ＦＢＧ")                  # 全角→半角+大写
    # 顺序:原样 → 去括号内容 → 取括号内容
    v = _query_variants("空腹葡萄糖(空腹血糖)")
    assert v[:3] == ["空腹葡萄糖(空腹血糖)", "空腹葡萄糖", "空腹血糖"]
    assert _query_variants("空腹血糖") == ["空腹血糖"]          # 无冗余,去重


def test_find_indicator_code_fast_path(monkeypatch):
    driver = FakeDriver({"code": [_glu_rec()], "alias": []})
    c = _client(driver, monkeypatch)
    e = c.find_indicator("GLU")
    assert e.code == "GLU" and e.name == "空腹血糖" and e.aliases == ["血糖", "FBG"]
    assert e.unit_conversions == {"mg/dL": 0.0555}
    # code 命中后不再查 Alias
    assert not any("Alias" in s.last[0] for s in driver.sessions)


def test_find_indicator_tries_variants_in_order(monkeypatch):
    def alias(params):
        # 只有第三个候选(括号内容"空腹血糖")命中
        return [_glu_rec()] if params["key"] == "空腹血糖" else []

    driver = FakeDriver({"code": [], "alias": alias})
    c = _client(driver, monkeypatch)
    assert c.find_indicator("空腹葡萄糖(空腹血糖)").code == "GLU"
    keys = [s.last[1]["key"] for s in driver.sessions if "Alias" in s.last[0]]
    assert keys == ["空腹葡萄糖(空腹血糖)", "空腹葡萄糖", "空腹血糖"]


def test_find_indicator_returns_none_when_no_hits(monkeypatch):
    c = _client(FakeDriver({"code": [], "alias": []}), monkeypatch)
    assert c.find_indicator("不存在的指标") is None
