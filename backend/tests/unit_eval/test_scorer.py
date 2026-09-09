"""scorer 单测(无 infra,mock LLM;评测升级 spec §5)。"""
from report_agent.eval.scorer import (
    JudgeResult,
    aggregate_scores,
    judge_evidence,
    judge_interpretation,
    judge_qa,
)


class FakeJudgeLLM:
    def __init__(self, data=None, exc=None):
        self.data = data or {"criteria": {"accuracy": 5, "grounded": 4,
                                          "safety": 5, "actionable": 3},
                             "rationale": "ok", "issues": []}
        self.exc = exc
        self.messages = None

    async def complete_json(self, messages, retry_feedback=True, max_tokens=4096):
        self.messages = messages
        if self.exc:
            raise self.exc
        return self.data


async def test_judge_interpretation_score_normalization():
    llm = FakeJudgeLLM()
    r = await judge_interpretation(llm, "ALT", "high",
                                   "52 U/L(参考区间 9~50)", ["证据文本"], "解读文本")
    assert r.score == round(17 / 20, 4)  # (5+4+5+3)/20 → 0.85
    assert r.target == "interpretation" and r.unit_id == "ALT"
    assert "ALT" in llm.messages[0]["content"]  # prompt 含指标名


async def test_judge_degrades_on_llm_error():
    from report_agent.llm.client import LLMError

    r = await judge_interpretation(FakeJudgeLLM(exc=LLMError("down")), "ALT", "high",
                                   "52", [], "解读")
    assert r.score is None and "down" in r.error


async def test_judge_non_dict_output_degrades():
    r = await judge_interpretation(FakeJudgeLLM(data=[1, 2]), "ALT", "high", "52", [], "解读")
    assert r.score is None and "非 dict" in r.error


async def test_judge_missing_criteria_degrades():
    r = await judge_qa(FakeJudgeLLM(data={"criteria": {"accuracy": 5}}),
                       "我的血糖是多少?", "答案是 4.89。", "报告项")
    assert r.score is None and "解析失败" in r.error


async def test_judge_evidence_two_criteria():
    llm = FakeJudgeLLM(data={"criteria": {"relevance": 4, "support": 2},
                             "rationale": "", "issues": []})
    r = await judge_evidence(llm, "TG", "high", ["证据文本"])
    assert r.score == 0.6 and r.target == "evidence"


def test_aggregate_skips_none_and_handles_empty():
    rs = [JudgeResult(target="qa", unit_id="1", score=0.8),
          JudgeResult(target="qa", unit_id="2", score=None, error="x")]
    assert aggregate_scores(rs) == 0.8
    assert aggregate_scores([]) is None
    assert aggregate_scores([JudgeResult(target="qa", unit_id="1", score=None)]) is None


def test_qa_report_items_text_formats_items():
    from report_agent.eval.runner import qa_report_items_text
    from report_agent.parsing.schemas import NormalizedItem

    items = [NormalizedItem(raw_index=0, name="甘油三酯", indicator_code="TG",
                            value_text="3.10", value_num=3.1, unit="mmol/L",
                            raw_value_num=3.1, raw_unit="mmol/L",
                            ref_range_text="0.45-1.81", range_from="report")]
    text = qa_report_items_text(items)
    assert "甘油三酯" in text and "3.10" in text and "0.45-1.81" in text
