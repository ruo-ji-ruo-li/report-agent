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
        # 追问会话 checkpointer(LangGraph 记忆)+ LangSmith 开关。独立 try:
        # postgres 不可用只降级聊天记忆,不影响已启动的核心服务。
        _chat_saver_cm = None
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            # langgraph-checkpoint-postgres>=3: from_conn_string 是 async 上下文管理器,
            # 实例生命周期 = 连接生命周期,故 __aenter__ 于启动、__aexit__ 于关闭。
            _chat_saver_cm = AsyncPostgresSaver.from_conn_string(settings.postgres_dsn)
            saver = await _chat_saver_cm.__aenter__()
            saver.setup()
            app.state.chat_checkpointer = saver
            if settings.langsmith_tracing and settings.langsmith_api_key:
                import os

                os.environ["LANGSMITH_TRACING"] = "true"
                os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
                os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
                log.info("langsmith_enabled")
        except Exception as e:  # noqa: BLE001 —— 追问记忆降级,核心服务不受影响
            log.error("chat_checkpointer_degraded", error=str(e))
        try:
            yield
        finally:
            if _chat_saver_cm is not None:
                try:
                    await _chat_saver_cm.__aexit__(None, None, None)
                except Exception as e:  # noqa: BLE001
                    log.error("chat_checkpointer_close_failed", error=str(e))

    app = FastAPI(title="report-agent", version="0.1.0", lifespan=lifespan)
    from report_agent.api import chat, health, reports, tasks

    app.include_router(health.router)
    app.include_router(reports.router)
    app.include_router(tasks.router)
    app.include_router(chat.router)
    return app
