"""向后兼容 shim(评测升级 spec §3):纯函数已迁入 report_agent.eval.metrics。
旧引用(scripts / 测试)继续可用;新代码请直接 import report_agent.eval.metrics。"""
from report_agent.eval.metrics import (
    compare_baseline,
    compute_code_f1,
    guardrail_violations,
    status_accuracy,
)

__all__ = ["compare_baseline", "compute_code_f1", "guardrail_violations", "status_accuracy"]
