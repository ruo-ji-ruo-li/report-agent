"""parse-upgrade: 全表 created_at/updated_at + reports.name + raw.code +
unparsed_tables + item_section_mapping,删两张 item 表的 section 列(spec §9)。

手写迁移(本机无 docker/postgres,无法 autogenerate),与 0001 同风格:
add_column 一律字面表名(一致性测试对 add_column 做 AST 静态核对,见
tests/unit_api/test_0001_migration_consistency.py)。
验证:tests/unit_api/test_0001_migration_consistency.py(sqlite 差分,0001→0002 链)。
Revision ID: 0002
Revises: 0001
Create Date: 2026-09-06
"""
import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- reports:name + updated_at(created_at 已有)----
    op.add_column("reports", sa.Column("name", sa.String(length=128), nullable=True))
    op.add_column("reports", sa.Column("updated_at", sa.DateTime(timezone=True),
                                       server_default=sa.text("now()"), nullable=False))

    # ---- report_items_raw:code + 时间戳,删 section ----
    op.add_column("report_items_raw", sa.Column("code", sa.String(length=32), nullable=True))
    op.add_column("report_items_raw", sa.Column("created_at", sa.DateTime(timezone=True),
                                                server_default=sa.text("now()"), nullable=False))
    op.add_column("report_items_raw", sa.Column("updated_at", sa.DateTime(timezone=True),
                                                server_default=sa.text("now()"), nullable=False))
    op.drop_column("report_items_raw", "section")

    # ---- report_items_normalized:时间戳,删 section ----
    op.add_column("report_items_normalized", sa.Column("created_at", sa.DateTime(timezone=True),
                                                       server_default=sa.text("now()"),
                                                       nullable=False))
    op.add_column("report_items_normalized", sa.Column("updated_at", sa.DateTime(timezone=True),
                                                       server_default=sa.text("now()"),
                                                       nullable=False))
    op.drop_column("report_items_normalized", "section")

    # ---- 其余表补 updated_at(created_at 已有)----
    op.add_column("interpretations", sa.Column("updated_at", sa.DateTime(timezone=True),
                                               server_default=sa.text("now()"), nullable=False))
    op.add_column("followup_plans", sa.Column("updated_at", sa.DateTime(timezone=True),
                                              server_default=sa.text("now()"), nullable=False))
    op.add_column("chat_sessions", sa.Column("updated_at", sa.DateTime(timezone=True),
                                             server_default=sa.text("now()"), nullable=False))
    op.add_column("chat_messages", sa.Column("updated_at", sa.DateTime(timezone=True),
                                             server_default=sa.text("now()"), nullable=False))
    op.add_column("audit_events", sa.Column("updated_at", sa.DateTime(timezone=True),
                                            server_default=sa.text("now()"), nullable=False))

    # ---- unparsed_tables(spec §7.1)----
    op.create_table(
        "unparsed_tables",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("table_html", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["report_id"], ["reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_unparsed_tables_report_id", "unparsed_tables", ["report_id"], unique=False)
    op.create_index("ix_unparsed_tables_task_id", "unparsed_tables", ["task_id"], unique=False)

    # ---- item_section_mapping(spec §9.2)----
    op.create_table(
        "item_section_mapping",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_name", sa.String(length=128), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_name"),
    )


def downgrade() -> None:
    op.drop_table("item_section_mapping")
    op.drop_index("ix_unparsed_tables_task_id", table_name="unparsed_tables")
    op.drop_index("ix_unparsed_tables_report_id", table_name="unparsed_tables")
    op.drop_table("unparsed_tables")
    for table in ["audit_events", "chat_messages", "chat_sessions",
                  "followup_plans", "interpretations"]:
        op.drop_column(table, "updated_at")
    op.add_column("report_items_normalized",
                  sa.Column("section", sa.String(length=64), nullable=True))
    op.drop_column("report_items_normalized", "updated_at")
    op.drop_column("report_items_normalized", "created_at")
    op.add_column("report_items_raw",
                  sa.Column("section", sa.String(length=64), nullable=True))
    op.drop_column("report_items_raw", "updated_at")
    op.drop_column("report_items_raw", "created_at")
    op.drop_column("report_items_raw", "code")
    op.drop_column("reports", "updated_at")
    op.drop_column("reports", "name")
