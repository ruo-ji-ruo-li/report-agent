"""DataAccess 行写幂等回归(final review F3):sqlite+aiosqlite 内存库跑真 SQL。

锁定语义:
- parse/normalize 阶段重跑 = 报告级 delete-then-insert → 不累积重复行;
- plan 阶段重跑 = task 级 delete-then-insert → 不撞 interpretations/followup_plans
  的 task_id 唯一约束(修前第二次纯 insert 抛 IntegrityError → 任务永久 failed);
- delete 只删目标 report/task 的行,不影响其他行。

迁移一致性由 test_0001_migration_consistency.py 覆盖,这里只验证运行时行为。
"""
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from report_agent.db import models
from report_agent.parsing.schemas import NormalizedItem, RawReportItem, ReportMeta
from report_agent.pipeline.db_access import DataAccess
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus
from report_agent.pipeline.tasks import TaskService


@pytest.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield DataAccess(factory), factory
    await engine.dispose()


async def _mk_report(da: DataAccess) -> str:
    return await da.create_report("manual", None,
                                  ReportMeta(source="manual", sex="male", age=40.0))


def _raw(name: str, value_num: float | None) -> RawReportItem:
    return RawReportItem(name=name, value_text=str(value_num),
                         value_num=value_num, unit="mmol/L", ref_range_text="3.9-6.1",
                         abnormal_flag=None)


def _norm(name: str, code: str | None) -> NormalizedItem:
    return NormalizedItem(raw_index=0, name=name, indicator_code=code,
                          value_text="6.8", value_num=6.8, unit="mmol/L",
                          raw_value_num=6.8, raw_unit="mmol/L", ref_range_text="3.9-6.1",
                          range_from="report")


async def _count(store_factory, model, *where) -> int:
    async with store_factory() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_parse_rerun_delete_then_insert_keeps_single_raw_batch(store):
    """F3:parse 重跑(delete_raw_items + save_raw_items × 2)→ 单批行,不累积。"""
    da, _ = store
    rid = await _mk_report(da)
    rows = [_raw("空腹血糖", 6.8), _raw("白细胞", 5.2)]
    for _ in range(2):
        await da.delete_raw_items(rid)
        await da.save_raw_items(rid, rows)
    assert len(await da.get_raw_items(rid)) == len(rows)


async def test_normalize_rerun_delete_then_insert_keeps_single_batch(store):
    """F3:normalize 重跑(delete_normalized + save_normalized × 2)→ 单批行。"""
    da, _ = store
    rid = await _mk_report(da)
    rows = [_norm("空腹血糖", "GLU"), _norm("未知项", None)]
    for _ in range(2):
        await da.delete_normalized(rid)
        await da.save_normalized(rid, rows)
    assert len(await da.get_normalized(rid)) == len(rows)


async def test_delete_scoped_to_target_report(store):
    """F3:delete 按 report_id 过滤,不影响其他报告的 raw/normalized 行。"""
    da, _ = store
    r1, r2 = await _mk_report(da), await _mk_report(da)
    await da.save_raw_items(r1, [_raw("A", 1.0)])
    await da.save_raw_items(r2, [_raw("B", 2.0)])
    await da.save_normalized(r1, [_norm("A", "A1")])
    await da.save_normalized(r2, [_norm("B", "B1")])
    await da.delete_raw_items(r1)
    await da.delete_normalized(r1)
    assert await da.get_raw_items(r1) == []
    assert await da.get_raw_items(r2)  # 其他报告行不受影响
    assert await da.get_normalized(r1) == []
    assert await da.get_normalized(r2)


async def test_plan_rerun_without_delete_collides_on_task_unique(store):
    """F3 崩溃窗口复现:plan 重跑未删旧行 → 第二次 save_interpretation 撞 task_id 唯一
    约束(IntegrityError)→ 这正是修复前"任务永久 failed"的根因。"""
    da, factory = store
    rid = await _mk_report(da)
    task = await TaskService(factory).create(rid)
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d"}
    await da.save_interpretation(rid, task.id, doc)
    with pytest.raises(IntegrityError):
        await da.save_interpretation(rid, task.id, doc)


async def test_plan_rerun_delete_then_insert_single_rows(store):
    """F3:plan 重跑 = 按 task_id 删旧解读/复查行后插入 → 不再抛错,每 task 各一行。"""
    da, factory = store
    rid = await _mk_report(da)
    task = await TaskService(factory).create(rid)
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d"}
    for _ in range(2):
        await da.delete_interpretation(task.id)
        await da.save_interpretation(rid, task.id, doc)
    for _ in range(2):
        await da.delete_followup(task.id)
        await da.save_followup(rid, task.id, {"items": [], "degraded": True})
    assert await _count(factory, models.InterpretationRow,
                        models.InterpretationRow.task_id == task.id) == 1
    assert await _count(factory, models.FollowupPlanRow,
                        models.FollowupPlanRow.task_id == task.id) == 1


async def test_plan_delete_scoped_to_task_keeps_other_task_rows(store):
    """F3:delete_interpretation/delete_followup 按 task_id 过滤 —— 同报告先后两个任务
    (新任务重跑)只删自己的旧行,不影响其他任务已落库的解读/复查单。"""
    da, factory = store
    rid = await _mk_report(da)
    svc = TaskService(factory)
    t1 = await svc.create(rid)
    t2 = await svc.create(rid)
    doc = {"summary": "s", "items": [], "advice_summary": "a", "disclaimer": "d"}
    for t in (t1, t2):
        await da.save_interpretation(rid, t.id, doc)
        await da.save_followup(rid, t.id, {"items": [], "degraded": False})
    await da.delete_interpretation(t1.id)
    await da.delete_followup(t1.id)
    assert await _count(factory, models.InterpretationRow,
                        models.InterpretationRow.task_id == t1.id) == 0
    assert await _count(factory, models.InterpretationRow,
                        models.InterpretationRow.task_id == t2.id) == 1
    assert await _count(factory, models.FollowupPlanRow,
                        models.FollowupPlanRow.task_id == t1.id) == 0
    assert await _count(factory, models.FollowupPlanRow,
                        models.FollowupPlanRow.task_id == t2.id) == 1


async def test_unparsed_tables_delete_then_insert_and_section_map(store):
    da, factory = store
    rid = await _mk_report(da)
    htmls = ["<table><tr><td>x</td></tr></table>", "<table><tr><td>y</td></tr></table>"]

    await da.save_unparsed_tables(rid, "task-1", htmls)
    assert await _count(factory, models.UnparsedTable,
                        models.UnparsedTable.report_id == rid) == 2

    # F3: 先删后写 —— 重跑只留第二批,不累积
    await da.delete_unparsed_tables(rid)
    await da.save_unparsed_tables(rid, "task-1", htmls[:1])
    assert await _count(factory, models.UnparsedTable,
                        models.UnparsedTable.report_id == rid) == 1

    # section 映射:空表 → {};手工插入后命中(spec §9.2)
    assert await da.get_section_map() == {}
    async with factory() as s:
        s.add(models.ItemSectionMapping(item_name="白细胞计数", section="血常规"))
        await s.commit()
    assert await da.get_section_map() == {"白细胞计数": "血常规"}


async def test_list_sessions_filters_empty_orders_desc_and_previews(store):
    """追问会话列表:只返回有消息的会话(过滤历史空会话),created_at 倒序;
    preview = 首条 user 提问截断(无 user 消息回退最早消息),计数 = 消息总条数。"""
    da, factory = store
    rid = await _mk_report(da)
    from datetime import UTC, datetime, timedelta

    async with factory() as s:
        # sqlite 方言落库时丢弃 tzinfo,仅满足 DTZ001;读回 naive,断言不依赖 tz
        base = datetime(2026, 9, 8, 10, 0, 0, tzinfo=UTC)
        empty = models.ChatSession(report_id=rid, created_at=base - timedelta(minutes=10))
        older = models.ChatSession(report_id=rid, created_at=base)
        newer = models.ChatSession(report_id=rid, created_at=base + timedelta(minutes=2))
        s.add_all([empty, older, newer])
        await s.flush()
        s.add_all([
            models.ChatMessage(session_id=older.id, role="user",
                               content="血糖偏高怎么调理", created_at=base),
            models.ChatMessage(session_id=older.id, role="assistant",
                               content="建议饮食调整……", created_at=base + timedelta(minutes=1)),
            models.ChatMessage(session_id=newer.id, role="user",
                               content="转氨酶升高严重吗" + "补充说明" * 15,
                               created_at=base + timedelta(minutes=2)),
        ])
        await s.commit()
        want = {"older": older.id, "newer": newer.id}
    rows = await da.list_sessions(rid)
    assert [r["session_id"] for r in rows] == [want["newer"], want["older"]]  # 最近在前
    assert rows[0]["message_count"] == 1
    assert rows[0]["preview"].startswith("转氨酶升高严重吗")
    assert len(rows[0]["preview"]) <= 30  # 截断作会话标题
    assert rows[1]["message_count"] == 2
    assert rows[1]["preview"] == "血糖偏高怎么调理"
    assert rows[1]["created_at"] is not None
    assert await da.list_sessions("no-such-report") == []


async def test_get_report_detail_section_from_mapping(store):
    da, factory = store
    rid = await _mk_report(da)
    await da.save_raw_items(rid, [_raw("空腹血糖", 6.8)])
    async with factory() as s:
        s.add(models.ItemSectionMapping(item_name="空腹血糖", section="糖代谢"))
        await s.commit()
    detail = await da.get_report_detail(rid)
    assert detail["items"][0]["name"] == "空腹血糖"
    assert detail["items"][0]["section"] == "糖代谢"  # 查映射表


def _judgment(it: NormalizedItem, status: ItemStatus, low: float, high: float) -> ItemJudgment:
    return ItemJudgment(indicator_code=it.indicator_code, name=it.name, value_num=it.value_num,
                        value_text=it.value_text, unit=it.unit, status=status,
                        ref_low=low, ref_high=high, critical=False, range_source="report")


async def test_apply_judgments_writes_when_aligned(store):
    """读取→判定→回写(判定由 get_normalized 结果构造)→ 逐行写对,状态/区间不错配。"""
    da, _ = store
    rid = await _mk_report(da)
    await da.save_normalized(rid, [_norm("空腹血糖", "GLU"), _norm("白细胞", "WBC")])
    stored = await da.get_normalized(rid)
    statuses = {"空腹血糖": ItemStatus.HIGH, "白细胞": ItemStatus.LOW}
    await da.apply_judgments(
        rid, [_judgment(it, statuses[it.name], 3.9, 6.1) for it in stored])
    by_name = {n["item_name"]: n for n in (await da.get_report_detail(rid))["normalized"]}
    assert by_name["空腹血糖"]["status"] == "high"
    assert by_name["白细胞"]["status"] == "low"
    assert by_name["白细胞"]["ref_low"] == 3.9 and by_name["白细胞"]["ref_high"] == 6.1


async def test_apply_judgments_raises_on_misaligned_rows(store):
    """(修复:QA 夹具错配)行按 id(uuid4)排序,与落库/传入顺序无关 —— judgments 与
    get_normalized 不同序时必须抛错,而不是把状态/参考区间写到别的项目上。"""
    da, _ = store
    rid = await _mk_report(da)
    await da.save_normalized(rid, [_norm("空腹血糖", "GLU"), _norm("白细胞", "WBC")])
    stored = await da.get_normalized(rid)
    misaligned = [_judgment(it, ItemStatus.HIGH, 3.9, 6.1) for it in reversed(stored)]
    with pytest.raises(ValueError, match="错位"):
        await da.apply_judgments(rid, misaligned)
    # 抛错发生在 commit 前 → 不留半截脏数据
    by_name = {n["item_name"]: n for n in (await da.get_report_detail(rid))["normalized"]}
    assert all(n["status"] is None for n in by_name.values())
