"""LLM 评分器(评测升级 spec §5):解读/问答/证据三个对象,rubric 打分 → 0~1。

judge 复用 llm_factory 的 chat 模型;judge 输出解析失败/调用失败 → 该单元降级
(score=None,不计入聚合分母),不阻断评测(spec §5.3)。评分 I/O 由调用方经
TracedLLM/ log_judge 落 trace。
"""
from dataclasses import dataclass, field

from report_agent.llm.prompts import load_prompt

JUDGE_CRITERIA = {
    "interpretation": ("accuracy", "grounded", "safety", "actionable"),
    "qa": ("accuracy", "completeness", "safety", "helpfulness"),
    "evidence": ("relevance", "support"),
}

STATUS_CN = {"high": "升高", "low": "降低", "critical_high": "显著升高(危急值)",
             "critical_low": "显著降低(危急值)", "normal": "正常", "unknown": "无法判定",
             "unmapped": "未识别"}


@dataclass
class JudgeResult:
    target: str           # interpretation / qa / evidence
    unit_id: str          # 评分单元标识(指标名 / 问题摘要)
    score: float | None   # 0~1;None = 降级(该单元不计入分母,spec §5.3)
    criteria: dict[str, float] = field(default_factory=dict)
    rationale: str = ""
    issues: list[str] = field(default_factory=list)
    error: str | None = None


def _parse_judge(target: str, unit_id: str, data: dict) -> JudgeResult:
    try:
        crit = {k: float(data["criteria"][k]) for k in JUDGE_CRITERIA[target]}
        if any(not (0 <= v <= 5) for v in crit.values()):
            raise ValueError(f"criteria 越界: {crit}")
        return JudgeResult(
            target=target, unit_id=unit_id,
            score=round(sum(crit.values()) / (5 * len(crit)), 4),
            criteria=crit, rationale=str(data.get("rationale") or ""),
            issues=[str(x) for x in (data.get("issues") or [])],
        )
    except (KeyError, TypeError, ValueError) as e:
        return JudgeResult(target=target, unit_id=unit_id, score=None,
                           error=f"judge 输出解析失败: {e}")


async def _judge_call(llm, target: str, unit_id: str, prompt: str) -> JudgeResult:
    try:
        data = await llm.complete_json([{"role": "user", "content": prompt}])
        if not isinstance(data, dict):
            return JudgeResult(target=target, unit_id=unit_id, score=None,
                               error=f"judge 输出非 dict: {type(data).__name__}")
        return _parse_judge(target, unit_id, data)
    except Exception as e:  # noqa: BLE001 —— LLMError 等:单元降级(spec §5.3)
        return JudgeResult(target=target, unit_id=unit_id, score=None, error=str(e))


async def judge_interpretation(llm, name: str, status: str, value_text: str,
                               evidence_texts: list[str], text: str) -> JudgeResult:
    ev_block = "\n\n".join(f"[e{i}]{t}" for i, t in enumerate(evidence_texts)) or "(无检索证据)"
    prompt = load_prompt("judge_interpretation").format(
        name=name, status=STATUS_CN.get(status, status), value=value_text,
        evidence=ev_block, text=text)
    return await _judge_call(llm, "interpretation", name, prompt)


async def judge_qa(llm, question: str, answer: str, report_items_text: str) -> JudgeResult:
    prompt = load_prompt("judge_qa").format(
        question=question, report_items=report_items_text, answer=answer)
    return await _judge_call(llm, "qa", question[:60], prompt)


async def judge_evidence(llm, name: str, direction: str, ev_texts: list[str]) -> JudgeResult:
    ev_block = "\n\n".join(f"[e{i}]{t}" for i, t in enumerate(ev_texts)) or "(无证据)"
    prompt = load_prompt("judge_evidence").format(
        name=name, direction=STATUS_CN.get(direction, direction), evidence=ev_block)
    return await _judge_call(llm, "evidence", name, prompt)


def aggregate_scores(results: list[JudgeResult]) -> float | None:
    """评分单元均值(spec §5.2);全部降级或空 → None。"""
    vals = [r.score for r in results if r.score is not None]
    return round(sum(vals) / len(vals), 4) if vals else None
