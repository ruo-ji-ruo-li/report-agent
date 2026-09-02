# tests/unit_api/test_models.py —— 不需要真库,验证表定义与枚举字符串约定
from report_agent.db import models


def test_all_tables_defined():
    assert {t.name for t in models.Base.metadata.sorted_tables} == {
        "reports",
        "report_items_raw",
        "report_items_normalized",
        "interpretation_tasks",
        "interpretations",
        "followup_plans",
        "chat_sessions",
        "chat_messages",
        "audit_events",
    }


def test_report_has_meta_columns():
    cols = {c.name for c in models.Report.__table__.columns}
    assert {"id", "source", "file_path", "institution", "report_date", "sex", "age", "user_id"} <= cols


def test_task_has_checkpoint_columns():
    cols = {c.name for c in models.InterpretationTask.__table__.columns}
    assert {"checkpoints", "timings", "status", "stage", "error"} <= cols
