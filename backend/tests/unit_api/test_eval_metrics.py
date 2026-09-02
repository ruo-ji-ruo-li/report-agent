from report_agent.eval_metrics import compare_baseline, compute_code_f1, status_accuracy


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
