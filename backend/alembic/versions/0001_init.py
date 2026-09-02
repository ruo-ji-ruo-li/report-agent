"""init: 9 张核心表

手写迁移(本机无 docker/postgres, 无法 autogenerate)。
与 src/report_agent/db/models.py 逐列一致:
- String 列存枚举 .value 字符串, 无原生 enum;
- 可空性由模型注解决定: Mapped[X] 非空 / Mapped[X | None] 可空;
- JSON 列默认值(Python 侧 default=dict/list)不进 DDL, 仅 ORM 层生效;
- status/critical/degraded 等默认值同为 Python 侧, 无 server_default;
- 索引命名采用 SQLAlchemy 默认约定 ix_<table>_<column>, 与模型 index=True
  在 create_all 下生成的索引一一对应; 单列唯一约束不命名(postgres 自动命名)。
验证: tests/unit_api/test_0001_migration_consistency.py(sqlite 差分)。
Revision ID: 0001
Revises:
Create Date: 2026-09-02
"""
import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- reports ----
    op.create_table(
        "reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("file_path", sa.String(length=512), nullable=True),
        sa.Column("institution", sa.String(length=128), nullable=True),
        sa.Column("report_date", sa.String(length=32), nullable=True),
        sa.Column("sex", sa.String(length=8), nullable=True),
        sa.Column("age", sa.Float(), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ---- report_items_raw ----
    op.create_table(
        "report_items_raw",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=True),
        sa.Column("item_name", sa.String(length=128), nullable=False),
        sa.Column("value_text", sa.String(length=64), nullable=True),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("ref_range_text", sa.String(length=128), nullable=True),
        sa.Column("abnormal_flag", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_report_items_raw_report_id", "report_items_raw", ["report_id"], unique=False)

    # ---- report_items_normalized ----
    op.create_table(
        "report_items_normalized",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("raw_item_id", sa.String(length=36), nullable=True),
        sa.Column("section", sa.String(length=64), nullable=True),
        sa.Column("item_name", sa.String(length=128), nullable=False),
        sa.Column("indicator_code", sa.String(length=32), nullable=True),
        sa.Column("value_text", sa.String(length=64), nullable=True),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("raw_value_num", sa.Float(), nullable=True),
        sa.Column("raw_unit", sa.String(length=32), nullable=True),
        sa.Column("ref_range_text", sa.String(length=128), nullable=True),
        sa.Column("range_from", sa.String(length=8), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=True),
        sa.Column("ref_low", sa.Float(), nullable=True),
        sa.Column("ref_high", sa.Float(), nullable=True),
        sa.Column("critical", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["raw_item_id"], ["report_items_raw.id"]),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_report_items_normalized_report_id", "report_items_normalized", ["report_id"], unique=False
    )
    op.create_index(
        "ix_report_items_normalized_indicator_code",
        "report_items_normalized",
        ["indicator_code"],
        unique=False,
    )

    # ---- interpretation_tasks ----
    op.create_table(
        "interpretation_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=True),
        sa.Column("checkpoints", sa.JSON(), nullable=False),
        sa.Column("timings", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_interpretation_tasks_report_id", "interpretation_tasks", ["report_id"], unique=False
    )
    op.create_index(
        "ix_interpretation_tasks_status", "interpretation_tasks", ["status"], unique=False
    )

    # ---- interpretations ----
    op.create_table(
        "interpretations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("advice_summary", sa.Text(), nullable=False),
        sa.Column("disclaimer", sa.Text(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["task_id"], ["interpretation_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_interpretations_report_id", "interpretations", ["report_id"], unique=False)

    # ---- followup_plans ----
    op.create_table(
        "followup_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["task_id"], ["interpretation_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_followup_plans_report_id", "followup_plans", ["report_id"], unique=False)

    # ---- chat_sessions ----
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_sessions_report_id", "chat_sessions", ["report_id"], unique=False)

    # ---- chat_messages ----
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tool_calls", sa.JSON(), nullable=True),
        sa.Column("evidence_ids", sa.JSON(), nullable=True),
        sa.Column("guardrail_flags", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"], unique=False)

    # ---- audit_events ----
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_event_type", "audit_events", ["event_type"], unique=False)
    op.create_index("ix_audit_events_report_id", "audit_events", ["report_id"], unique=False)
    op.create_index("ix_audit_events_task_id", "audit_events", ["task_id"], unique=False)
    op.create_index("ix_audit_events_session_id", "audit_events", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_events_session_id", table_name="audit_events")
    op.drop_index("ix_audit_events_task_id", table_name="audit_events")
    op.drop_index("ix_audit_events_report_id", table_name="audit_events")
    op.drop_index("ix_audit_events_event_type", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index("ix_chat_sessions_report_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")

    op.drop_index("ix_followup_plans_report_id", table_name="followup_plans")
    op.drop_table("followup_plans")

    op.drop_index("ix_interpretations_report_id", table_name="interpretations")
    op.drop_table("interpretations")

    op.drop_index("ix_interpretation_tasks_status", table_name="interpretation_tasks")
    op.drop_index("ix_interpretation_tasks_report_id", table_name="interpretation_tasks")
    op.drop_table("interpretation_tasks")

    op.drop_index(
        "ix_report_items_normalized_indicator_code", table_name="report_items_normalized"
    )
    op.drop_index("ix_report_items_normalized_report_id", table_name="report_items_normalized")
    op.drop_table("report_items_normalized")

    op.drop_index("ix_report_items_raw_report_id", table_name="report_items_raw")
    op.drop_table("report_items_raw")

    op.drop_table("reports")
