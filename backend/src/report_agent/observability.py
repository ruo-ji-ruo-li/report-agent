"""structlog JSON 结构化日志。上下文变量: request_id/task_id/session_id。"""
import contextvars

import structlog

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("task_id", default=None)
_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)


def bind_context(**values) -> None:
    for k, v in values.items():
        if v is not None:
            structlog.contextvars.bind_contextvars(**{k: v})


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


def setup_logging() -> None:
    import logging

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return structlog.get_logger(name)
