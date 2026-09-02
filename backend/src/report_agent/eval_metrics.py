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


def compare_baseline(current: dict, baseline: dict) -> list[str]:
    """返回回退的指标名(当前值 < 基线值 0.01 即视为回退)。"""
    regressions = []
    for key, base in baseline.items():
        if key in current and isinstance(base, (int, float)) and current[key] < base - 0.01:
            regressions.append(key)
    return regressions
