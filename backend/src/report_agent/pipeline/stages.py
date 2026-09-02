"""管线阶段函数。STAGE_FUNCS 注册表:Task 11 注册 parse/normalize/compare,
Task 12 注册 retrieve/generate/plan,Task 13 注册 guardrail。
每个阶段:读 ctx(db/checkpoints)→ 计算 → 写 db → 返回 checkpoint payload;失败抛异常。
"""
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
