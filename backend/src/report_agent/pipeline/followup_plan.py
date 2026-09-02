"""复查计划:规则收集(项目/时间窗/科室/依据)+ 模板渲染 + LLM 仅润色依据槽位。spec §5.6。"""
from dataclasses import dataclass

from report_agent.guardrails.rules import GuardrailContext, Verdict, rule_guardrail
from report_agent.knowledge.kg_client import IndicatorContext
from report_agent.llm.client import LLMError
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus

log = get_logger(__name__)

_ABNORMAL = {ItemStatus.HIGH, ItemStatus.LOW, ItemStatus.CRITICAL_HIGH, ItemStatus.CRITICAL_LOW}
# F1(b): LLM 润色 basis 的规则检查上下文 —— basis 是复查项目文本,不携报告数值,
# 故不挂数值白名单(有数字即视为不可信回退模板,不留 LLM 内容)
_BASIS_GCTX = GuardrailContext(require_disclaimer=False)


@dataclass
class FollowupItem:
    item: str       # 复查项目
    timeframe: str  # 时间窗
    department: str  # 挂号科室
    basis: str      # 依据(指标+判定+引用)


@dataclass
class FollowupPlanDoc:
    items: list[FollowupItem]
    degraded: bool


def build_followup_items(
    judgments: list[ItemJudgment],
    matched_patterns: list[tuple],
    ctx_by_code: dict[str, IndicatorContext],
) -> list[FollowupItem]:
    items: list[FollowupItem] = []
    for j in judgments:
        if j.status not in _ABNORMAL:
            continue
        ctx = ctx_by_code.get(j.indicator_code or "")
        if j.critical:
            items.append(FollowupItem(
                item=f"{j.name}(危急值)复查",
                timeframe="立即",
                department=next((d for d in (ctx.departments if ctx else [])), "急诊/相应专科"),
                basis=f"{j.name} 达危急值水平,需立即评估",
            ))
            continue
        recheck = next(
            (iv for iv in (ctx.interventions if ctx else []) if iv.level in ("recheck", "specialist")),
            None,
        )
        items.append(FollowupItem(
            item=recheck.text if recheck else f"复查{j.name}",
            timeframe=recheck.timeframe if recheck and recheck.timeframe else "遵医嘱",
            department="、".join(recheck.departments) if recheck and recheck.departments
            else ("、".join(ctx.departments) if ctx and ctx.departments else "相应专科"),
            basis=f"{j.name} 判定{'升高' if j.status.value.endswith('high') else '降低'},建议复查",
        ))
    for pattern, hits in matched_patterns:
        # plan_stage 传 (模式名str, [ItemJudgment]);直接构造时可传 (PatternSpec, [ItemJudgment])
        pname = pattern.name if hasattr(pattern, "name") else pattern
        hit_names = [h.name if hasattr(h, "name") else h for h in hits]
        items.append(FollowupItem(
            item=f"组合模式复查:{pname}",
            timeframe="遵医嘱",
            department="相应专科",
            basis=f"命中组合模式 {pname}(涉及:{'、'.join(hit_names)})",
        ))
    return items


async def smooth_basis(items: list[FollowupItem], llm) -> list[FollowupItem] | None:
    """LLM 仅润色 basis 槽位;输出 schema 校验(必须覆盖全部下标),失败返回 None 回退模板。"""
    if not items or llm is None:
        return None
    prompt = load_prompt("followup_smooth")
    block = "\n".join(f"{i}. 项目:{it.item} | 时间窗:{it.timeframe} | 科室:{it.department} "
                      f"| 依据:{it.basis}" for i, it in enumerate(items))
    try:
        data = await llm.complete_json(
            [{"role": "user", "content": prompt.format(items=block)}]
        )
        if set(data.keys()) != {str(i) for i in range(len(items))}:
            log.warning("followup_smooth_schema_mismatch")
            return None
        out = []
        for i, it in enumerate(items):
            out.append(FollowupItem(item=it.item, timeframe=it.timeframe,
                                    department=it.department, basis=data[str(i)]))
        return out
    except LLMError:
        log.warning("followup_smooth_llm_failed_using_template")
        return None


async def build_followup_plan(judgments, matched_patterns, ctx_by_code, llm) -> FollowupPlanDoc:
    items = build_followup_items(judgments, matched_patterns, ctx_by_code)
    try:
        smoothed = await smooth_basis(items, llm)
    except Exception as e:  # noqa: BLE001
        log.warning("followup_plan_error", error=str(e))
        smoothed = None
    basis_fallback = False
    if smoothed is not None:
        # F1(b): LLM 润色的 basis 槽位同样过规则护栏(诊断/药品/剂量/数值),否则
        # "复查建议用二甲双胍/已确诊"类表述经润色直达用户(spec §5.5 每 LLM 输出
        # → 护栏)。SUSPECT/BLOCK → 该条回退模板原文 basis 并记日志,不引发生成级重试。
        guarded: list[FollowupItem] = []
        for orig, sm in zip(items, smoothed, strict=True):
            result = rule_guardrail(sm.basis, _BASIS_GCTX)
            if result.verdict != Verdict.PASS:
                log.warning("followup_smooth_basis_guardrail_fallback",
                            findings=result.findings, item=orig.item)
                guarded.append(orig)
                basis_fallback = True
            else:
                guarded.append(sm)
        smoothed = guarded
    # 空复查单(全正常报告)无事可润色,不视为降级 —— 评审裁决 Important-1(a);
    # 润色失败或逐条 basis 回退模板 → 复查单 degraded(落 followup_plans.degraded)
    return FollowupPlanDoc(
        items=smoothed or items,
        degraded=(smoothed is None and len(items) > 0) or basis_fallback,
    )
