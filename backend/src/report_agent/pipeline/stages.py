"""管线阶段函数。STAGE_FUNCS 注册表:Task 11 注册 parse/normalize/compare,
Task 12 注册 retrieve/generate/plan,Task 13 注册 guardrail。
每个阶段:读 ctx(db/checkpoints)→ 计算 → 写 db → 返回 checkpoint payload;失败抛异常。
"""
import asyncio
from dataclasses import dataclass, field

from report_agent.observability import get_logger
from report_agent.parsing.parse_report import parse_report
from report_agent.parsing.schemas import ReportMeta
from report_agent.pipeline.rule_compare import judge_all, match_patterns

log = get_logger(__name__)

STAGE_ORDER = ["parse", "normalize", "compare", "retrieve", "generate", "guardrail", "plan"]


@dataclass
class StageContext:
    task_id: str
    report: dict
    checkpoints: dict = field(default_factory=dict)
    deps: object = None  # PipelineDeps(build 见 runner)
    db: object = None    # DataAccess
    tasks: object = None  # TaskService


STAGE_FUNCS: dict[str, callable] = {}


def register(name: str):
    def deco(fn):
        STAGE_FUNCS[name] = fn
        return fn
    return deco


@register("parse")
async def parse_stage(ctx: StageContext) -> dict:
    if ctx.report["source"] == "manual":
        # 手动录入:items 已在 API 层写入 raw_items,这里只校验
        items = await ctx.db.get_raw_items(ctx.report["id"])
        if not items:
            raise ValueError("手动录入未提供任何检验项")
        return {"method": "manual", "n_items": len(items), "needs_meta": False}

    out = await parse_report(
        ctx.report["file_path"], ctx.report["source"], ctx.deps.settings, ctx.deps.llms
    )
    if out is None:
        raise ValueError("解析失败:不支持的来源")
    await ctx.db.update_report_meta(ctx.report["id"], out.meta)
    await ctx.db.save_raw_items(ctx.report["id"], out.items)
    needs_meta = out.meta.sex is None or out.meta.age is None
    if needs_meta:
        log.warning("report_meta_missing", task_id=ctx.task_id,
                    sex=out.meta.sex, age=out.meta.age)
    return {"method": out.method, "n_items": len(out.items), "needs_meta": needs_meta}


@register("normalize")
async def normalize_stage(ctx: StageContext) -> dict:
    raw_items = await ctx.db.get_raw_items(ctx.report["id"])
    items = await ctx.deps.normalizer.normalize(raw_items, llm=ctx.deps.llms.chat)
    await ctx.db.save_normalized(ctx.report["id"], items)
    return {"n_items": len(items)}


@register("compare")
async def compare_stage(ctx: StageContext) -> dict:
    report = ctx.report
    if report["sex"] is None or report["age"] is None:
        raise ValueError("报告缺少性别/年龄,无法判定参考区间;请先补录 meta(PATCH /api/reports/{id}/meta)")
    items = await ctx.db.get_normalized(ctx.report["id"])

    meta = ReportMeta(sex=report["sex"], age=report["age"])
    specs = {}
    for it in items:
        if it.indicator_code and it.indicator_code not in specs:
            specs[it.indicator_code] = ctx.deps.kg.range_specs(it.indicator_code)
    judgments = judge_all(items, specs, meta)
    await ctx.db.apply_judgments(ctx.report["id"], judgments)
    abnormal = [j for j in judgments if j.status.value not in ("normal", "unknown", "unmapped")]
    critical = [j for j in abnormal if j.critical]
    matched = [
        (p.name, [j.name for j in js])
        for p, js in match_patterns(judgments, ctx.deps.kg.all_patterns())
    ]
    return {
        "n_abnormal": len(abnormal),
        "critical_count": len(critical),
        "matched_patterns": matched,
        "judgments": [
            {"indicator_code": j.indicator_code, "name": j.name, "status": j.status.value,
             "value_num": j.value_num, "value_text": j.value_text, "unit": j.unit,
             "ref_low": j.ref_low, "ref_high": j.ref_high, "critical": j.critical,
             "range_source": j.range_source}
            for j in judgments
        ],
    }


@register("retrieve")
async def retrieve_stage(ctx: StageContext) -> dict:
    """逐异常项并发检索(信号量限流)。单项失败 → 该项占位证据(spec §11)。"""
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus
    from report_agent.retrieval.hybrid import RetrievalQuery

    judgments = ctx.checkpoints.get("compare", {}).get("judgments", [])
    abnormal = [
        ItemJudgment(indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
                     value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
                     ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
                     range_source=j["range_source"])
        for j in judgments
        if j["status"] in ("high", "low", "critical_high", "critical_low")
    ]
    sem = asyncio.Semaphore(ctx.deps.settings.retrieve_concurrency)

    async def one(j: ItemJudgment) -> tuple[str, list[dict]]:
        async with sem:
            q = RetrievalQuery(
                text=f"{j.name} {j.status.value} 健康风险", indicator_code=j.indicator_code,
                direction="high" if j.status.value.endswith("high") else "low",
            )
            evs = await ctx.deps.retriever.search(q)
            if not evs:
                from report_agent.retrieval.hybrid import Evidence

                evs = [Evidence(text="知识库未覆盖该项,建议线下咨询医生。", source="placeholder")]
            key = j.indicator_code or j.name
            return key, [
                {"text": e.text, "source": e.source, "entity_type": e.entity_type,
                 "entity_id": e.entity_id, "title": e.title, "score": e.score,
                 "rrf_score": e.rrf_score, "rrf_sources": e.rrf_sources}
                for e in evs
            ]

    results = await asyncio.gather(*(one(j) for j in abnormal))
    return {"evidence": dict(results)}


@register("generate")
async def generate_stage(ctx: StageContext) -> dict:
    from report_agent.pipeline.interpret import (
        ItemInterpretation,
        assemble_interpretation,
        build_advice_summary,
        generate_summary,
        interpret_item,
    )
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus
    from report_agent.retrieval.hybrid import Evidence

    judgments = ctx.checkpoints.get("compare", {}).get("judgments", [])
    evidence = ctx.checkpoints.get("retrieve", {}).get("evidence", {})
    abnormal = [j for j in judgments if j["status"] in
                ("high", "low", "critical_high", "critical_low")]

    async def one(j: dict) -> ItemInterpretation:
        key = j["indicator_code"] or j["name"]
        evs = [Evidence(text=e["text"], source=e["source"], entity_type=e["entity_type"],
                        entity_id=e["entity_id"], title=e["title"], score=e["score"],
                        rrf_score=e["rrf_score"], rrf_sources=e["rrf_sources"] or [])
               for e in evidence.get(key, [])]
        kctx = None
        if j["indicator_code"]:
            kctx = ctx.deps.kg.indicator_context(j["indicator_code"])
        item_judgment = ItemJudgment(
            indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
            value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
            ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
            range_source=j["range_source"],
        )
        return await interpret_item(item_judgment, kctx, evs, ctx.deps.llms.chat)

    items = await asyncio.gather(*(one(j) for j in abnormal))
    matched = [m[0] for m in ctx.checkpoints.get("compare", {}).get("matched_patterns", [])]
    unknown = sum(1 for j in judgments if j["status"] == "unknown")
    summary = await generate_summary(items, matched, unknown, ctx.deps.llms.chat)
    doc = assemble_interpretation(summary, items, build_advice_summary(items), degraded=False)
    return {"doc": doc, "interpretations": [
        {"indicator_code": i.indicator_code, "name": i.name, "status": i.status,
         "value_text": i.value_text, "meaning": i.meaning, "risks": i.risks,
         "advice_level": i.advice_level, "advice": i.advice, "evidence_ids": i.evidence_ids}
        for i in items
    ]}


@register("plan")
async def plan_stage(ctx: StageContext) -> dict:
    """复查计划生成 + 落库解读与复查单。"""
    from report_agent.pipeline.followup_plan import build_followup_plan
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus

    judgments = [
        ItemJudgment(indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
                     value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
                     ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
                     range_source=j["range_source"])
        for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
    ]
    matched = [
        (name, [j for j in judgments if j.name in hits])
        for name, hits in ctx.checkpoints.get("compare", {}).get("matched_patterns", [])
    ]
    ctx_by_code = {}
    for j in judgments:
        if j.indicator_code and j.indicator_code not in ctx_by_code:
            ctx_by_code[j.indicator_code] = ctx.deps.kg.indicator_context(j.indicator_code)
    plan = await build_followup_plan(judgments, matched, ctx_by_code, ctx.deps.llms.chat)

    doc = ctx.checkpoints.get("generate", {}).get("doc", {})
    # 解读行 degraded 只取护栏内容安全降级语义(spec §5.5);复查单自身模板降级
    # (plan.degraded)只留在 followup_plans.degraded 列 —— 评审裁决 Important-1(b)
    degraded = bool(ctx.checkpoints.get("guardrail", {}).get("degraded", False))
    await ctx.db.save_interpretation(ctx.report["id"], ctx.task_id, {**doc, "degraded": degraded})
    await ctx.db.save_followup(
        ctx.report["id"], ctx.task_id,
        {"items": [
            {"item": i.item, "timeframe": i.timeframe, "department": i.department, "basis": i.basis}
            for i in plan.items
        ], "degraded": plan.degraded},
    )
    return {"n_items": len(plan.items), "degraded": plan.degraded}
