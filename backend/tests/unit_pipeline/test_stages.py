"""parse/normalize/compare/retrieve/generate/guardrail/plan 七阶段单测:FakeDB/FakeKG/
FakeNormalizer,零 DB/零网络。retrieve/generate/plan 由 Task 12 追加注册,
guardrail 由 Task 13 注册(阶段级测试在 Task 13 评审修复轮补齐)。

锁定语义(brief + 评审裁决):
- parse_stage: manual 直通校验;非 manual needs_meta=sex/age 缺失 → 暂停信号;不支持来源 ValueError
- normalize_stage: 换算后标准值入库但 raw 原值(raw_value_num/raw_unit)不丢
- compare_stage: sex/age 缺失抛 ValueError 引导补录;payload n_abnormal/critical_count/matched_patterns
- retrieve_stage: 逐异常项并发检索;空结果 → placeholder 证据;Evidence 8 键 JSON 化
- generate_stage: 逐项解读(LLM 失败回退模板)+ 总评 + 四段式 doc;整体不因 LLM 失败而抛
- guardrail_stage: 深拷贝 generate doc 只读;总评/逐项过 enforce;guardrail doc 为落库权威源
- plan_stage: 复查计划落库;解读行 doc 以 guardrail checkpoint 为源(generate 兜底),
  degraded 只随 guardrail 标志(与 plan.degraded 无关);复查单自身模板降级留在
  followup_plans.degraded;全正常报告不降级
"""
import asyncio
from types import SimpleNamespace

import pytest

from report_agent.guardrails.rules import (
    GuardrailContext,
    Verdict,
    item_guardrail_text,
    rule_guardrail,
)
from report_agent.knowledge.kg_client import (
    IndicatorContext,
    PatternCriterion,
    PatternSpec,
    RangeSpec,
)
from report_agent.llm.client import LLMError
from report_agent.parsing.parse_report import ParseOutput
from report_agent.parsing.schemas import NormalizedItem, RawReportItem, ReportMeta
from report_agent.pipeline import stages as stages_mod
from report_agent.pipeline.stages import (
    StageContext,
    compare_stage,
    generate_stage,
    guardrail_stage,
    normalize_stage,
    parse_stage,
    plan_stage,
    retrieve_stage,
)
from report_agent.retrieval.hybrid import Evidence


class FakeDB:
    """记录各写调用;raw/normalized 由测试预置。delete_* 模拟报告/任务级先删后写(F3),
    重跑只保留最近一批行。"""

    def __init__(self):
        self.raw_items: list[RawReportItem] = []
        self.normalized: list[NormalizedItem] = []
        self.updated_meta = None
        self.saved_raw = None
        self.saved_norm = None
        self.applied = None
        self.saved_interpretation = None
        self.saved_followup = None
        self.delete_raw_calls: list[str] = []
        self.delete_norm_calls: list[str] = []
        self.delete_interpretation_calls: list[str] = []
        self.delete_followup_calls: list[str] = []
        self.raw_store: list = []
        self.norm_store: list = []
        self.interp_store: list = []
        self.followup_store: list = []

    async def get_raw_items(self, report_id):
        return self.raw_items

    async def get_normalized(self, report_id):
        return self.normalized

    async def update_report_meta(self, report_id, meta):
        self.updated_meta = meta

    async def save_raw_items(self, report_id, items):
        self.saved_raw = items
        self.raw_store.extend(items)

    async def delete_raw_items(self, report_id):
        self.delete_raw_calls.append(report_id)
        self.raw_store.clear()

    async def save_normalized(self, report_id, items):
        self.saved_norm = items
        self.norm_store.extend(items)

    async def delete_normalized(self, report_id):
        self.delete_norm_calls.append(report_id)
        self.norm_store.clear()

    async def apply_judgments(self, report_id, judgments):
        self.applied = judgments

    async def save_interpretation(self, report_id, task_id, doc):
        self.saved_interpretation = doc
        self.interp_store.append(doc)

    async def delete_interpretation(self, task_id):
        self.delete_interpretation_calls.append(task_id)
        self.interp_store.clear()

    async def save_followup(self, report_id, task_id, doc):
        self.saved_followup = doc
        self.followup_store.append(doc)

    async def delete_followup(self, task_id):
        self.delete_followup_calls.append(task_id)
        self.followup_store.clear()


class FakeKG:
    def __init__(self, specs=None, patterns=None, contexts=None):
        self.specs = specs or {}
        self.patterns = patterns or []
        self.contexts = contexts or {}
        self.range_calls: list[str] = []

    def range_specs(self, code):
        self.range_calls.append(code)
        return self.specs.get(code, [])

    def all_patterns(self):
        return self.patterns

    def indicator_context(self, code):
        return self.contexts.get(code, IndicatorContext(code=code, name=code))


class FakeLLM:
    """interpret_item/smooth_basis 走 complete_json,generate_summary 走 chat。"""

    def __init__(self, json_result=None, text_result="", error=False):
        self.json_result = json_result
        self.text_result = text_result
        self.error = error
        self.calls = []

    async def complete_json(self, messages, retry_feedback=True):
        self.calls.append(("json", messages))
        if self.error:
            raise LLMError("down")
        return self.json_result

    async def chat(self, messages, temperature=0.1, **kw):
        self.calls.append(("chat", messages))
        if self.error:
            raise LLMError("down")
        return self.text_result


class FakeNormalizer:
    def __init__(self, out=None):
        self.out = out or []
        self.calls = []

    async def normalize(self, raw_items, llm=None):
        self.calls.append((list(raw_items), llm))
        return self.out


def _raw(name: str, value_num: float | None, unit: str | None = None,
         ref_range: str | None = None) -> RawReportItem:
    return RawReportItem(
        name=name, value_text=str(value_num) if value_num is not None else None,
        value_num=value_num, unit=unit, ref_range_text=ref_range, abnormal_flag=None,
    )


# ---------------- parse ----------------

def test_parse_stage_manual_validates_existing_items():
    db = FakeDB()
    db.raw_items = [_raw("WBC", 5.2, "10^9/L", "3.5-9.5")]
    report = {"id": "r1", "source": "manual", "file_path": None, "sex": "male", "age": 40.0}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=object())
    payload = asyncio.run(parse_stage(ctx))
    assert payload == {"method": "manual", "n_items": 1, "needs_meta": False}


def test_parse_stage_manual_missing_meta_pauses_like_file_paths():
    """F2: manual 缺 sex/age → needs_meta=True(与 pdf/photo 同语义)。此前恒 False 使
    compare 抛错 → 任务永久 failed,PATCH meta 无从恢复(死端)。"""
    db = FakeDB()
    db.raw_items = [_raw("WBC", 5.2, "10^9/L", "3.5-9.5")]
    report = {"id": "r1", "source": "manual", "file_path": None, "sex": None, "age": None}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=object())
    payload = asyncio.run(parse_stage(ctx))
    assert payload["method"] == "manual"
    assert payload["needs_meta"] is True
    assert payload["n_items"] == 1
    # runner 收到 needs_meta=True 即 pause_for_meta → PATCH /meta → resume_from_meta,
    # compare 不再因缺 meta 抛错(runner 语义由 test_runner.test_run_task_needs_meta_pauses 锁定)

    report2 = {"id": "r1", "source": "manual", "file_path": None, "sex": "male", "age": None}
    ctx2 = StageContext(task_id="t1", report=report2, db=db, deps=object())
    assert asyncio.run(parse_stage(ctx2))["needs_meta"] is True  # 只缺 age 同样暂停

    report3 = {"id": "r1", "source": "manual", "file_path": None, "sex": "female", "age": 35.0}
    ctx3 = StageContext(task_id="t1", report=report3, db=db, deps=object())
    assert asyncio.run(parse_stage(ctx3))["needs_meta"] is False  # meta 齐备直通


def test_parse_stage_manual_without_items_raises():
    db = FakeDB()
    report = {"id": "r1", "source": "manual", "file_path": None}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=object())
    with pytest.raises(ValueError, match="手动录入未提供任何检验项"):
        asyncio.run(parse_stage(ctx))


def test_parse_stage_pdf_persists_meta_raw_and_signals_meta_missing(monkeypatch):
    items = [_raw("GLU", 6.8, "mmol/L", "3.9-6.1")]
    out = ParseOutput(
        items=items, meta=ReportMeta(institution="体检中心", report_date="2026-01-01",
                                     sex=None, age=None),
        method="unstructured",
    )

    async def fake_parse(file_path, source, settings, llms):
        assert file_path == "/tmp/r.pdf"
        assert source == "pdf"
        return out

    monkeypatch.setattr(stages_mod, "parse_report", fake_parse)
    db = FakeDB()
    deps = SimpleNamespace(settings=object(), llms=object())
    report = {"id": "r1", "source": "pdf", "file_path": "/tmp/r.pdf"}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    payload = asyncio.run(parse_stage(ctx))
    assert payload == {"method": "unstructured", "n_items": 1, "needs_meta": True}
    assert db.updated_meta is out.meta
    assert db.saved_raw == items


def test_parse_stage_full_meta_does_not_pause(monkeypatch):
    out = ParseOutput(
        items=[_raw("GLU", 6.8)], meta=ReportMeta(sex="male", age=40.0), method="vision",
    )

    async def fake_parse(file_path, source, settings, llms):
        return out

    monkeypatch.setattr(stages_mod, "parse_report", fake_parse)
    db = FakeDB()
    deps = SimpleNamespace(settings=object(), llms=object())
    report = {"id": "r1", "source": "photo", "file_path": "/tmp/r.png"}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    payload = asyncio.run(parse_stage(ctx))
    assert payload == {"method": "vision", "n_items": 1, "needs_meta": False}


def test_parse_stage_unsupported_source_raises(monkeypatch):
    async def fake_parse(file_path, source, settings, llms):
        return None

    monkeypatch.setattr(stages_mod, "parse_report", fake_parse)
    db = FakeDB()
    deps = SimpleNamespace(settings=object(), llms=object())
    report = {"id": "r1", "source": "weird", "file_path": None}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    with pytest.raises(ValueError, match="解析失败:不支持的来源"):
        asyncio.run(parse_stage(ctx))


# ---------------- normalize ----------------

def test_normalize_stage_converts_but_keeps_raw_original_values():
    raws = [_raw("空腹血糖", 6.8, "mmol/L", "3.9-6.1")]
    # 模拟 Normalizer 换算:标准值 6.8 mmol/L → 122.4 mg/dL;raw 原值必须原样保留
    converted = [
        NormalizedItem(
            raw_index=0, name="空腹血糖", indicator_code="GLU",
            value_text="6.8", value_num=122.4, unit="mg/dL",
            raw_value_num=6.8, raw_unit="mmol/L", ref_range_text="3.9-6.1",
            range_from="report",
        )
    ]
    norm = FakeNormalizer(out=converted)
    deps = SimpleNamespace(normalizer=norm, llms=SimpleNamespace(chat=object()))
    db = FakeDB()
    db.raw_items = raws
    report = {"id": "r1", "source": "pdf"}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    payload = asyncio.run(normalize_stage(ctx))
    assert payload == {"n_items": 1}
    assert norm.calls[0][0] == raws
    assert norm.calls[0][1] is deps.llms.chat
    saved = db.saved_norm
    assert saved == converted
    assert saved[0].value_num == 122.4  # 标准单位值已换算
    assert saved[0].raw_value_num == 6.8  # 落库前 raw 原值不丢
    assert saved[0].raw_unit == "mmol/L"


def test_normalize_stage_empty_result():
    norm = FakeNormalizer(out=[])
    deps = SimpleNamespace(normalizer=norm, llms=SimpleNamespace(chat=object()))
    db = FakeDB()
    report = {"id": "r1", "source": "pdf"}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    payload = asyncio.run(normalize_stage(ctx))
    assert payload == {"n_items": 0}
    assert db.saved_norm == []


# ---------------- compare ----------------

def test_compare_stage_missing_meta_raises_guidance():
    report = {"id": "r1", "sex": None, "age": 40.0}
    ctx = StageContext(task_id="t1", report=report, db=FakeDB(), deps=object())
    with pytest.raises(ValueError, match="补录"):
        asyncio.run(compare_stage(ctx))

    report = {"id": "r1", "sex": "male", "age": None}
    ctx = StageContext(task_id="t1", report=report, db=FakeDB(), deps=object())
    with pytest.raises(ValueError, match="补录"):
        asyncio.run(compare_stage(ctx))


def _glu_item() -> NormalizedItem:
    return NormalizedItem(
        raw_index=0, name="空腹血糖", indicator_code="GLU",
        value_text="12.0", value_num=12.0, unit="mmol/L",
        raw_value_num=12.0, raw_unit="mmol/L", ref_range_text="3.9-6.1",
        range_from="report",
    )


def _unmapped_item() -> NormalizedItem:
    return NormalizedItem(
        raw_index=1, name="未知检验项", indicator_code=None,
        value_text="+", value_num=None, unit=None,
        raw_value_num=None, raw_unit=None, ref_range_text=None, range_from=None,
    )


def _alt_item() -> NormalizedItem:
    return NormalizedItem(
        raw_index=2, name="丙氨酸氨基转移酶", indicator_code="ALT",
        value_text="20", value_num=20.0, unit="U/L",
        raw_value_num=20.0, raw_unit="U/L", ref_range_text=None, range_from=None,
    )


def _specs() -> dict[str, list[RangeSpec]]:
    return {
        "GLU": [RangeSpec(sex="any", age_min=0.0, age_max=150.0, low=3.9, high=6.1,
                          critical_low=None, critical_high=10.0, unit="mmol/L",
                          source_note=None)],
        "ALT": [RangeSpec(sex="any", age_min=0.0, age_max=150.0, low=7.0, high=40.0,
                          critical_low=None, critical_high=None, unit="U/L",
                          source_note=None)],
    }


def test_compare_stage_payload_counts_and_judgments():
    kg = FakeKG(specs=_specs())
    db = FakeDB()
    db.normalized = [_glu_item(), _unmapped_item(), _alt_item()]
    deps = SimpleNamespace(kg=kg)
    report = {"id": "r1", "sex": "male", "age": 40.0}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    payload = asyncio.run(compare_stage(ctx))
    assert payload["n_abnormal"] == 1  # 危急高算异常;unmapped/normal 不算
    assert payload["critical_count"] == 1
    assert payload["matched_patterns"] == []
    assert set(kg.range_calls) == {"GLU", "ALT"}  # 每指标只查一次
    js = payload["judgments"]
    assert len(js) == 3
    assert js[0]["status"] == "critical_high" and js[0]["critical"] is True
    assert js[0]["range_source"] == "report"
    assert js[1]["status"] == "unmapped"
    assert js[2]["status"] == "normal"
    assert js[2]["range_source"] == "kg"
    assert db.applied is not None and len(db.applied) == 3
    assert db.applied[0].status.value == "critical_high"


def test_compare_stage_reports_matched_patterns():
    kg = FakeKG(specs=_specs(), patterns=[
        PatternSpec(name="血糖升高模式", description="d",
                    criteria=[PatternCriterion(indicator_code="GLU", direction="high")]),
    ])
    db = FakeDB()
    db.normalized = [_glu_item()]
    deps = SimpleNamespace(kg=kg)
    report = {"id": "r1", "sex": "male", "age": 40.0}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    payload = asyncio.run(compare_stage(ctx))
    assert payload["matched_patterns"] == [("血糖升高模式", ["空腹血糖"])]
    assert payload["n_abnormal"] == 1


# ---------------- 注册表 ----------------

def test_stage_registry_and_order():
    assert set(stages_mod.STAGE_FUNCS) == {
        "parse", "normalize", "compare", "retrieve", "generate", "guardrail", "plan",
    }
    assert stages_mod.STAGE_ORDER == [
        "parse", "normalize", "compare", "retrieve", "generate", "guardrail", "plan",
    ]


# ---------------- retrieve / generate / plan(Task 12)----------------

def _jdict(code="GLU", name="空腹血糖", status="high", value_num=7.2, critical=False) -> dict:
    return {"indicator_code": code, "name": name, "value_num": value_num, "value_text": None,
            "unit": "mmol/L", "status": status, "ref_low": 3.9, "ref_high": 6.1,
            "critical": critical, "range_source": "report"}


def _compare_checkpoint(judgments, matched=None) -> dict:
    return {"n_abnormal": 1, "critical_count": 0, "matched_patterns": matched or [],
            "judgments": judgments}


class FakeRetriever:
    def __init__(self, out=None):
        self.out = out or []
        self.queries = []

    async def search(self, q):
        self.queries.append(q)
        return list(self.out)


def test_retrieve_stage_payload_placeholder_when_no_hits():
    judgments = [
        _jdict(code="GLU", status="high"),
        _jdict(code="CA", name="血钙", status="low", value_num=2.0),
        _jdict(code="WBC", name="白细胞", status="normal", value_num=5.2),
    ]
    retriever = FakeRetriever()
    deps = SimpleNamespace(
        settings=SimpleNamespace(retrieve_concurrency=2), retriever=retriever)
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": _compare_checkpoint(judgments)}, deps=deps)
    payload = asyncio.run(retrieve_stage(ctx))
    assert set(payload["evidence"]) == {"GLU", "CA"}  # normal 项不检索
    for key in ("GLU", "CA"):
        assert len(payload["evidence"][key]) == 1
        assert payload["evidence"][key][0]["source"] == "placeholder"
        assert set(payload["evidence"][key][0]) == {
            "text", "source", "entity_type", "entity_id", "title",
            "score", "rrf_score", "rrf_sources",
        }
    assert [(q.text, q.direction) for q in retriever.queries] == [
        ("空腹血糖 high 健康风险", "high"), ("血钙 low 健康风险", "low"),
    ]


def test_retrieve_stage_keeps_real_evidence_dict():
    retriever = FakeRetriever(out=[Evidence(text="血糖升高可能提示:糖尿病风险", source="kg",
                                            entity_type="indicator", entity_id="GLU",
                                            title="空腹血糖 知识图谱", score=None,
                                            rrf_score=0.0833, rrf_sources=["kg"])])
    deps = SimpleNamespace(settings=SimpleNamespace(retrieve_concurrency=2), retriever=retriever)
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": _compare_checkpoint([_jdict()])}, deps=deps)
    payload = asyncio.run(retrieve_stage(ctx))
    ev = payload["evidence"]["GLU"][0]
    assert ev["source"] == "kg" and ev["rrf_score"] == 0.0833
    assert ev["rrf_sources"] == ["kg"] and ev["title"] == "空腹血糖 知识图谱"


def _glu_json() -> dict:
    return {"meaning": "血糖偏高", "risks": ["糖尿病风险"], "advice_level": "recheck",
            "advice": "复查空腹血糖", "evidence_ids": []}


def _glu_evidence() -> dict:
    return {"text": "血糖升高可能提示:糖尿病风险", "source": "kg", "entity_type": "indicator",
            "entity_id": "GLU", "title": "空腹血糖 知识图谱", "score": None,
            "rrf_score": 0.0833, "rrf_sources": ["kg"]}


def test_generate_stage_assembles_doc_with_evidence_and_summary():
    judgments = [_jdict(), _jdict(code="XX", name="未知项", status="unknown", value_num=None)]
    kg = FakeKG(contexts={"GLU": IndicatorContext(code="GLU", name="空腹血糖")})
    llm = FakeLLM(json_result=_glu_json(), text_result=" 总体结论,请综合评估。 ")
    deps = SimpleNamespace(kg=kg, llms=SimpleNamespace(chat=llm))
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={
                           "compare": _compare_checkpoint(judgments),
                           "retrieve": {"evidence": {"GLU": [_glu_evidence()]}},
                       }, deps=deps)
    payload = asyncio.run(generate_stage(ctx))
    doc = payload["doc"]
    assert doc["degraded"] is False
    assert doc["summary"] == "总体结论,请综合评估。"
    assert doc["disclaimer"] and doc["items"][0]["name"] == "空腹血糖"
    assert doc["items"][0]["advice_level"] == "recheck"
    assert payload["interpretations"][0]["status"] == "high"
    # 调用序:先逐项 complete_json,后总评 chat;prompt 内含 KG 事实与 Evidence 文本
    assert [c[0] for c in llm.calls] == ["json", "chat"]
    content = llm.calls[0][1][0]["content"]
    assert "知识图谱事实" in content and "空腹血糖 知识图谱" in content


def test_generate_stage_llm_error_uses_fallbacks_no_raise():
    judgments = [_jdict()]
    kg = FakeKG()
    llm = FakeLLM(error=True)
    deps = SimpleNamespace(kg=kg, llms=SimpleNamespace(chat=llm))
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": _compare_checkpoint(judgments)}, deps=deps)
    payload = asyncio.run(generate_stage(ctx))  # 不抛:逐项与总评各自降级
    doc = payload["doc"]
    assert "线下咨询" in doc["items"][0]["advice"]  # 模板解读
    assert "项异常" in doc["summary"]  # template_summary
    assert doc["degraded"] is False  # generate 内部降级不置 doc.degraded(guardrail 管)


def test_plan_stage_followup_template_degraded_stays_on_followup_row():
    """LLM 润色失败 → 复查单 degraded=True 落 followup 行;解读行不并入该标志。"""
    judgments = [
        _jdict(status="critical_high", value_num=25, critical=True),
        _jdict(code="ALT", name="丙氨酸氨基转移酶", status="high", value_num=80),
    ]
    compare = _compare_checkpoint(judgments, matched=[("代谢异常模式", ["丙氨酸氨基转移酶"])])
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d",
           "degraded": False}
    llm = FakeLLM(error=True)  # 润色失败 → 模板原文回退
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm))
    db = FakeDB()
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, db=db,
                       deps=deps)
    payload = asyncio.run(plan_stage(ctx))
    assert payload == {"n_items": 3, "degraded": True}  # payload 反映复查单自身降级
    items = db.saved_followup["items"]
    assert items[0]["timeframe"] == "立即"  # 危急置顶
    assert items[2]["item"] == "组合模式复查:代谢异常模式"  # 模式条目以名称为载体
    assert "丙氨酸氨基转移酶" in items[2]["basis"]
    assert db.saved_followup["degraded"] is True
    # 无 guardrail checkpoint → 解读行 degraded=False(plan.degraded 不再并入)
    assert db.saved_interpretation["degraded"] is False


def test_plan_stage_smooth_ok_persists_plain_doc():
    judgments = [_jdict(code="ALT", name="丙氨酸氨基转移酶", status="high", value_num=80)]
    compare = _compare_checkpoint(judgments)
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d",
           "degraded": False}
    llm = FakeLLM(json_result={"0": "转氨酶升高,建议择期复查肝功能"})
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm))
    db = FakeDB()
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, db=db,
                       deps=deps)
    payload = asyncio.run(plan_stage(ctx))
    assert payload == {"n_items": 1, "degraded": False}
    assert db.saved_followup["items"][0]["basis"] == "转氨酶升高,建议择期复查肝功能"
    assert db.saved_followup["degraded"] is False
    assert db.saved_interpretation["degraded"] is False


def test_plan_stage_interpretation_degraded_only_from_guardrail():
    """guardrail degraded=True → 解读行 True;复查单润色成功(followup 行仍 False)。"""
    judgments = [_jdict(code="ALT", name="丙氨酸氨基转移酶", status="high", value_num=80)]
    compare = _compare_checkpoint(judgments)
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d",
           "degraded": False}
    llm = FakeLLM(json_result={"0": "转氨酶升高,建议择期复查肝功能"})  # 润色成功
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm))
    db = FakeDB()
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": compare, "generate": {"doc": doc},
                                    "guardrail": {"degraded": True}}, db=db, deps=deps)
    payload = asyncio.run(plan_stage(ctx))
    assert payload == {"n_items": 1, "degraded": False}
    assert db.saved_followup["degraded"] is False
    assert db.saved_interpretation["degraded"] is True  # 只随 guardrail 标志


def test_plan_stage_all_normal_report_not_degraded():
    """全正常报告(仅 normal/unknown 项)→ 空复查单,解读/复查两行均不降级。"""
    judgments = [
        _jdict(code="WBC", name="白细胞", status="normal", value_num=5.2),
        _jdict(code="XX", name="未知项", status="unknown", value_num=None),
    ]
    compare = _compare_checkpoint(judgments)
    doc = {"summary": "一切正常", "items": [], "advice_summary": "", "disclaimer": "d",
           "degraded": False}
    llm = FakeLLM(error=True)  # 空复查单不调 LLM,即便挂了也不影响
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm))
    db = FakeDB()
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, db=db,
                       deps=deps)
    payload = asyncio.run(plan_stage(ctx))
    assert payload == {"n_items": 0, "degraded": False}
    assert db.saved_followup == {"items": [], "degraded": False}
    assert db.saved_interpretation["degraded"] is False


# ---------------- guardrail 阶段 / plan 落库权威源(Task 13 评审修复轮)----------------

class FakeAuditSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class FakeAuditFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        s = FakeAuditSession()
        self.sessions.append(s)
        return s

    @property
    def events(self) -> list:
        return [e for s in self.sessions for e in s.added]


def _gdoc(summary: str, items: list[dict]) -> dict:
    return {"summary": summary, "items": items, "advice_summary": "a",
            "disclaimer": "【免责声明】本解读不构成医学诊断。", "degraded": False}


def _gitem(code: str, name: str, meaning: str, advice: str, status: str = "high") -> dict:
    return {"indicator_code": code, "name": name, "status": status,
            "value_text": "25 mmol/L(参考区间 3.9~6.1)", "meaning": meaning,
            "risks": [], "advice_level": "recheck", "advice": advice, "evidence_ids": []}


def _gctx(**kw) -> GuardrailContext:
    base = {"allowed_numbers": [25.0, 7.2, 3.9, 6.1, 40.0], "require_disclaimer": False,
            "require_critical_warning": False}
    base.update(kw)
    return GuardrailContext(**base)


def test_guardrail_stage_pass_through_no_llm_no_audit():
    """全 PASS 直通:doc 内容与 generate 一致,零 LLM 调用、零审计事件。"""
    judgments = [_jdict()]
    compare = _compare_checkpoint(judgments)
    items = [_gitem("GLU", "空腹血糖", "空腹血糖 7.2,高于上限 6.1,建议关注。",
                    "建议复查空腹血糖。")]
    doc = _gdoc("本次体检空腹血糖 7.2,高于上限 6.1,建议复查。", items)
    llm = FakeLLM()
    factory = FakeAuditFactory()
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm),
                           session_factory=factory)
    ctx = StageContext(task_id="t1", report={"id": "r1", "age": 40.0},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, deps=deps)
    payload = asyncio.run(guardrail_stage(ctx))
    assert payload["degraded"] is False
    assert payload["doc"]["summary"] == doc["summary"]
    assert payload["doc"]["items"] == doc["items"]
    assert payload["findings"] == [{"part": "summary", "degraded": False},
                                   {"part": "空腹血糖", "degraded": False}]
    assert llm.calls == [] and factory.sessions == []


def test_guardrail_stage_summary_suspect_review_fail_regen_passes_with_feedback():
    """总评 SUSPECT(越界数值)→ 审核失败 → 重生成注入 feedback 通过 → 不降级。"""
    judgments = [_jdict()]
    compare = _compare_checkpoint(judgments)
    doc = _gdoc("血糖 9.9 mmol/L,高于上限,请结合临床随访。", [])
    llm = FakeLLM(json_result={"passed": False, "issues": ["数值与报告不一致"]},
                  text_result="总体结论:血糖升高,建议定期复查并随访。")
    factory = FakeAuditFactory()
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm),
                           session_factory=factory)
    ctx = StageContext(task_id="t1", report={"id": "r1", "age": 40.0},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, deps=deps)
    payload = asyncio.run(guardrail_stage(ctx))
    assert payload["degraded"] is False
    assert payload["doc"]["summary"] == "总体结论:血糖升高,建议定期复查并随访。"
    # generate checkpoint 只读:总评改写不回流(评审 Critical-1b)
    assert ctx.checkpoints["generate"]["doc"]["summary"] == "血糖 9.9 mmol/L,高于上限,请结合临床随访。"
    assert [e.event_type for e in factory.events] == ["guardrail_suspect", "review_failed"]
    # 审计事件带 report/task 关联 id(评审 I-3)
    assert factory.events[0].report_id == "r1" and factory.events[0].task_id == "t1"
    # feedback(未过审原因)注入总评重生成消息(评审 I-2④)
    chat_contents = [m[0]["content"] for kind, m in llm.calls if kind == "chat"]
    assert any("上次护栏未过审原因" in c and "越界数值: 9.9" in c for c in chat_contents)


def test_guardrail_stage_critical_item_advice_violation_degrades_clears_advice():
    """违规在 advice → 重生成仍违规 → 降级:meaning 换危急降级文本(含"尽快就医"词元)、
    advice 清空;降级项复合文本在项级 ctx(危急强提醒)复检 PASS。评审 I-1/②③ 回归。"""
    judgments = [_jdict(status="critical_high", value_num=25, critical=True)]
    compare = _compare_checkpoint(judgments)
    items = [_gitem("GLU", "空腹血糖", "血糖 25,显著升高,已达危急值水平。",
                    "建议每日服用二甲双胍 500mg 控制血糖。", status="critical_high")]
    doc = _gdoc("本次体检空腹血糖 25,达危急值,请尽快就医复查。", items)
    llm = FakeLLM(json_result={"meaning": "血糖偏高。", "risks": [],
                               "advice_level": "urgent", "advice": "继续服用二甲双胍 500mg。",
                               "evidence_ids": []})
    factory = FakeAuditFactory()
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm),
                           session_factory=factory)
    ctx = StageContext(task_id="t1", report={"id": "r1", "age": 40.0},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, deps=deps)
    payload = asyncio.run(guardrail_stage(ctx))
    assert payload["degraded"] is True
    assert payload["doc"]["advice_summary"] == "所有异常项请以线下医师意见为准。"
    item = payload["doc"]["items"][0]
    assert item["advice"] == ""  # 降级写回清空 advice,不留违规原文(评审 I-1)
    assert item["meaning"] == "空腹血糖达危急值水平,请尽快就医,具体请以线下医师意见为准。"
    # generate checkpoint 只读:item dict 不被原地改写(评审 Critical-1b)
    gen_item = ctx.checkpoints["generate"]["doc"]["items"][0]
    assert gen_item["meaning"] == "血糖 25,显著升高,已达危急值水平。"
    assert gen_item["advice"] == "建议每日服用二甲双胍 500mg 控制血糖。"
    assert [e.event_type for e in factory.events] == ["guardrail_block", "degraded_output"]
    # 回归:降级项复合文本(meaning/risks/advice 三槽口径,F1)在项级 ctx(危急强提醒)
    # 下必须 PASS;risks 已清空、advice 为空 —— 不留 LLM 违规原文
    assert item["risks"] == []
    r = rule_guardrail(item_guardrail_text(item["meaning"], item["risks"], item["advice"]),
                       _gctx(require_critical_warning=True))
    assert r.verdict == Verdict.PASS


def test_guardrail_stage_item_block_regen_pass_writes_back():
    """项 BLOCK(诊断用语在 advice)→ 重生成注入 feedback 通过 → meaning/advice 双双写回。"""
    judgments = [_jdict()]
    compare = _compare_checkpoint(judgments)
    items = [_gitem("GLU", "空腹血糖", "血糖 7.2,偏高,建议关注。",
                    "您患有糖尿病,建议用药治疗。")]
    doc = _gdoc("本次体检空腹血糖 7.2,高于上限 6.1,建议复查。", items)
    llm = FakeLLM(json_result={"meaning": "血糖偏高,建议饮食控制。", "risks": [],
                               "advice_level": "recheck", "advice": "建议复查空腹血糖。",
                               "evidence_ids": []})
    factory = FakeAuditFactory()
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm),
                           session_factory=factory)
    ctx = StageContext(task_id="t1", report={"id": "r1", "age": 40.0},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, deps=deps)
    payload = asyncio.run(guardrail_stage(ctx))
    assert payload["degraded"] is False
    item = payload["doc"]["items"][0]
    assert item["meaning"] == "血糖偏高,建议饮食控制。"
    assert item["advice"] == "建议复查空腹血糖。"
    assert [e.event_type for e in factory.events] == ["guardrail_block"]
    json_contents = [m[0]["content"] for kind, m in llm.calls if kind == "json"]
    assert any("上次护栏未过审原因" in c and "诊断用语" in c for c in json_contents)


def test_plan_stage_interpretation_doc_guardrail_authoritative_generate_fallback():
    """解读行 doc:guardrail checkpoint 存在则以其为权威源(含降级 summary);否则退回 generate。"""
    judgments = [_jdict(code="ALT", name="丙氨酸氨基转移酶", status="high", value_num=80)]
    compare = _compare_checkpoint(judgments)
    llm = FakeLLM(json_result={"0": "转氨酶升高,建议择期复查肝功能"})
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm))
    gen_doc = {"summary": "generate 原文总评", "items": [], "advice_summary": "a0",
               "disclaimer": "d0", "degraded": False}
    gr_doc = {"summary": "降级版:仅数值对照。", "items": [], "advice_summary": "a1",
              "disclaimer": "d0", "degraded": True}
    db = FakeDB()
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": compare, "generate": {"doc": gen_doc},
                                    "guardrail": {"doc": gr_doc, "degraded": True,
                                                  "findings": []}},
                       db=db, deps=deps)
    asyncio.run(plan_stage(ctx))
    assert db.saved_interpretation["summary"] == "降级版:仅数值对照。"  # guardrail 权威
    assert db.saved_interpretation["advice_summary"] == "a1"
    assert db.saved_interpretation["degraded"] is True

    db2 = FakeDB()
    ctx2 = StageContext(task_id="t1", report={"id": "r1"},
                        checkpoints={"compare": compare, "generate": {"doc": gen_doc}},
                        db=db2, deps=deps)
    asyncio.run(plan_stage(ctx2))
    assert db2.saved_interpretation["summary"] == "generate 原文总评"  # 无 guardrail → 兜底
    assert db2.saved_interpretation["degraded"] is False


# ================ F1/F3/F6 修复回归(final review 修复轮)================

def test_guardrail_stage_risk_slot_violation_degrades_and_clears_risks():
    """F1:违规表述只出现在 risks(LLM 自由文本槽位)→ 同样 BLOCK;重生成仍违规 → 降级,
    risks/advice 清空、meaning 换模板,复合文本复检 PASS —— risks 不再旁路。"""
    judgments = [_jdict()]
    compare = _compare_checkpoint(judgments)
    items = [_gitem("GLU", "空腹血糖", "血糖偏高,建议关注。", "建议复查空腹血糖。")]
    items[0]["risks"] = ["长期血糖偏高可能提示糖尿病风险", "可考虑服用二甲双胍控制"]
    doc = _gdoc("本次体检空腹血糖偏高,建议复查。", items)
    llm = FakeLLM(json_result={"meaning": "血糖偏高。", "risks": ["建议服用阿司匹林预防"],
                               "advice_level": "recheck", "advice": "建议复查空腹血糖。",
                               "evidence_ids": []})
    factory = FakeAuditFactory()
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm),
                           session_factory=factory)
    ctx = StageContext(task_id="t1", report={"id": "r1", "age": 40.0},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, deps=deps)
    payload = asyncio.run(guardrail_stage(ctx))
    assert payload["degraded"] is True
    item = payload["doc"]["items"][0]
    assert item["risks"] == []  # 降级后不留 LLM 违规原文(F1)
    assert item["advice"] == ""
    assert item["meaning"] == "空腹血糖检测结果请以线下医师意见为准。"
    assert [e.event_type for e in factory.events] == ["guardrail_block", "degraded_output"]
    r = rule_guardrail(item_guardrail_text(item["meaning"], item["risks"], item["advice"]),
                       _gctx())
    assert r.verdict == Verdict.PASS


def test_guardrail_stage_regen_pass_replaces_meaning_risks_advice_all_three():
    """F1:违规在 advice → 重生成干净(含新 risks)→ 写回三槽整体替换,消除
    "新 meaning/advice + 旧 risks"错位(旧 risks 若含违规,重生成复检未覆盖即绕过)。"""
    judgments = [_jdict()]
    compare = _compare_checkpoint(judgments)
    items = [_gitem("GLU", "空腹血糖", "血糖偏高,建议关注。", "您患有糖尿病,建议用药治疗。")]
    items[0]["risks"] = ["需警惕冠心病风险"]
    doc = _gdoc("本次体检空腹血糖偏高,建议复查。", items)
    regen_risks = ["建议结合糖化血红蛋白评估"]
    llm = FakeLLM(json_result={"meaning": "血糖偏高,建议饮食控制。", "risks": regen_risks,
                               "advice_level": "recheck", "advice": "建议复查空腹血糖。",
                               "evidence_ids": []})
    factory = FakeAuditFactory()
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm),
                           session_factory=factory)
    ctx = StageContext(task_id="t1", report={"id": "r1", "age": 40.0},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, deps=deps)
    payload = asyncio.run(guardrail_stage(ctx))
    assert payload["degraded"] is False
    item = payload["doc"]["items"][0]
    assert item["meaning"] == "血糖偏高,建议饮食控制。"
    assert item["risks"] == regen_risks  # 新 risks 写入,旧 risks 不残留
    assert item["advice"] == "建议复查空腹血糖。"
    assert [e.event_type for e in factory.events] == ["guardrail_block"]
    # 重生成复检覆盖三槽:新 risks 若含违规,复合文本即 BLOCK,不会整体放行
    r = rule_guardrail(item_guardrail_text(item["meaning"], ["建议服用阿司匹林"],
                                           item["advice"]), _gctx())
    assert r.verdict == Verdict.BLOCK


def test_retrieve_stage_placeholder_emits_retrieval_fallback_audit():
    """F6(b):检索无命中 → 占位证据(降级)记审计 retrieval_fallback(带项名/report/task)。"""
    retriever = FakeRetriever()
    factory = FakeAuditFactory()
    deps = SimpleNamespace(
        settings=SimpleNamespace(retrieve_concurrency=2), retriever=retriever,
        session_factory=factory)
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": _compare_checkpoint([_jdict()])}, deps=deps)
    payload = asyncio.run(retrieve_stage(ctx))
    assert payload["evidence"]["GLU"][0]["source"] == "placeholder"
    assert [e.event_type for e in factory.events] == ["retrieval_fallback"]
    assert factory.events[0].payload == {"item": "GLU"}
    assert factory.events[0].report_id == "r1" and factory.events[0].task_id == "t1"


def test_parse_stage_rerun_replaces_raw_rows_single_batch(monkeypatch):
    """F3:parse 重跑先删该 report 旧 raw 行再写 —— 崩溃窗口恢复不累积重复行。"""
    items = [_raw("GLU", 6.8, "mmol/L", "3.9-6.1")]
    out = ParseOutput(items=items, meta=ReportMeta(sex="male", age=40.0), method="unstructured")

    async def fake_parse(file_path, source, settings, llms):
        return out

    monkeypatch.setattr(stages_mod, "parse_report", fake_parse)
    db = FakeDB()
    deps = SimpleNamespace(settings=object(), llms=object())
    report = {"id": "r1", "source": "pdf", "file_path": "/tmp/r.pdf"}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=deps)
    asyncio.run(parse_stage(ctx))
    asyncio.run(parse_stage(ctx))
    assert db.delete_raw_calls == ["r1", "r1"]  # 每次写前先删
    assert len(db.raw_store) == 1  # 单批行,不翻倍
    assert db.raw_store == items


def test_normalize_stage_rerun_keeps_single_batch():
    """F3:normalize 重跑(崩溃恢复/词典升级后派生层重跑)先删旧行,不累积重复行。"""
    raws = [_raw("空腹血糖", 6.8, "mmol/L", "3.9-6.1")]
    converted = [
        NormalizedItem(
            raw_index=0, name="空腹血糖", indicator_code="GLU",
            value_text="6.8", value_num=122.4, unit="mg/dL",
            raw_value_num=6.8, raw_unit="mmol/L", ref_range_text="3.9-6.1",
            range_from="report",
        )
    ]
    norm = FakeNormalizer(out=converted)
    deps = SimpleNamespace(normalizer=norm, llms=SimpleNamespace(chat=object()))
    db = FakeDB()
    db.raw_items = raws
    ctx = StageContext(task_id="t1", report={"id": "r1", "source": "pdf"}, db=db, deps=deps)
    asyncio.run(normalize_stage(ctx))
    asyncio.run(normalize_stage(ctx))
    assert db.delete_norm_calls == ["r1", "r1"]
    assert len(db.norm_store) == 1  # 单批行
    assert db.norm_store[0].value_num == 122.4


def test_plan_stage_rerun_deletes_old_rows_no_unique_collision():
    """F3:plan 落库前先删同 task_id 旧行 —— interpretations/followup_plans.task_id 唯一,
    崩溃重跑不撞 IntegrityError;每 task 只留最新一批。"""
    judgments = [_jdict(code="ALT", name="丙氨酸氨基转移酶", status="high", value_num=80)]
    compare = _compare_checkpoint(judgments)
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d",
           "degraded": False}
    llm = FakeLLM(error=True)
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm))
    db = FakeDB()
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, db=db,
                       deps=deps)
    asyncio.run(plan_stage(ctx))
    asyncio.run(plan_stage(ctx))  # 重跑(首轮落库后崩溃,plan checkpoint 未写)
    assert db.delete_interpretation_calls == ["t1", "t1"]
    assert db.delete_followup_calls == ["t1", "t1"]
    assert len(db.interp_store) == 1 and len(db.followup_store) == 1


def test_plan_stage_smooth_basis_guardrail_fallback_template_and_audit():
    """F1(b)/F6(c):LLM 润色 basis 含诊断用语 → 该条回退模板原文、复查单降级,
    审计 followup_template_fallback;解读行不受影响。"""
    judgments = [_jdict(code="ALT", name="丙氨酸氨基转移酶", status="high", value_num=80)]
    compare = _compare_checkpoint(judgments)
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d",
           "degraded": False}
    llm = FakeLLM(json_result={"0": "转氨酶升高,您患有脂肪肝,建议用药治疗"})
    factory = FakeAuditFactory()
    deps = SimpleNamespace(kg=FakeKG(), llms=SimpleNamespace(chat=llm),
                           session_factory=factory)
    db = FakeDB()
    ctx = StageContext(task_id="t1", report={"id": "r1"},
                       checkpoints={"compare": compare, "generate": {"doc": doc}}, db=db,
                       deps=deps)
    payload = asyncio.run(plan_stage(ctx))
    assert payload == {"n_items": 1, "degraded": True}
    assert db.saved_followup["degraded"] is True
    assert db.saved_followup["items"][0]["basis"] == "丙氨酸氨基转移酶 判定升高,建议复查"
    assert db.saved_interpretation["degraded"] is False  # 复查单自身降级不升任务级
    assert [e.event_type for e in factory.events] == ["followup_template_fallback"]
    assert factory.events[0].report_id == "r1" and factory.events[0].task_id == "t1"
