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
        # F2: manual 与 pdf/photo 同语义 —— 缺 sex/age 必须暂停 awaiting_meta,
        # 否则 compare 抛错使任务永久 failed、PATCH meta 无从恢复(设计决策 2)
        needs_meta = ctx.report.get("sex") is None or ctx.report.get("age") is None
        if needs_meta:
            log.warning("report_meta_missing", task_id=ctx.task_id,
                        sex=ctx.report.get("sex"), age=ctx.report.get("age"))
        return {"method": "manual", "n_items": len(items), "needs_meta": needs_meta}

    out = await parse_report(
        ctx.report["file_path"], ctx.report["source"], ctx.deps.settings, ctx.deps.llms
    )
    if out is None:
        raise ValueError("解析失败:不支持的来源")
    await ctx.db.update_report_meta(ctx.report["id"], out.meta)
    # F3: 先删后写 —— 崩溃窗口(DB 写后、checkpoint 前)恢复重跑不累积重复行
    await ctx.db.delete_raw_items(ctx.report["id"])
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
    # F3: 报告级先删后写 —— 词典升级后"只重跑归一化及以下阶段"(spec §4.1)可执行
    await ctx.db.delete_normalized(ctx.report["id"])
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
    """逐异常项并发检索(信号量限流)。单项失败/无命中 → 该项占位证据(spec §11)。"""
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus
    from report_agent.retrieval.hybrid import RetrievalQuery

    audit = None
    factory = getattr(ctx.deps, "session_factory", None)
    if factory is not None:
        from report_agent.guardrails.audit import AuditLog

        audit = AuditLog(factory, defaults={"report_id": ctx.report["id"],
                                            "task_id": ctx.task_id})

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
            key = j.indicator_code or j.name
            if not evs:
                from report_agent.retrieval.hybrid import Evidence

                # F6(b): 占位即降级 —— 审计检索回退,验收 3(停 Neo4j 场景)可查
                if audit is not None:
                    await audit.log("retrieval_fallback", {"item": key})
                evs = [Evidence(text="知识库未覆盖该项,建议线下咨询医生。", source="placeholder")]
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


@register("guardrail")
async def guardrail_stage(ctx: StageContext) -> dict:
    """对生成产物执行护栏:总评 + 逐项解读逐一过检,重生成/降级由 enforce 闭环。

    必含元素按用户可见粒度施加(评审 Important-2 ①②):免责声明是四段式固定
    元素(assemble_interpretation 恒附,spec §5.4),不对纯文本逐字强索;危急强
    提醒只要求总评与危急项文本。generate checkpoint 只读(深拷贝),改写/降级
    结果以 guardrail checkpoint 的 doc 为唯一权威源(评审 Critical-1)。
    """
    import copy

    from report_agent.guardrails.audit import AuditLog
    from report_agent.guardrails.enforce import enforce_guardrail
    from report_agent.guardrails.rules import GuardrailContext, item_guardrail_text
    from report_agent.pipeline.interpret import build_degraded_interpretation
    from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus

    # 深拷贝:禁止原地改写 generate checkpoint 内容(live/resume 落库分歧根源)
    doc = copy.deepcopy(ctx.checkpoints.get("generate", {}).get("doc", {}))
    judgments = [
        ItemJudgment(indicator_code=j["indicator_code"], name=j["name"], value_num=j["value_num"],
                     value_text=j["value_text"], unit=j["unit"], status=ItemStatus(j["status"]),
                     ref_low=j["ref_low"], ref_high=j["ref_high"], critical=j["critical"],
                     range_source=j["range_source"])
        for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
    ]
    allowed = []
    for j in judgments:
        allowed.extend([x for x in (j.value_num, j.ref_low, j.ref_high) if x is not None])
    report = ctx.report
    if report.get("age") is not None:
        allowed.append(float(report["age"]))
    has_critical = any(j["critical"] for j in
                       ctx.checkpoints.get("compare", {}).get("judgments", []))
    sum_ctx = GuardrailContext(allowed_numbers=allowed, require_disclaimer=False,
                               require_critical_warning=has_critical)
    audit = AuditLog(ctx.deps.session_factory,
                     defaults={"report_id": ctx.report["id"], "task_id": ctx.task_id})
    final_doc = dict(doc)
    degraded = False
    findings_all = []

    # 1) 总评
    async def regen_summary(feedback):
        from report_agent.pipeline.interpret import ItemInterpretation, generate_summary

        # doc items 是四段式 dict,generate_summary 消费 ItemInterpretation 对象
        items = [
            ItemInterpretation(
                indicator_code=i.get("indicator_code"), name=i["name"], status=i["status"],
                value_text=i["value_text"], meaning=i.get("meaning", ""),
                risks=list(i.get("risks") or []), advice_level=i["advice_level"],
                advice=i.get("advice", ""), evidence_ids=list(i.get("evidence_ids") or []),
            )
            for i in doc.get("items", [])
        ]
        matched = [m[0] for m in ctx.checkpoints.get("compare", {}).get("matched_patterns", [])]
        unknown = sum(1 for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
                      if j["status"] == "unknown")
        return await generate_summary(items, matched, unknown, ctx.deps.llms.chat,
                                      feedback=feedback)

    async def degrade_summary():
        return build_degraded_interpretation(judgments)["summary"]

    summary, sum_degraded = await enforce_guardrail(
        doc.get("summary", ""), sum_ctx, regen_summary, degrade_summary,
        ctx.deps.llms.chat, audit,
    )
    final_doc["summary"] = summary
    degraded = degraded or sum_degraded
    findings_all.append({"part": "summary", "degraded": sum_degraded})

    # 2) 逐项解读(meaning/risks/advice 三槽合并检;危急强提醒只对危急项要求)
    #    F1:risks 是 LLM 自由文本(最多 3 条),必须与 meaning/advice 一并入检,
    #    否则"建议服用二甲双胍/确诊"类表述放进 risks 即绕过护栏直达用户。
    new_items = []
    for item in doc.get("items", []):
        item_ctx = GuardrailContext(
            allowed_numbers=allowed, require_disclaimer=False,
            require_critical_warning=str(item.get("status", "")).startswith("critical"),
        )
        text = item_guardrail_text(item["meaning"], item.get("risks") or [], item["advice"])

        holder: dict = {}

        async def regen_item(feedback, item=item, holder=holder):
            # 单项重生成:重跑 interpret_item(证据在 retrieve checkpoint),feedback 注入 prompt
            from report_agent.pipeline.interpret import interpret_item
            from report_agent.retrieval.hybrid import Evidence

            key = item["indicator_code"] or item["name"]
            evs = [Evidence(**e) for e in
                   ctx.checkpoints.get("retrieve", {}).get("evidence", {}).get(key, [])]
            j0 = next(j for j in ctx.checkpoints.get("compare", {}).get("judgments", [])
                      if (j["indicator_code"] or j["name"]) == key)
            jj = ItemJudgment(indicator_code=j0["indicator_code"], name=j0["name"],
                              value_num=j0["value_num"], value_text=j0["value_text"],
                              unit=j0["unit"], status=ItemStatus(j0["status"]),
                              ref_low=j0["ref_low"], ref_high=j0["ref_high"],
                              critical=j0["critical"], range_source=j0["range_source"])
            kctx = ctx.deps.kg.indicator_context(key) if j0["indicator_code"] else None
            new_i = await interpret_item(jj, kctx, evs, ctx.deps.llms.chat, feedback=feedback)
            holder["item"] = new_i  # 结构化带出,供护栏通过后三槽整体替换
            return item_guardrail_text(new_i.meaning, new_i.risks, new_i.advice)

        async def degrade_item(item=item):
            # 危急项降级文本须带可被危急强提醒校验检出的词元(评审 I-2③/⚠️-4)
            if str(item.get("status", "")).startswith("critical"):
                return f"{item['name']}达危急值水平,请尽快就医,具体请以线下医师意见为准。"
            return f"{item['name']}检测结果请以线下医师意见为准。"

        new_text, item_degraded = await enforce_guardrail(
            text, item_ctx, regen_item, degrade_item, ctx.deps.llms.chat, audit,
        )
        if item_degraded or (new_text != text):
            if item_degraded:
                # 降级写回:文本为代码模板;advice/risks 一并清空,不留 LLM 违规原文
                # (评审 I-1;F1:risks 与 advice 同语义)
                item["meaning"] = new_text
                item["advice"] = ""
                item["risks"] = []
            else:
                # 重生成通过:meaning/risks/advice 三槽整体替换(结构化,不经文本解析),
                # 消除"新 meaning/advice + 旧 risks"错位与绕过(F1)
                new_i = holder.get("item")
                if new_i is not None:
                    item["meaning"] = new_i.meaning
                    item["risks"] = list(new_i.risks)
                    item["advice"] = new_i.advice
                else:  # pragma: no cover —— 理论不可达:未降级必有重生成输出
                    item["meaning"] = new_text
                    item["advice"] = ""
                    item["risks"] = []
        degraded = degraded or item_degraded
        findings_all.append({"part": item["name"], "degraded": item_degraded})
        new_items.append(item)

    final_doc["items"] = new_items
    final_doc["degraded"] = degraded
    if degraded:
        final_doc["advice_summary"] = "所有异常项请以线下医师意见为准。"
    return {"doc": final_doc, "degraded": degraded, "findings": findings_all}


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

    # 解读行 doc 以 guardrail checkpoint 为权威源(generate 兜底,兼容旧任务/
    # guardrail-off)——护栏改写/降级内容必须到达用户可见行(spec §4.2/§5.5,评审 Critical-1)
    doc = (ctx.checkpoints.get("guardrail", {}).get("doc")
           or ctx.checkpoints.get("generate", {}).get("doc", {}))
    # 解读行 degraded 只取护栏内容安全降级语义(spec §5.5);复查单自身模板降级
    # (plan.degraded)只留在 followup_plans.degraded 列 —— 评审裁决 Important-1(b)
    degraded = bool(ctx.checkpoints.get("guardrail", {}).get("degraded", False))
    # F3: 落库前先删同 task_id 旧行 —— interpretations/followup_plans.task_id 唯一,
    # "两次 DB 写之后、plan checkpoint 之前崩溃 → 恢复重跑"不再撞唯一约束永久 failed
    await ctx.db.delete_interpretation(ctx.task_id)
    await ctx.db.delete_followup(ctx.task_id)
    await ctx.db.save_interpretation(ctx.report["id"], ctx.task_id, {**doc, "degraded": degraded})
    await ctx.db.save_followup(
        ctx.report["id"], ctx.task_id,
        {"items": [
            {"item": i.item, "timeframe": i.timeframe, "department": i.department, "basis": i.basis}
            for i in plan.items
        ], "degraded": plan.degraded},
    )
    # F6(c): 复查单模板降级(润色失败/润色 basis 未过护栏回退模板)→ 审计事件
    audit = None
    factory = getattr(ctx.deps, "session_factory", None)
    if factory is not None:
        from report_agent.guardrails.audit import AuditLog

        audit = AuditLog(factory, defaults={"report_id": ctx.report["id"],
                                            "task_id": ctx.task_id})
    if plan.degraded and audit is not None:
        await audit.log("followup_template_fallback", {"n_items": len(plan.items)})
    return {"n_items": len(plan.items), "degraded": plan.degraded}
