"""逐项解读生成 + 总评(规则模板兜底)+ 四段式组装 + 安全降级版。spec §3.2/§3.4/§5.4。"""
from dataclasses import dataclass

from report_agent.knowledge.kg_client import IndicatorContext
from report_agent.llm.client import LLMError
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger
from report_agent.pipeline.rule_compare import ItemJudgment, ItemStatus
from report_agent.retrieval.hybrid import Evidence

log = get_logger(__name__)

DISCLAIMER = (
    "【免责声明】本解读由系统基于您的体检报告数据与医学知识库自动生成,仅供健康参考,"
    "不构成医学诊断、治疗或用药建议。如有不适或疑问,请咨询执业医师。"
)
ADVICE_LEVELS = ["lifestyle", "recheck", "specialist", "urgent"]


@dataclass
class ItemInterpretation:
    indicator_code: str | None
    name: str
    status: str
    value_text: str
    meaning: str
    risks: list[str]
    advice_level: str
    advice: str
    evidence_ids: list[str]


def _status_cn(status: ItemStatus) -> str:
    return {
        ItemStatus.HIGH: "升高", ItemStatus.LOW: "降低",
        ItemStatus.CRITICAL_HIGH: "显著升高(危急值)", ItemStatus.CRITICAL_LOW: "显著降低(危急值)",
        ItemStatus.NORMAL: "正常", ItemStatus.UNKNOWN: "无法判定", ItemStatus.UNMAPPED: "未识别",
    }[status]


def _value_text(j: ItemJudgment) -> str:
    v = f"{j.value_num} {j.unit}".strip() if j.value_num is not None else (j.value_text or "-")
    if j.ref_low is not None or j.ref_high is not None:
        lo = j.ref_low if j.ref_low is not None else "-∞"
        hi = j.ref_high if j.ref_high is not None else "+∞"
        return f"{v}(参考区间 {lo}~{hi})"
    return v


def _fallback_interpretation(j: ItemJudgment) -> ItemInterpretation:
    return ItemInterpretation(
        indicator_code=j.indicator_code, name=j.name, status=j.status.value,
        value_text=_value_text(j), meaning=f"{j.name}检测结果为{_status_cn(j.status)}。",
        risks=[], advice_level="specialist" if j.critical else "recheck",
        advice=("该结果达到危急值水平,建议尽快线下就医评估。" if j.critical
                else "建议携带完整报告线下咨询医生进一步评估。"),
        evidence_ids=[],
    )


async def interpret_item(
    j: ItemJudgment, ctx: IndicatorContext | None, evidence: list[Evidence], llm,
    feedback: str | None = None,
) -> ItemInterpretation:
    if llm is None:
        return _fallback_interpretation(j)
    prompt = load_prompt("interpret_item")
    ev_block = "\n\n".join(
        f"[e{i}](来源:{e.source},标题:{e.title or ''})\n{e.text}" for i, e in enumerate(evidence)
    ) or "(无检索证据,请按知识库覆盖不足处理)"
    kg_facts = ""
    if ctx is not None:
        kg_facts = (
            f"知识图谱事实: 升高提示 {[f'{c.name}({c.strength})' for c in ctx.high_suggests]}; "
            f"降低提示 {[f'{c.name}({c.strength})' for c in ctx.low_suggests]}; "
            f"建议 {[f'{i.level}:{i.text}' for i in ctx.interventions]}; 科室 {ctx.departments}"
        )
    content = prompt.format(
        name=j.name, status=_status_cn(j.status), value=_value_text(j),
        kg_facts=kg_facts, evidence=ev_block,
    )
    if feedback:
        # 护栏重生成注入上次未过审原因(spec §5.5 带问题反馈重生成;评审 I-2④)
        content += f"\n\n上次护栏未过审原因(本次必须修复):\n{feedback}"
    messages = [{"role": "user", "content": content}]
    try:
        data = await llm.complete_json(messages)
        if not isinstance(data, dict):
            # 合法 JSON 但非 dict(数组/字符串)→ 视同 LLM 失败走模板,不得冒泡
            # (对齐 Task 6 先例 7e1bf73)—— 评审裁决 Important-2
            log.warning("interpret_item_llm_bad_shape", name=j.name, shape=type(data).__name__)
            return _fallback_interpretation(j)
        return ItemInterpretation(
            indicator_code=j.indicator_code, name=j.name, status=j.status.value,
            value_text=_value_text(j), meaning=data.get("meaning", ""),
            risks=list(data.get("risks") or []),
            advice_level=data.get("advice_level") if data.get("advice_level") in ADVICE_LEVELS
            else "recheck",
            advice=data.get("advice", ""),
            evidence_ids=[e for e in data.get("evidence_ids", []) if isinstance(e, str)],
        )
    except LLMError:
        log.warning("interpret_item_llm_failed", name=j.name)
        return _fallback_interpretation(j)


def template_summary(n_abnormal: int, critical_count: int, matched_patterns: list[str],
                     unknown_count: int) -> str:
    parts = [f"本次体检共发现 {n_abnormal} 项异常。"]
    if critical_count:
        parts.append(f"其中 {critical_count} 项达到危急值水平,请尽快线下就医。")
    if matched_patterns:
        parts.append(f"检出组合模式:{'、'.join(matched_patterns)},建议综合评估。")
    if unknown_count:
        parts.append(f"另有 {unknown_count} 项指标无法判定,请咨询医生。")
    return "".join(parts)


async def generate_summary(items: list[ItemInterpretation], matched_patterns: list[str],
                           unknown_count: int, llm, feedback: str | None = None) -> str:
    n_abnormal = len(items)
    critical = sum(1 for i in items if i.status.startswith("critical"))
    try:
        prompt = load_prompt("summary")
        items_block = "\n".join(
            f"- {i.name}: {_value_text_from_item(i)}, 建议级别 {i.advice_level}"
            for i in items
        )
        content = prompt.format(
            items=items_block, matched_patterns="、".join(matched_patterns) or "无",
            unknown_count=unknown_count,
        )
        if feedback:
            # 护栏重生成注入上次未过审原因(spec §5.5;评审 I-2④)
            content += f"\n\n上次护栏未过审原因(本次必须修复):\n{feedback}"
        messages = [{"role": "user", "content": content}]
        return (await llm.chat(messages, temperature=0.1)).strip()
    except LLMError:
        log.warning("summary_llm_failed_using_template")
        return template_summary(n_abnormal, critical, matched_patterns, unknown_count)


def _value_text_from_item(i: ItemInterpretation) -> str:
    return i.value_text


def build_advice_summary(items: list[ItemInterpretation]) -> str:
    """代码排序:urgent > specialist > recheck > lifestyle,同级按危急值优先。

    注:ADVICE_LEVELS 为升序(lifestyle→urgent),故映射倒序使 urgent 排最前,
    brief 原 test_advice_summary_orders_urgent_first 锁定该输出顺序。
    """
    order = {lvl: i for i, lvl in enumerate(reversed(ADVICE_LEVELS))}

    def key(it: ItemInterpretation):
        return (order.get(it.advice_level, 99), 0 if it.status.startswith("critical") else 1)

    lines = []
    for it in sorted(items, key=key):
        label = {"urgent": "尽快就医", "specialist": "专科就诊",
                 "recheck": "定期复查", "lifestyle": "生活方式调整"}.get(it.advice_level, "随访")
        lines.append(f"【{label}】{it.name}: {it.advice}")
    return "\n".join(lines)


def assemble_interpretation(summary: str, items: list[ItemInterpretation],
                            advice_summary: str, degraded: bool) -> dict:
    return {
        "summary": summary,
        "items": [
            {"indicator_code": i.indicator_code, "name": i.name, "status": i.status,
             "value_text": i.value_text, "meaning": i.meaning, "risks": i.risks,
             "advice_level": i.advice_level, "advice": i.advice, "evidence_ids": i.evidence_ids}
            for i in items
        ],
        "advice_summary": advice_summary,
        "disclaimer": DISCLAIMER,
        "degraded": degraded,
    }


def build_degraded_interpretation(judgments: list[ItemJudgment]) -> dict:
    """安全降级版:仅数值对照表 + 强建议线下就医(spec §4.2/§5.5)。"""
    abnormal = [j for j in judgments if j.status.value in
                ("high", "low", "critical_high", "critical_low")]
    table = "\n".join(
        f"- {j.name}: {_value_text(j)},判定 {_status_cn(j.status)}" for j in abnormal
    ) or "- 无异常项"
    summary = (
        f"因内容安全校验未通过,本次仅提供数值对照结果,不提供解释性内容。\n{table}\n"
        "请携带本报告尽快线下就医咨询,由执业医师评估。"
    )
    return assemble_interpretation(
        summary=summary,
        items=[ItemInterpretation(
            indicator_code=j.indicator_code, name=j.name, status=j.status.value,
            value_text=_value_text(j), meaning="", risks=[],
            advice_level="urgent" if j.critical else "specialist",
            advice="请线下就医咨询。", evidence_ids=[]) for j in abnormal],
        advice_summary="所有异常项请以线下医师意见为准。",
        degraded=True,
    )
