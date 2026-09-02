"""AuditLog 单测:落库字段(默认关联 id 兜底 / 显式参数覆盖)、写失败只告警不抛。spec §4.4/§10。"""
import asyncio

from report_agent.db.models import AuditEvent
from report_agent.guardrails.audit import AuditLog


class _FakeSession:
    def __init__(self, fail_commit=False):
        self.added = []
        self.fail_commit = fail_commit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("db down")


class _FakeFactory:
    def __init__(self, fail_open=False, fail_commit=False):
        self.sessions = []
        self.fail_open = fail_open
        self.fail_commit = fail_commit

    def __call__(self):
        if self.fail_open:
            raise RuntimeError("db down")
        s = _FakeSession(fail_commit=self.fail_commit)
        self.sessions.append(s)
        return s


def test_audit_log_writes_event_with_default_ids():
    factory = _FakeFactory()
    asyncio.run(AuditLog(factory, defaults={"report_id": "r1", "task_id": "t1"}).log(
        "guardrail_block", {"findings": ["诊断用语: 确诊"]}))
    ev = factory.sessions[0].added[0]
    assert isinstance(ev, AuditEvent)
    assert ev.event_type == "guardrail_block"
    assert ev.payload == {"findings": ["诊断用语: 确诊"]}
    assert (ev.report_id, ev.task_id, ev.session_id) == ("r1", "t1", None)


def test_audit_log_explicit_args_override_defaults():
    factory = _FakeFactory()
    asyncio.run(AuditLog(factory, defaults={"report_id": "r1", "task_id": "t1"}).log(
        "degraded_output", report_id="r9", session_id="s2"))
    ev = factory.sessions[0].added[0]
    assert ev.event_type == "degraded_output"
    assert ev.report_id == "r9"  # 显式参数优先
    assert ev.task_id == "t1" and ev.session_id == "s2"


def test_audit_log_backward_compat_plain_constructor():
    factory = _FakeFactory()
    asyncio.run(AuditLog(factory).log("review_failed"))  # 无 defaults → 全部 None
    ev = factory.sessions[0].added[0]
    assert ev.event_type == "review_failed"
    assert ev.payload == {}
    assert ev.report_id is None and ev.task_id is None and ev.session_id is None


def test_audit_log_write_failures_warn_only_no_raise():
    asyncio.run(AuditLog(_FakeFactory(fail_open=True), defaults={"report_id": "r1"}).log(
        "guardrail_suspect"))  # 工厂打开失败 → 告警不抛
    asyncio.run(AuditLog(_FakeFactory(fail_commit=True)).log("degraded_output"))  # commit 失败 → 告警不抛
