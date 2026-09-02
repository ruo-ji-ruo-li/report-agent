"""PipelineRunner 单测:注入 FakeTaskService/FakeStage/FakeDB,零 DB/零网络。

(brief Step 1 五个用例;STAGE_ORDER 全局改用 monkeypatch.setattr,避免跨用例泄漏)
"""
import asyncio

from report_agent.pipeline import runner as runner_mod
from report_agent.pipeline.runner import PipelineRunner
from report_agent.pipeline.tasks import TaskStatus


class FakeTaskService:
    def __init__(self):
        self.state = {"status": TaskStatus.PENDING, "checkpoints": {}, "stage": None}
        self.calls = []

    async def claim(self, task_id):
        self.calls.append("claim")
        if self.state["status"] not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return None
        self.state["status"] = TaskStatus.RUNNING
        return type("T", (), {
            "id": task_id, "report_id": "r1", "status": self.state["status"],
            "stage": None, "checkpoints": self.state["checkpoints"],
            "timings": {}, "error": None,
        })()

    async def save_checkpoint(self, task_id, stage, payload, seconds):
        self.calls.append(("checkpoint", stage))
        self.state["checkpoints"][stage] = payload

    async def pause_for_meta(self, task_id):
        self.calls.append("pause")
        self.state["status"] = TaskStatus.AWAITING_META

    async def complete(self, task_id, degraded=False):
        self.calls.append(("complete", degraded))

    async def fail(self, task_id, error):
        self.calls.append(("fail", error))


class FakeAuditSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


class FakeAuditFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        s = FakeAuditSession()
        self.sessions.append(s)
        return s

    @property
    def events(self) -> list:
        return [e for s in self.sessions for e in s.added]


class FakeDeps:
    def __init__(self, session_factory):
        self.session_factory = session_factory


class FakeDB:
    async def get_report(self, report_id):
        return {"id": report_id, "source": "manual", "file_path": None,
                "sex": "male", "age": 40.0}


def _runner(tasks, stages, deps=None) -> PipelineRunner:
    r = PipelineRunner(task_service=tasks, db=FakeDB(), deps=deps or object())
    r._stage_funcs = stages  # 注入
    return r


def test_run_task_stage_order_and_checkpoints(monkeypatch):
    tasks = FakeTaskService()
    order = []
    monkeypatch.setattr(runner_mod, "STAGE_ORDER", ["parse", "normalize", "compare"])

    async def mk(name):
        async def fn(ctx):
            order.append(name)
            return {"n": len(order)}
        return fn

    stages = {"parse": asyncio.run(mk("parse")),
              "normalize": asyncio.run(mk("normalize")),
              "compare": asyncio.run(mk("compare"))}
    asyncio.run(_runner(tasks, stages).run_task("t1"))
    assert order == ["parse", "normalize", "compare"]
    assert tasks.calls.count("claim") == 1
    assert ("checkpoint", "parse") in tasks.calls and ("checkpoint", "compare") in tasks.calls
    assert ("complete", False) in tasks.calls


def test_run_task_skips_completed_stages(monkeypatch):
    tasks = FakeTaskService()
    tasks.state["checkpoints"] = {"parse": {"needs_meta": False}}
    order = []
    monkeypatch.setattr(runner_mod, "STAGE_ORDER", ["parse", "normalize"])

    async def aparse(ctx):
        order.append("parse")
        return {}

    async def anormalize(ctx):
        order.append("normalize")
        return {}

    r = _runner(tasks, {"parse": aparse, "normalize": anormalize})
    asyncio.run(r.run_task("t1"))
    assert order == ["normalize"]  # parse 已在 checkpoint,跳过


def test_run_task_needs_meta_pauses(monkeypatch):
    tasks = FakeTaskService()
    monkeypatch.setattr(runner_mod, "STAGE_ORDER", ["parse"])

    async def aparse(ctx):
        return {"needs_meta": True}

    asyncio.run(_runner(tasks, {"parse": aparse}).run_task("t1"))
    assert "pause" in tasks.calls
    assert tasks.state["status"] == TaskStatus.AWAITING_META


def test_run_task_stage_failure_fails_task(monkeypatch):
    tasks = FakeTaskService()
    monkeypatch.setattr(runner_mod, "STAGE_ORDER", ["parse"])

    async def aparse(ctx):
        raise ValueError("解析彻底失败")

    asyncio.run(_runner(tasks, {"parse": aparse}).run_task("t1"))
    fails = [c[1] for c in tasks.calls if isinstance(c, tuple) and c[0] == "fail"]
    assert fails and "彻底失败" in fails[0]


def test_run_task_stage_failure_emits_pipeline_failed_audit(monkeypatch):
    """F6(a):阶段异常除 structlog + task fail 外,追加 audit pipeline_failed
    {stage, error}(带 report/task 关联)。"""
    tasks = FakeTaskService()
    monkeypatch.setattr(runner_mod, "STAGE_ORDER", ["parse", "normalize"])
    factory = FakeAuditFactory()

    async def aparse(ctx):
        raise ValueError("解析彻底失败")

    async def anormalize(ctx):
        return {}

    deps = FakeDeps(factory)
    asyncio.run(_runner(tasks, {"parse": aparse, "normalize": anormalize}, deps).run_task("t1"))
    fails = [c[1] for c in tasks.calls if isinstance(c, tuple) and c[0] == "fail"]
    assert fails and "彻底失败" in fails[0]
    assert [e.event_type for e in factory.events] == ["pipeline_failed"]
    ev = factory.events[0]
    assert ev.payload["stage"] == "parse" and "彻底失败" in ev.payload["error"]
    assert ev.report_id == "r1" and ev.task_id == "t1"


def test_recover_on_startup_reruns_running(monkeypatch):
    tasks = FakeTaskService()
    tasks.state["status"] = TaskStatus.RUNNING

    class FakeTasks2(FakeTaskService):
        async def list_running(self):
            return ["t9"]

    tasks = FakeTasks2()
    ran = []

    def capture(fn):  # brief 原文为 async def,body 永远不会执行(无人 await);改同步并显式 close
        ran.append(1)
        fn.close()  # 丢弃被调度协程,避免 never-awaited 告警

    monkeypatch.setattr(runner_mod.asyncio, "create_task", capture)
    r = _runner(tasks, {})
    r.recover_on_startup()
    assert ran == [1]
