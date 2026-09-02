"""FastAPI 工厂。lifespan:build deps → runner + 启动恢复;外部服务不可用只告警不崩。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from report_agent.config import get_settings
from report_agent.db.session import session_factory
from report_agent.observability import get_logger, setup_logging
from report_agent.pipeline.deps import build_deps
from report_agent.pipeline.runner import PipelineRunner
from report_agent.pipeline.tasks import TaskService

log = get_logger(__name__)


def create_app(deps_builder=None) -> FastAPI:
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        try:
            deps = await (deps_builder or build_deps)(settings)
            app.state.deps = deps
            app.state.db_access = deps.db
            app.state.tasks = TaskService(session_factory)
            app.state.runner = PipelineRunner(app.state.tasks, deps.db, deps)
            app.state.runner.recover_on_startup()
            log.info("app_started")
        except Exception as e:  # noqa: BLE001 —— 依赖服务不可用时应用仍可启动(降级)
            log.error("app_startup_degraded", error=str(e))
        yield

    app = FastAPI(title="report-agent", version="0.1.0", lifespan=lifespan)
    from report_agent.api import health, reports, tasks

    app.include_router(health.router)
    app.include_router(reports.router)
    app.include_router(tasks.router)
    return app
