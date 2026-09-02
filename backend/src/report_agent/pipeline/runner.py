"""管线运行器:认领 → 逐阶段执行 → checkpoint → complete/fail;含启动恢复。"""
import asyncio
import time

from report_agent.observability import bind_context, clear_context, get_logger
from report_agent.pipeline.stages import STAGE_FUNCS, STAGE_ORDER, StageContext
from report_agent.pipeline.tasks import TaskService

log = get_logger(__name__)


class PipelineRunner:
    def __init__(self, task_service: TaskService, db, deps):
        self._tasks = task_service
        self._db = db
        self._deps = deps
        # 阶段注册表引用(同一 dict 对象,Task 12/13 的 @register 追加自动可见);
        # 单测经 _stage_funcs 注入 fake 阶段函数(见 tests/unit_pipeline/test_runner.py)
        self._stage_funcs = STAGE_FUNCS

    async def run_task(self, task_id: str) -> None:
        task = await self._tasks.claim(task_id)
        if task is None:
            return
        bind_context(task_id=task_id)
        failed_stage = None
        try:
            report = await self._db.get_report(task.report_id)
            if report is None:
                raise ValueError(f"报告不存在: {task.report_id}")
            ctx = StageContext(
                task_id=task_id, report=report, checkpoints=task.checkpoints,
                deps=self._deps, db=self._db, tasks=self._tasks,
            )
            for stage in STAGE_ORDER:
                if stage in task.checkpoints:
                    continue
                failed_stage = stage
                fn = self._stage_funcs.get(stage)
                if fn is None:
                    raise RuntimeError(f"阶段未注册: {stage}")
                t0 = time.perf_counter()
                log.info("stage_start", stage=stage)
                payload = await fn(ctx)
                await self._tasks.save_checkpoint(task_id, stage, payload, time.perf_counter() - t0)
                ctx.checkpoints[stage] = payload
                log.info("stage_done", stage=stage, seconds=round(time.perf_counter() - t0, 2))
                if stage == "parse" and payload.get("needs_meta"):
                    await self._tasks.pause_for_meta(task_id)
                    return
            degraded = bool(ctx.checkpoints.get("guardrail", {}).get("degraded"))
            await self._tasks.complete(task_id, degraded=degraded)
            log.info("pipeline_completed", degraded=degraded)
        except Exception as e:
            log.exception("pipeline_stage_error")
            await self._tasks.fail(task_id, str(e))
            # F6(a): 失败已有 structlog,追加审计行 —— 解析失败/阶段崩溃可查(audit_events)
            factory = getattr(self._deps, "session_factory", None)
            if factory is not None:
                from report_agent.guardrails.audit import AuditLog

                audit = AuditLog(factory, defaults={"report_id": task.report_id,
                                                    "task_id": task_id})
                await audit.log("pipeline_failed",
                                {"stage": failed_stage, "error": str(e)[:500]})
        finally:
            clear_context()

    def recover_on_startup(self) -> None:
        """服务重启:把所有 running 任务放回执行队列(spec §5.7)。"""

        async def _recover():
            for task_id in await self._tasks.list_running():
                log.warning("recovering_task", task_id=task_id)
                asyncio.create_task(self.run_task(task_id))

        asyncio.create_task(_recover())
