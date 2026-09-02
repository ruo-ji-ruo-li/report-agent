"""parse/normalize/compare 三阶段单测:FakeDB/FakeKG/FakeNormalizer,零 DB/零网络。

锁定语义(brief 质量注意):
- parse_stage: manual 直通校验;非 manual needs_meta=sex/age 缺失 → 暂停信号;不支持来源 ValueError
- normalize_stage: 换算后标准值入库但 raw 原值(raw_value_num/raw_unit)不丢
- compare_stage: sex/age 缺失抛 ValueError 引导补录;payload n_abnormal/critical_count/matched_patterns
"""
import asyncio
from types import SimpleNamespace

import pytest

from report_agent.knowledge.kg_client import PatternCriterion, PatternSpec, RangeSpec
from report_agent.parsing.parse_report import ParseOutput
from report_agent.parsing.schemas import NormalizedItem, RawReportItem, ReportMeta
from report_agent.pipeline import stages as stages_mod
from report_agent.pipeline.stages import (
    StageContext,
    compare_stage,
    normalize_stage,
    parse_stage,
)


class FakeDB:
    """记录各写调用;raw/normalized 由测试预置。"""

    def __init__(self):
        self.raw_items: list[RawReportItem] = []
        self.normalized: list[NormalizedItem] = []
        self.updated_meta = None
        self.saved_raw = None
        self.saved_norm = None
        self.applied = None

    async def get_raw_items(self, report_id):
        return self.raw_items

    async def get_normalized(self, report_id):
        return self.normalized

    async def update_report_meta(self, report_id, meta):
        self.updated_meta = meta

    async def save_raw_items(self, report_id, items):
        self.saved_raw = items

    async def save_normalized(self, report_id, items):
        self.saved_norm = items

    async def apply_judgments(self, report_id, judgments):
        self.applied = judgments


class FakeKG:
    def __init__(self, specs=None, patterns=None):
        self.specs = specs or {}
        self.patterns = patterns or []
        self.range_calls: list[str] = []

    def range_specs(self, code):
        self.range_calls.append(code)
        return self.specs.get(code, [])

    def all_patterns(self):
        return self.patterns


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
        section="生化", name=name, value_text=str(value_num) if value_num is not None else None,
        value_num=value_num, unit=unit, ref_range_text=ref_range, abnormal_flag=None,
    )


# ---------------- parse ----------------

def test_parse_stage_manual_validates_existing_items():
    db = FakeDB()
    db.raw_items = [_raw("WBC", 5.2, "10^9/L", "3.5-9.5")]
    report = {"id": "r1", "source": "manual", "file_path": None}
    ctx = StageContext(task_id="t1", report=report, db=db, deps=object())
    payload = asyncio.run(parse_stage(ctx))
    assert payload == {"method": "manual", "n_items": 1, "needs_meta": False}


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
            raw_index=0, section="生化", name="空腹血糖", indicator_code="GLU",
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
        raw_index=0, section="生化", name="空腹血糖", indicator_code="GLU",
        value_text="12.0", value_num=12.0, unit="mmol/L",
        raw_value_num=12.0, raw_unit="mmol/L", ref_range_text="3.9-6.1",
        range_from="report",
    )


def _unmapped_item() -> NormalizedItem:
    return NormalizedItem(
        raw_index=1, section="其他", name="未知检验项", indicator_code=None,
        value_text="+", value_num=None, unit=None,
        raw_value_num=None, raw_unit=None, ref_range_text=None, range_from=None,
    )


def _alt_item() -> NormalizedItem:
    return NormalizedItem(
        raw_index=2, section="生化", name="丙氨酸氨基转移酶", indicator_code="ALT",
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
    assert set(stages_mod.STAGE_FUNCS) == {"parse", "normalize", "compare"}
    assert stages_mod.STAGE_ORDER == [
        "parse", "normalize", "compare", "retrieve", "generate", "guardrail", "plan",
    ]
