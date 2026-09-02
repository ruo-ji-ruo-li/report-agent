from fastapi import FastAPI

from report_agent.observability import setup_logging


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="report-agent", version="0.1.0")
    from report_agent.api import health

    app.include_router(health.router)
    return app
