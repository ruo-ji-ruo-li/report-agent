"""审计事件写入。写失败只告警不抛——审计不阻塞用户响应(spec §4.4/§10)。"""
from report_agent.db.models import AuditEvent
from report_agent.observability import get_logger

log = get_logger(__name__)


class AuditLog:
    def __init__(self, session_factory):
        self._factory = session_factory

    async def log(self, event_type: str, payload: dict | None = None, report_id: str | None = None,
                  task_id: str | None = None, session_id: str | None = None) -> None:
        try:
            async with self._factory() as s:
                s.add(AuditEvent(
                    event_type=event_type, payload=payload or {},
                    report_id=report_id, task_id=task_id, session_id=session_id,
                ))
                await s.commit()
        except Exception as e:  # noqa: BLE001
            log.error("audit_write_failed", event_type=event_type, error=str(e))
