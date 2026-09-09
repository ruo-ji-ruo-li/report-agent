"""run_eval.py CLI 与 runner 入口单测(不连库、不跑全量)。"""
import importlib.util
from pathlib import Path

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
    assert await runner.run(None) == 2


def test_run_meta_shape():
    from report_agent.eval import runner

    meta = runner.run_meta("20260909-120000", {"max_llm_reports": 5}, {"f1": 0.9})
    assert meta["run_id"] == "20260909-120000"
    assert meta["git_commit"]  # 非空(git 环境)或 "unknown"
    assert meta["metrics"] == {"f1": 0.9}
    assert "finished_at" in meta
