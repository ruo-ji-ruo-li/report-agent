"""9 张表,全部 String 列存枚举值(见 spec §4.1)。JSON 列用 sqlalchemy JSON 类型。"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))


class Base(DeclarativeBase):
    pass


class Report(Base):
    __tablename__ = "reports"

    id = _pk()
    source: Mapped[str] = mapped_column(String(16))  # pdf / photo / manual
    file_path: Mapped[str | None] = mapped_column(String(512))
    institution: Mapped[str | None] = mapped_column(String(128))
    report_date: Mapped[str | None] = mapped_column(String(32))
    sex: Mapped[str | None] = mapped_column(String(8))  # male / female
    age: Mapped[float | None] = mapped_column(Float)
    user_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items_raw: Mapped[list["RawItem"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    items_normalized: Mapped[list["NormalizedItemRow"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class RawItem(Base):
    __tablename__ = "report_items_raw"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    section: Mapped[str | None] = mapped_column(String(64))
    item_name: Mapped[str] = mapped_column(String(128))
    value_text: Mapped[str | None] = mapped_column(String(64))
    value_num: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    ref_range_text: Mapped[str | None] = mapped_column(String(128))
    abnormal_flag: Mapped[str | None] = mapped_column(String(16))

    report: Mapped["Report"] = relationship(back_populates="items_raw")


class NormalizedItemRow(Base):
    __tablename__ = "report_items_normalized"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    raw_item_id: Mapped[str | None] = mapped_column(ForeignKey("report_items_raw.id"))
    section: Mapped[str | None] = mapped_column(String(64))
    item_name: Mapped[str] = mapped_column(String(128))
    indicator_code: Mapped[str | None] = mapped_column(String(32), index=True)  # None = unmapped
    value_text: Mapped[str | None] = mapped_column(String(64))
    value_num: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))  # 标准单位
    raw_value_num: Mapped[float | None] = mapped_column(Float)  # 换算前原值(与报告区间同单位)
    raw_unit: Mapped[str | None] = mapped_column(String(32))
    ref_range_text: Mapped[str | None] = mapped_column(String(128))  # 报告自带区间
    range_from: Mapped[str | None] = mapped_column(String(8))  # report / kg
    status: Mapped[str | None] = mapped_column(String(16))  # 判定结果,normalize 阶段后填写
    ref_low: Mapped[float | None] = mapped_column(Float)
    ref_high: Mapped[float | None] = mapped_column(Float)
    critical: Mapped[bool] = mapped_column(default=False)

    report: Mapped["Report"] = relationship(back_populates="items_normalized")


class InterpretationTask(Base):
    __tablename__ = "interpretation_tasks"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", index=True
    )  # pending/awaiting_meta/running/completed/failed/degraded
    stage: Mapped[str | None] = mapped_column(String(16))
    checkpoints: Mapped[dict] = mapped_column(JSON, default=dict)
    timings: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class InterpretationRow(Base):
    __tablename__ = "interpretations"

    id = _pk()
    task_id: Mapped[str] = mapped_column(
        ForeignKey("interpretation_tasks.id", ondelete="CASCADE"), unique=True
    )
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    summary: Mapped[str] = mapped_column(Text)
    items: Mapped[list] = mapped_column(JSON)  # 逐项解读数组
    advice_summary: Mapped[str] = mapped_column(Text)
    disclaimer: Mapped[str] = mapped_column(Text)
    degraded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FollowupPlanRow(Base):
    __tablename__ = "followup_plans"

    id = _pk()
    task_id: Mapped[str] = mapped_column(
        ForeignKey("interpretation_tasks.id", ondelete="CASCADE"), unique=True
    )
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    items: Mapped[list] = mapped_column(JSON)
    degraded: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = _pk()
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = _pk()
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list | None] = mapped_column(JSON)
    evidence_ids: Mapped[list | None] = mapped_column(JSON)
    guardrail_flags: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = _pk()
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    report_id: Mapped[str | None] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
