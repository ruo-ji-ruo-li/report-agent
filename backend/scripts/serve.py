"""一键启动: 先幂等数据库迁移(alembic upgrade head), 再启动 FastAPI 服务。

运行(backend/ 下):
  uv run python scripts/serve.py                        # 默认 127.0.0.1:8000
  uv run python scripts/serve.py --host 0.0.0.0 --port 9000
  uv run python scripts/serve.py --skip-migration       # 跳过迁移直接启动

幂等性由 alembic 原生保证: 首次运行建表并写入 alembic_version 版本记录, 之后每次启动
发现已是 head 自动跳过, 不存在"表已存在仍重建"的问题。连接串经 alembic/env.py 以
.env 的 POSTGRES_DSN 覆写, alembic.ini 内的 sqlalchemy.url 仅为占位。
注意: 迁移必须在 uvicorn 事件循环启动之前同步执行——env.py online 模式内部用
asyncio.run(), 不能在已有事件循环内调用——故"先迁移、后 uvicorn.run"的顺序不可调换。
--skip-migration 对应降级启动哲学(spec §11): 数据库暂不可达时仍允许带病启动服务,
由应用 lifespan 自行降级告警, 但默认(不加该开关)迁移失败会直接阻止启动。

Windows 另有一处事件循环约束: psycopg async(langgraph 追问记忆 checkpointer)只支持
SelectorEventLoop, 而 uvicorn>=0.36 在 win32 默认硬编码 loop_factory=ProactorEventLoop
并显式传给 asyncio.run, 会绕过事件循环 policy——故启动服务前切换 policy 并以
loop="none" 让 uvicorn 回退按 policy 建循环(见 use_selector_loop_on_windows)。
迁移阶段用的 asyncpg 两种循环都支持, 保持默认不受影响。
"""
import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn
from alembic.config import Config

from alembic import command

ROOT = Path(__file__).resolve().parents[1]


def build_alembic_config(root: Path) -> Config:
    """指向 backend/alembic.ini 的迁移配置。script_location=%(here)s/alembic 相对
    ini 文件自身定位, 因此从任意 CWD 启动均有效。"""
    return Config(str(root / "alembic.ini"))


def run_migrations(root: Path) -> None:
    """升级到最新迁移版本; 在已是最新版本的库上重复执行为 no-op(幂等)。"""
    command.upgrade(build_alembic_config(root), "head")


def use_selector_loop_on_windows() -> None:
    """Windows 下 psycopg async(langgraph 追问记忆 checkpointer)只支持 SelectorEventLoop;
    而 uvicorn>=0.36 在 win32 硬编码 loop_factory=ProactorEventLoop 并显式传给 asyncio.run,
    绕过事件循环 policy——故除设 policy 外, uvicorn.run 还须以 loop="none" 交回默认行为
    (asyncio.run → new_event_loop → 按 policy 建循环)。非 Windows 平台为 no-op。"""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行幂等数据库迁移后启动 report-agent API 服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址(默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="监听端口(默认 8000)")
    parser.add_argument(
        "--skip-migration",
        action="store_true",
        help="跳过迁移直接启动(数据库暂不可达仍需带病启动时使用)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if args.skip_migration:
        print("已跳过数据库迁移(--skip-migration)。")
    else:
        print("执行数据库迁移(幂等: 已是最新则自动跳过)...")
        run_migrations(ROOT)
        print("数据库迁移完成。")
    print(f"启动 API 服务 http://{args.host}:{args.port} (Ctrl-C 停止)...")
    use_selector_loop_on_windows()
    # loop="none": 不让 uvicorn 自行选定循环工厂(win32 会硬编码 Proactor),
    # 回退 asyncio.run 默认行为, 使上面设置的 Windows policy 生效。
    uvicorn.run(
        "report_agent.api.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        loop="none",
    )


if __name__ == "__main__":
    main()
