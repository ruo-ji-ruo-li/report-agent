"""serve.py 一键启动脚本的单元测试(不连库、不真正启动服务)。

scripts/ 不在包内, 用 importlib 按文件路径加载;
build_alembic_config 只断言配置指向与解析结果, 不触发 upgrade。
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SERVE = BACKEND_ROOT / "scripts" / "serve.py"

_spec = importlib.util.spec_from_file_location("serve", _SERVE)
serve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serve)


def test_build_alembic_config_resolves_script_location_regardless_of_cwd(tmp_path, monkeypatch):
    """script_location=%(here)s/alembic 应相对 ini 文件自身定位:
    切换到任意 CWD 后, 配置仍指向 backend/alembic.ini 与 backend/alembic。"""
    monkeypatch.chdir(tmp_path)
    cfg = serve.build_alembic_config(BACKEND_ROOT)
    assert Path(cfg.config_file_name) == BACKEND_ROOT / "alembic.ini"
    assert Path(ScriptDirectory.from_config(cfg).dir) == BACKEND_ROOT / "alembic"


def test_parse_args_defaults():
    args = serve.parse_args([])
    assert args.host == "127.0.0.1"
    assert args.port == 8000
    assert args.skip_migration is False


def test_parse_args_overrides():
    args = serve.parse_args(["--host", "0.0.0.0", "--port", "9000", "--skip-migration"])
    assert args.host == "0.0.0.0"
    assert args.port == 9000
    assert args.skip_migration is True


def test_use_selector_loop_on_windows_switches_policy(monkeypatch):
    """win32 分支: policy 应切为 WindowsSelectorEventLoopPolicy(psycopg async 只支持它);
    monkeypatch sys.platform 使断言在任何平台可执行。"""
    if not hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        pytest.skip("WindowsSelectorEventLoopPolicy 仅 Windows 提供")
    prev = asyncio.get_event_loop_policy()
    monkeypatch.setattr(sys, "platform", "win32")
    try:
        serve.use_selector_loop_on_windows()
        assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)
    finally:
        asyncio.set_event_loop_policy(prev)


def test_use_selector_loop_noop_elsewhere(monkeypatch):
    """非 win32 分支: no-op,policy 不变。"""
    monkeypatch.setattr(sys, "platform", "linux")
    prev = asyncio.get_event_loop_policy()
    serve.use_selector_loop_on_windows()
    assert asyncio.get_event_loop_policy() is prev
