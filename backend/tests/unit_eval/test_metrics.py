"""eval.metrics 纯函数单测(无 infra;分 key 容差为评测升级 spec §8)。"""
from report_agent.eval.metrics import compare_baseline


def test_compare_baseline_default_tolerance_005():
    """缺省容差 0.05(旧口径不变):0.9667 对 1.0 不算回退。"""
    assert compare_baseline({"refusal_correct": 0.9667}, {"refusal_correct": 1.0}) == []


def test_compare_baseline_per_key_tolerance():
    """LLM 评分维度容差 0.15(评测升级 spec §8):0.7 对 0.8 不算回退。"""
    tolerances = {"llm_interpretation_score": 0.15}
    assert compare_baseline({"llm_interpretation_score": 0.7},
                            {"llm_interpretation_score": 0.8}, tolerances) == []
    # 0.6 对 0.8 仍算回退;未列 key 沿用 0.05
    assert compare_baseline({"llm_interpretation_score": 0.6},
                            {"llm_interpretation_score": 0.8}, tolerances) == \
        ["llm_interpretation_score"]
    assert compare_baseline({"f1": 0.9}, {"f1": 0.95}, tolerances) == []


def test_compare_baseline_skips_none_current_value():
    """LLM 评分全量降级时 current 侧为 None(JSON null):必须跳过比较而非抛 TypeError。
    评测升级 spec §8:LLM 评分全量降级时 new key 为 None,基线比较必须跳过。
    """
    assert compare_baseline({"llm_interpretation_score": None},
                            {"llm_interpretation_score": 0.8}) == []
