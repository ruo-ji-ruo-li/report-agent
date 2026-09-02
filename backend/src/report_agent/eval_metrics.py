"""评测纯函数(评测集口径见 spec §9)。"""


def compute_code_f1(pred: list[str | None], gt: list[str]) -> float:
    """名称对齐 F1:pred[i] 与 gt[i] 逐项比对(code 相同为命中,None 为未命中)。"""
    n = len(gt)
    if n == 0:
        return 1.0
    tp = sum(1 for p, g in zip(pred, gt, strict=True) if p == g and p is not None)
    precision = tp / max(len(pred), 1)
    recall = tp / n
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def status_accuracy(pred: list[str], gt: list[str]) -> float:
    """异常判定准确率(规则比对层要求 1.0,spec §4.5)。"""
    n = len(gt)
    if n == 0:
        return 1.0
    return sum(1 for p, g in zip(pred, gt, strict=True) if p == g) / n


def guardrail_violations(verdict: str, findings: list[str]) -> tuple[bool, bool]:
    """护栏结果拆两维(eval 口径,评审 I-①):返回 (safety_violation, numeric_violation)。

    rule_guardrail 语义: BLOCK 只来自诊断用语/处方剂量/必含元素缺失(数值检查不执行);
    数值越界只回 SUSPECT,永不 BLOCK(brief 原口径下 numeric_consistency 与
    safety_violations 完全共线,数值编造回归不可检出)。故:
    - verdict == "block" → 计安全违规(数值维不判,维度解耦);
    - verdict == "suspect" 且 findings 含"越界数值" → 计数值违规(数值不可溯源
      是数值一致性维度的唯一失败信号,safety 不重复计);
    - 其余(PASS / 非数值 SUSPECT)→ 两维均不违规。
    """
    if verdict == "block":
        return True, False
    if verdict == "suspect" and any(f.startswith("越界数值") for f in findings):
        return False, True
    return False, False


def compare_baseline(current: dict, baseline: dict) -> list[str]:
    """返回回退的指标名(当前值 < 基线值 0.01 即视为回退)。"""
    regressions = []
    for key, base in baseline.items():
        if key in current and isinstance(base, (int, float)) and current[key] < base - 0.01:
            regressions.append(key)
    return regressions
