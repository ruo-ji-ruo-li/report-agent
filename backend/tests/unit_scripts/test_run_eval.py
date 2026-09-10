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


# ---------------------------------------------------------------------------
# QA 夹具建行路径回归(修复:QA 夹具错配)。真实 apply_judgments 按 id(uuid4)排序逐行
# 配对,与落库顺序无关 —— 这里让 get_normalized 逆序返回模拟"读回序 ≠ 落库序",
# 锁定 runner 必须按回读顺序构造判定,否则参考区间/状态会错配到别的项目
# (实测 r01 的 HDL-C 0.8 被配上 GLU 的区间 3.9~6.1 与 high 判定)。
# ---------------------------------------------------------------------------


class _FixtureDB:
    """最小 DataAccess 替身:仅覆盖 QA 夹具建行路径;读回序 = 落库逆序。"""

    def __init__(self):
        self._n = 0
        self.rows: dict[str, list] = {}    # rid → 读回序的 NormalizedItem
        self.judged: dict[str, list] = {}  # rid → [(name, status, ref_low, ref_high)]

    async def create_report(self, source, file_path, meta):
        self._n += 1
        rid = f"fixture-{self._n}"
        self.rows[rid] = []
        return rid

    async def save_raw_items(self, rid, items):
        pass

    async def save_normalized(self, rid, items):
        self.rows[rid] = list(reversed(items))  # 读回序 ≠ 落库序

    async def get_normalized(self, rid):
        return list(self.rows[rid])

    async def apply_judgments(self, rid, judgments):
        rows = self.rows[rid]
        if len(rows) != len(judgments):
            raise ValueError("行数与判定数不一致")
        out = []
        for i, (row, j) in enumerate(zip(rows, judgments, strict=True)):
            # 与真实 apply_judgments 守卫同口径
            if (row.name, row.value_text, row.value_num, row.unit) != \
                    (j.name, j.value_text, j.value_num, j.unit):
                raise ValueError(f"判定与归一化行错位(第 {i} 行)")
            out.append((row.name, j.status.value, j.ref_low, j.ref_high))
        self.judged[rid] = out


class _StubGraph:
    """假 chat agent:直接给固定回答,不触 LLM。"""

    async def ainvoke(self, state, config=None):
        return {"messages": [SimpleNamespace(content="甘油三酯 3.1 mmol/L", tool_calls=[])]}


async def test_qa_fixture_rows_judged_in_storage_order(tmp_path, monkeypatch):
    """夹具行的判定必须与落库行一一对应(修复前 runner 传 gt 顺序 → 错位 → 本测试红)。"""
    from report_agent.eval import runner

    reports = _write_fixture(tmp_path)
    _point_runner_at_tmp(monkeypatch, tmp_path, reports, tmp_path / "no_baseline.json")
    qa = tmp_path / "qa_pairs.jsonl"
    qa.write_text(json.dumps({"question": "甘油三酯多少?", "must_contain": ["3.1"]},
                             ensure_ascii=False), "utf-8")
    monkeypatch.setattr(runner, "QA_FILE", qa)
    monkeypatch.setattr("report_agent.chat.agent.build_chat_agent",
                        lambda deps, rid, checkpointer=None: _StubGraph())
    deps = _fake_deps()
    deps.db = _FixtureDB()

    assert await runner.run(deps) == 0
    assert deps.db.judged, "QA 夹具未建行(修复前:判定错位 → 守卫抛错 → 被 except 吞掉)"
    (rows,) = deps.db.judged.values()
    got = {name: (status, lo, hi) for name, status, lo, hi in rows}
    # 每项的判定/区间必须来自它自己:GLU 4.9∈[3.9,6.1] normal;TG 3.1>1.7 high
    assert got["空腹血糖"] == ("normal", 3.9, 6.1)
    assert got["甘油三酯"] == ("high", 0.4, 1.7)


# ---------------------------------------------------------------------------
# 检索口径对齐(清理死条件 + 与 pipeline.retrieve_stage 同 query 口径):
# 修复前 text 缺 "健康风险" 后缀、direction 恒 None → KG 路是生产的超集
# (high 项混入"降低可能提示"反向证据);原 `e.source != "placeholder"` 死条件
# (placeholder 只在 retrieve_stage 空命中分支注入)一并简化为"检索非空即覆盖"。
# ---------------------------------------------------------------------------


class _RecordingRetriever:
    """记录 query、恒返回 1 条 kg 证据(锁 query 口径与"检索非空即覆盖")。"""

    def __init__(self):
        self.queries = []

    async def search(self, q):
        from report_agent.retrieval.hybrid import Evidence

        self.queries.append(q)
        return [Evidence(text="甘油三酯升高可能提示:胰腺炎风险", source="kg")]


async def test_eval_retrieval_queries_match_pipeline_caliber(tmp_path, monkeypatch):
    """runner 的检索必须走 build_item_query —— text/direction 与 retrieve_stage 同口径。"""
    reports = _write_fixture(tmp_path)
    _point_runner_at_tmp(monkeypatch, tmp_path, reports, tmp_path / "no_baseline.json")
    from report_agent.eval import runner

    deps = _fake_deps()
    deps.retriever = _RecordingRetriever()
    assert await runner.run(deps) == 0
    # 覆盖率循环 1 次 + LLM 维度循环 1 次(TG 是夹具里唯一异常项)
    assert [(q.text, q.direction) for q in deps.retriever.queries] == [
        ("甘油三酯 high 健康风险", "high")] * 2
    # 清理死条件后的覆盖口径:检索非空即覆盖
    result = json.loads((tmp_path / "result.json").read_text("utf-8"))
    assert result["evidence_coverage"] == 1.0


async def test_eval_real_case_queries_match_pipeline_caliber(tmp_path, monkeypatch):
    """真实 case 分支的镜像检索块同样走 build_item_query(覆盖率 + LLM 维度各一次)。"""
    reports = _write_fixture(tmp_path)
    _point_runner_at_tmp(monkeypatch, tmp_path, reports, tmp_path / "no_baseline.json")
    from report_agent.eval import real_case as real_case_mod
    from report_agent.eval import runner
    from report_agent.parsing import table_extractor

    gt = real_case_mod.RealCaseGT(meta={"sex": "male", "age": 50}, items=[
        real_case_mod.RealGTItem(name="甘油三酯", code="TG", status="high",
                                 value_text="3.10", value_num=3.1, unit="mmol/L",
                                 ref_range_text="0.45-1.81")])
    # run() 内是调用时 from ... import → 打源模块属性即可命中(同 rule_guardrail 先例)
    monkeypatch.setattr(real_case_mod, "load_real_case", lambda real_dir: ("# 表", gt))
    monkeypatch.setattr(table_extractor, "parse_merged_tables", lambda md: ([], []))
    deps = _fake_deps()
    deps.retriever = _RecordingRetriever()
    assert await runner.run(deps) == 0
    # 合成 coverage 1 + 真实 coverage 1 + 合成 LLM 1 + 真实 LLM 1(TG 是唯一异常项)
    assert [(q.text, q.direction) for q in deps.retriever.queries] == [
        ("甘油三酯 high 健康风险", "high")] * 4
