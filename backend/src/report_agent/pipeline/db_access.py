"""管线阶段的数据访问(隔离 SQL,便于阶段函数单测注入 fake)。"""
from report_agent.db.models import (
    FollowupPlanRow,
    InterpretationRow,
    NormalizedItemRow,
    RawItem,
    Report,
)
from report_agent.parsing.schemas import NormalizedItem, RawReportItem, ReportMeta
from report_agent.pipeline.rule_compare import ItemJudgment


class DataAccess:
    def __init__(self, session_factory):
        self._factory = session_factory

    async def get_report(self, report_id: str) -> dict | None:
        async with self._factory() as s:
            row = await s.get(Report, report_id)
            if row is None:
                return None
            return {
                "id": row.id, "source": row.source, "file_path": row.file_path,
                "institution": row.institution, "report_date": row.report_date,
                "sex": row.sex, "age": row.age,
            }

    async def update_report_meta(self, report_id: str, meta: ReportMeta) -> None:
        async with self._factory() as s:
            row = await s.get(Report, report_id)
            if row is None:
                return
            row.institution = row.institution or meta.institution
            row.report_date = row.report_date or meta.report_date
            row.sex = row.sex or meta.sex
            row.age = row.age if row.age is not None else meta.age
            await s.commit()

    async def save_raw_items(self, report_id: str, items: list[RawReportItem]) -> None:
        async with self._factory() as s:
            s.add_all([
                RawItem(report_id=report_id, item_name=it.name,
                        value_text=it.value_text, value_num=it.value_num, unit=it.unit,
                        ref_range_text=it.ref_range_text, abnormal_flag=it.abnormal_flag)
                for it in items
            ])
            await s.commit()

    async def delete_raw_items(self, report_id: str) -> None:
        """F3: 报告级删旧 raw 行(parse 阶段重跑幂等:崩溃恢复/重跑不累积重复行)。"""
        from sqlalchemy import delete

        async with self._factory() as s:
            await s.execute(delete(RawItem).where(RawItem.report_id == report_id))
            await s.commit()

    async def get_raw_items(self, report_id: str) -> list[RawReportItem]:
        async with self._factory() as s:
            from sqlalchemy import select

            rows = (await s.execute(
                select(RawItem).where(RawItem.report_id == report_id).order_by(RawItem.id)
            )).scalars().all()
            return [
                RawReportItem(name=r.item_name, value_text=r.value_text,
                              value_num=r.value_num, unit=r.unit,
                              ref_range_text=r.ref_range_text, abnormal_flag=r.abnormal_flag)
                for r in rows
            ]

    async def save_normalized(self, report_id: str, items: list[NormalizedItem]) -> None:
        async with self._factory() as s:
            s.add_all([
                NormalizedItemRow(
                    report_id=report_id, item_name=it.name,
                    indicator_code=it.indicator_code, value_text=it.value_text,
                    value_num=it.value_num, unit=it.unit,
                    raw_value_num=it.raw_value_num, raw_unit=it.raw_unit,
                    ref_range_text=it.ref_range_text, range_from=it.range_from,
                )
                for it in items
            ])
            await s.commit()

    async def delete_normalized(self, report_id: str) -> None:
        """F3: 报告级删旧 normalized 行(normalize 阶段重跑幂等,spec §4.1 派生层重跑可执行)。"""
        from sqlalchemy import delete

        async with self._factory() as s:
            await s.execute(
                delete(NormalizedItemRow).where(NormalizedItemRow.report_id == report_id)
            )
            await s.commit()

    async def get_normalized(self, report_id: str) -> list[NormalizedItem]:
        async with self._factory() as s:
            from sqlalchemy import select

            rows = (await s.execute(
                select(NormalizedItemRow).where(NormalizedItemRow.report_id == report_id)
                .order_by(NormalizedItemRow.id)
            )).scalars().all()
            return [
                NormalizedItem(
                    raw_index=i, name=r.item_name, indicator_code=r.indicator_code,
                    value_text=r.value_text, value_num=r.value_num, unit=r.unit,
                    raw_value_num=r.raw_value_num, raw_unit=r.raw_unit,
                    ref_range_text=r.ref_range_text, range_from=r.range_from,
                )
                for i, r in enumerate(rows)
            ]

    async def apply_judgments(self, report_id: str, judgments: list[ItemJudgment]) -> None:
        async with self._factory() as s:
            from sqlalchemy import select

            rows = (await s.execute(
                select(NormalizedItemRow).where(NormalizedItemRow.report_id == report_id)
                .order_by(NormalizedItemRow.id)
            )).scalars().all()
            for row, j in zip(rows, judgments, strict=True):
                row.status = j.status.value
                row.ref_low, row.ref_high = j.ref_low, j.ref_high
                row.critical = j.critical
            await s.commit()

    async def save_interpretation(self, report_id: str, task_id: str, doc: dict) -> None:
        async with self._factory() as s:
            s.add(InterpretationRow(
                report_id=report_id, task_id=task_id, summary=doc["summary"],
                items=doc["items"], advice_summary=doc["advice_summary"],
                disclaimer=doc["disclaimer"], degraded=doc.get("degraded", False),
            ))
            await s.commit()

    async def delete_interpretation(self, task_id: str) -> None:
        """F3: 按 task_id 删旧解读行 —— interpretations.task_id 唯一约束,plan 重跑幂等。"""
        from sqlalchemy import delete

        async with self._factory() as s:
            await s.execute(
                delete(InterpretationRow).where(InterpretationRow.task_id == task_id)
            )
            await s.commit()

    async def save_followup(self, report_id: str, task_id: str, doc: dict) -> None:
        async with self._factory() as s:
            s.add(FollowupPlanRow(
                report_id=report_id, task_id=task_id, items=doc["items"],
                degraded=doc.get("degraded", False),
            ))
            await s.commit()

    async def delete_followup(self, task_id: str) -> None:
        """F3: 按 task_id 删旧复查单行(followup_plans.task_id 唯一约束,plan 重跑幂等)。"""
        from sqlalchemy import delete

        async with self._factory() as s:
            await s.execute(delete(FollowupPlanRow).where(FollowupPlanRow.task_id == task_id))
            await s.commit()

    # ===== API 层使用 =====
    async def create_report(self, source: str, file_path: str | None, meta: ReportMeta) -> str:
        from report_agent.db.models import Report

        async with self._factory() as s:
            row = Report(source=source, file_path=file_path, institution=meta.institution,
                         report_date=meta.report_date, sex=meta.sex, age=meta.age)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row.id

    async def get_report_detail(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        async with self._factory() as s:
            row = await s.get(Report, report_id)
            if row is None:
                return None
            raws = (await s.execute(
                select(RawItem).where(RawItem.report_id == report_id).order_by(RawItem.id)
            )).scalars().all()
            norms = (await s.execute(
                select(NormalizedItemRow).where(NormalizedItemRow.report_id == report_id)
                .order_by(NormalizedItemRow.id)
            )).scalars().all()
            return {
                "meta": {"id": row.id, "source": row.source, "institution": row.institution,
                         "report_date": row.report_date, "sex": row.sex, "age": row.age},
                "items": [
                    {"section": r.section, "name": r.item_name, "value_text": r.value_text,
                     "value_num": r.value_num, "unit": r.unit, "ref_range_text": r.ref_range_text,
                     "abnormal_flag": r.abnormal_flag}
                    for r in raws
                ],
                "normalized": [
                    {"item_name": n.item_name, "indicator_code": n.indicator_code,
                     "value_num": n.value_num, "unit": n.unit, "status": n.status,
                     "ref_low": n.ref_low, "ref_high": n.ref_high, "critical": n.critical,
                     "section": n.section}
                    for n in norms
                ],
            }

    async def get_task_for_report(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        from report_agent.db.models import InterpretationTask

        async with self._factory() as s:
            row = (await s.execute(
                select(InterpretationTask).where(InterpretationTask.report_id == report_id)
                .order_by(InterpretationTask.created_at.desc()).limit(1)
            )).scalars().first()
            if row is None:
                return None
            return {"id": row.id, "status": row.status, "stage": row.stage,
                    "error": row.error}

    async def get_interpretation(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        from report_agent.db.models import InterpretationRow

        async with self._factory() as s:
            row = (await s.execute(
                select(InterpretationRow).where(InterpretationRow.report_id == report_id)
                .order_by(InterpretationRow.created_at.desc()).limit(1)
            )).scalars().first()
            if row is None:
                return None
            return {"summary": row.summary, "items": row.items,
                    "advice_summary": row.advice_summary, "disclaimer": row.disclaimer,
                    "degraded": row.degraded}

    async def get_followup(self, report_id: str) -> dict | None:
        from sqlalchemy import select

        from report_agent.db.models import FollowupPlanRow

        async with self._factory() as s:
            row = (await s.execute(
                select(FollowupPlanRow).where(FollowupPlanRow.report_id == report_id)
                .order_by(FollowupPlanRow.created_at.desc()).limit(1)
            )).scalars().first()
            if row is None:
                return None
            return {"items": row.items, "degraded": row.degraded}

    # ===== 追问会话 =====
    async def create_session(self, report_id: str) -> str:
        from report_agent.db.models import ChatSession

        async with self._factory() as s:
            row = ChatSession(report_id=report_id)
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return row.id

    async def get_session(self, session_id: str) -> dict | None:
        from report_agent.db.models import ChatSession

        async with self._factory() as s:
            row = await s.get(ChatSession, session_id)
            return {"id": row.id, "report_id": row.report_id} if row else None

    async def add_message(self, session_id: str, role: str, content: str, tool_calls=None,
                          evidence_ids=None, guardrail_flags=None) -> None:
        from report_agent.db.models import ChatMessage

        async with self._factory() as s:
            s.add(ChatMessage(session_id=session_id, role=role, content=content,
                              tool_calls=tool_calls, evidence_ids=evidence_ids,
                              guardrail_flags=guardrail_flags))
            await s.commit()

    async def get_messages(self, session_id: str) -> list[dict]:
        from sqlalchemy import select

        from report_agent.db.models import ChatMessage

        async with self._factory() as s:
            rows = (await s.execute(
                select(ChatMessage).where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at)
            )).scalars().all()
            return [
                {"role": r.role, "content": r.content, "guardrail_flags": r.guardrail_flags,
                 "created_at": r.created_at.isoformat() if r.created_at else None}
                for r in rows
            ]
