"""run_eval.py CLI 与 runner 入口单测(不连库、不跑全量)。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_RUN_EVAL = BACKEND_ROOT / "scripts" / "run_eval.py"

_spec = importlib.util.spec_from_file_location("run_eval", _RUN_EVAL)
run_eval_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval_script)


def test_parse_args_defaults():
    args = run_eval_script.parse_args([])
    assert args.update_baseline is False
    assert args.max_llm_reports == 5


def test_parse_args_overrides():
    args = run_eval_script.parse_args(["--max-llm-reports", "3", "--update-baseline"])
    assert args.max_llm_reports == 3
    assert args.update_baseline is True


async def test_run_exits_2_on_empty_reports(tmp_path, monkeypatch):
    """空评测集 → 返回 2,不触碰 deps(评审 I-④:禁止评测与冻结基线)。"""
    from report_agent.eval import runner

    monkeypatch.setattr(runner, "REPORTS_DIR", tmp_path)
    monkeypatch.setattr(runner, "TRACES_DIR", tmp_path / "traces")
    assert await runner.run(None) == 2
    # (最终评审加固:TraceSink 移到空集检查之后 → exit 2 不得残留空 trace 目录)
    assert not (tmp_path / "traces").exists()


def test_run_meta_shape():
    from report_agent.eval import runner

    meta = runner.run_meta("20260909-120000", {"max_llm_reports": 5}, {"f1": 0.9})
    assert meta["run_id"] == "20260909-120000"
    assert meta["git_commit"]  # 非空(git 环境)或 "unknown"
    assert meta["metrics"] == {"f1": 0.9}
    assert "finished_at" in meta


# ---------------------------------------------------------------------------
# 门禁三出口(exit 1)单测(最终评审建议):fake deps 覆盖 runner.run 全流程,
# 不连 postgres/neo4j/milvus/LLM —— LLM 全失败走模板与 judge 降级,QA 文件不存在跳过。
# ---------------------------------------------------------------------------

# 夹具:gt 与规则层判定一致(GLU 4.9∈[3.9,6.1] normal;TG 3.1>1.7 high)→ 默认 rule_accuracy 1.0
_FIXTURE = {
    "id": "r01",
    "meta": {"sex": "male", "age": 50},
    "raw_items": [
        {"name": "空腹血糖", "value_text": "4.9", "value_num": 4.9, "unit": "mmol/L",
         "ref_range_text": "3.9-6.1", "abnormal_flag": "", "section": "生化"},
        {"name": "甘油三酯", "value_text": "3.1", "value_num": 3.1, "unit": "mmol/L",
         "ref_range_text": "0.4-1.7", "abnormal_flag": "↑", "section": "血脂"},
    ],
    "gt_codes": {"空腹血糖": "GLU", "甘油三酯": "TG"},
    "gt_statuses": {"GLU": "normal", "TG": "high"},
}


class _FakeNormalizer:
    """全部 code=null → normalize_f1 偏低;规则层维度直接喂 gt,不受影响。"""

    async def normalize(self, raws):
        from report_agent.parsing.schemas import NormalizedItem

        return [NormalizedItem(
            raw_index=i, name=r.name, indicator_code=None,
            value_text=r.value_text, value_num=r.value_num, unit=r.unit,
            raw_value_num=r.value_num, raw_unit=r.unit,
            ref_range_text=r.ref_range_text, range_from="report",
        ) for i, r in enumerate(raws)]


class _FakeKG:
    def range_specs(self, code):
        return []

    def indicator_context(self, code):
        return None


class _FakeRetriever:
    async def search(self, query):
        return []


class _FakeChat:
    """LLM 全失败:解读/总评走模板,judge 单元降级(聚合 None),不阻断流程。"""

    async def chat(self, *a, **k):
        from report_agent.llm.client import LLMError

        raise LLMError("fake llm down")

    async def complete_json(self, *a, **k):
        from report_agent.llm.client import LLMError

        raise LLMError("fake llm down")


class _RaisingDB:
    """任何 DB 调用失败 → QA fixture 建行失败仅告警(本组测试 QA_FILE 不存在)。"""

    def __getattr__(self, name):
        async def _raise(*a, **k):
            raise RuntimeError("no postgres in unit test")

        return _raise


def _fake_deps():
    return SimpleNamespace(normalizer=_FakeNormalizer(), kg=_FakeKG(),
                           retriever=_FakeRetriever(),
                           llms=SimpleNamespace(chat=_FakeChat()), db=_RaisingDB())


def _write_fixture(tmp_path: Path, gt_statuses: dict | None = None) -> Path:
    reports = tmp_path / "reports"
    reports.mkdir()
    data = dict(_FIXTURE)
    if gt_statuses is not None:
        data["gt_statuses"] = gt_statuses
    (reports / "r01.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    return reports


def _point_runner_at_tmp(monkeypatch, tmp_path: Path, reports: Path, baseline: Path) -> None:
    """全部路径改指 tmp:结果/trace 落 tmp,QA 不存在跳过,真实 case 目录缺失跳过。"""
    from report_agent.eval import runner

    monkeypatch.setattr(runner, "REPORTS_DIR", reports)
    monkeypatch.setattr(runner, "QA_FILE", tmp_path / "no_qa_pairs.jsonl")
    monkeypatch.setattr(runner, "RESULT_FILE", tmp_path / "result.json")
    monkeypatch.setattr(runner, "TRACES_DIR", tmp_path / "traces")
    monkeypatch.setattr(runner, "BASELINE_FILE", baseline)
    monkeypatch.setattr(runner, "REAL_DIR", tmp_path / "no_real")


async def test_run_exits_1_on_rule_accuracy_below_one(tmp_path, monkeypatch):
    """规则层 < 1.0 → exit 1(GLU 实际 normal,gt_statuses 谎报 high)。"""
    reports = _write_fixture(tmp_path, gt_statuses={"GLU": "high", "TG": "high"})
    _point_runner_at_tmp(monkeypatch, tmp_path, reports, tmp_path / "no_baseline.json")
    from report_agent.eval import runner

    assert await runner.run(_fake_deps()) == 1


async def test_run_exits_1_on_safety_violation(tmp_path, monkeypatch):
    """安全违规 > 0 → exit 1(规则层先满足 1.0,护栏被替身为恒 BLOCK)。"""
    reports = _write_fixture(tmp_path)
    _point_runner_at_tmp(monkeypatch, tmp_path, reports, tmp_path / "no_baseline.json")
    from report_agent.eval import runner
    from report_agent.guardrails import rules as rules_mod

    def _block(text, ctx):
        return rules_mod.GuardrailResult(rules_mod.Verdict.BLOCK, ["诊断用语: 确诊"])

    # runner.run 在函数内 from ... import rule_guardrail → 打模块属性即可命中
    monkeypatch.setattr(rules_mod, "rule_guardrail", _block)
    assert await runner.run(_fake_deps()) == 1


async def test_run_exits_1_on_baseline_regression(tmp_path, monkeypatch):
    """指标低于基线容差 → exit 1(fake normalizer 全 null → normalize_f1 0.0 < 1.0-0.05)。"""
    reports = _write_fixture(tmp_path)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"normalize_f1": 1.0}), "utf-8")
    _point_runner_at_tmp(monkeypatch, tmp_path, reports, baseline)
    from report_agent.eval import runner

    assert await runner.run(_fake_deps()) == 1
