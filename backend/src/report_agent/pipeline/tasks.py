"""任务表服务:认领(SKIP LOCKED)/阶段 checkpoint/暂停/恢复/完成/失败。spec §5.7。"""
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select, update

from report_agent.db.models import InterpretationTask
from report_agent.observability import get_logger

log = get_logger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    AWAITING_META = "awaiting_meta"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEGRADED = "degraded"


@dataclass
class TaskInfo:
    id: str
    report_id: str
    status: str
    stage: str | None
    checkpoints: dict
    timings: dict
    error: str | None


def _info(row: InterpretationTask) -> TaskInfo:
    return TaskInfo(
        id=row.id, report_id=row.report_id, status=row.status, stage=row.stage,
        checkpoints=row.checkpoints or {}, timings=row.timings or {}, error=row.error,
    )


class TaskService:
    def __init__(self, session_factory):
        self._factory = session_factory

    async def create(self, report_id: str) -> TaskInfo:
        async with self._factory() as s:
            row = InterpretationTask(report_id=report_id, status=TaskStatus.PENDING)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _info(row)

    async def get(self, task_id: str) -> TaskInfo | None:
        async with self._factory() as s:
            row = await s.get(InterpretationTask, task_id)
            return _info(row) if row else None

    async def claim(self, task_id: str) -> TaskInfo | None:
        """pending/running → running。FOR UPDATE SKIP LOCKED:多 worker 安全(spec §5.7)。"""
        async with self._factory() as s:
            row = (await s.execute(
                select(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .with_for_update(skip_locked=True)
            )).scalar_one_or_none()
            if row is None or row.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
                return None
            row.status = TaskStatus.RUNNING
            await s.commit()
            await s.refresh(row)
            return _info(row)

    async def save_checkpoint(self, task_id: str, stage: str, payload: dict, seconds: float) -> None:
        async with self._factory() as s:
            row = await s.get(InterpretationTask, task_id)
            if row is None:
                return
            checkpoints = dict(row.checkpoints or {})
            checkpoints[stage] = payload
            timings = dict(row.timings or {})
            timings[stage] = round(seconds, 3)
            row.checkpoints, row.timings, row.stage = checkpoints, timings, stage
            await s.commit()

    async def pause_for_meta(self, task_id: str) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .values(status=TaskStatus.AWAITING_META)
            )
            await s.commit()

    async def resume_from_meta(self, task_id: str) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id, InterpretationTask.status == TaskStatus.AWAITING_META)
                .values(status=TaskStatus.RUNNING)
            )
            await s.commit()

    async def complete(self, task_id: str, degraded: bool) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .values(status=TaskStatus.DEGRADED if degraded else TaskStatus.COMPLETED)
            )
            await s.commit()

    async def fail(self, task_id: str, error: str) -> None:
        async with self._factory() as s:
            await s.execute(
                update(InterpretationTask)
                .where(InterpretationTask.id == task_id)
                .values(status=TaskStatus.FAILED, error=error[:2000])
            )
            await s.commit()
        log.error("task_failed", task_id=task_id, error=error)

    async def list_running(self) -> list[str]:
        async with self._factory() as s:
            rows = (await s.execute(
                select(InterpretationTask.id).where(InterpretationTask.status == TaskStatus.RUNNING)
            )).scalars().all()
            return list(rows)
