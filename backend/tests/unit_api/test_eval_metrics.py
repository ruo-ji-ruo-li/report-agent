from report_agent.eval_metrics import (
    compare_baseline,
    compute_code_f1,
    guardrail_violations,
    status_accuracy,
)


def test_code_f1_perfect_and_partial():
    assert compute_code_f1(["GLU", "WBC"], ["GLU", "WBC"]) == 1.0
    f1 = compute_code_f1(["GLU", None], ["GLU", "WBC"])
    assert 0.4 < f1 < 0.7  # P=1.0 R=0.5 → F1=0.667


def test_status_accuracy_requires_perfect():
    assert status_accuracy(["high"], ["high"]) == 1.0
    assert status_accuracy(["high"], ["low"]) == 0.0


def test_compare_baseline_detects_regression():
    reg = compare_baseline({"f1": 0.8, "acc": 1.0}, {"f1": 0.9, "acc": 1.0})
    assert reg == ["f1"]
    assert compare_baseline({"f1": 0.95}, {"f1": 0.9}) == []
    # 容差 0.05:单条 QA 的 LLM 波动(1/30≈0.033)属噪声,不算回退
    assert compare_baseline({"refusal_correct": 0.9667}, {"refusal_correct": 1.0}) == []


def test_guardrail_suspect_numeric_counts_numeric_not_safety():
    """评审 I-①:数值越界 → SUSPECT,计数值维违规、安全维不违规(两维解耦)。"""
    sv, nv = guardrail_violations("suspect", ["越界数值: 999"])
    assert (sv, nv) == (False, True)
    # 越界解读样本:1 条违规 + 1 条合规 → numeric_consistency < 1.0 且 safety_violations == 0
    results = [
        guardrail_violations("suspect", ["越界数值: 999"]),
        guardrail_violations("pass", []),
    ]
    violations = sum(nv for _, nv in results)
    safety = sum(sv for sv, _ in results)
    assert 1 - violations / len(results) < 1.0
    assert safety == 0


def test_guardrail_block_counts_safety_not_numeric():
    """评审 I-①:BLOCK(诊断/处方/必含元素)只计安全维,数值维不判。"""
    sv, nv = guardrail_violations("block", ["诊断用语: 确诊"])
    assert (sv, nv) == (True, False)
    assert guardrail_violations("pass", []) == (False, False)
