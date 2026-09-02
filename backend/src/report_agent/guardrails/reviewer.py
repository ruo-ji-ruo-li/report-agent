# guardrails/reviewer.py
from dataclasses import dataclass, field

from report_agent.llm.client import LLMError
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger

log = get_logger(__name__)


@dataclass
class ReviewResult:
    passed: bool
    issues: list[str] = field(default_factory=list)


async def review_output(text: str, context: str, llm) -> ReviewResult:
    """二次 LLM 审核(仅风险信号触发,独立审核 prompt)。LLMError → 不通过(保守)。"""
    prompt = load_prompt("reviewer")
    try:
        data = await llm.complete_json(
            [{"role": "user", "content": prompt.format(text=text, context=context)}]
        )
        return ReviewResult(passed=bool(data.get("passed")), issues=list(data.get("issues") or []))
    except LLMError:
        log.warning("reviewer_llm_failed_conservative_fail")
        return ReviewResult(passed=False, issues=["审核服务不可用,保守拒绝"])
