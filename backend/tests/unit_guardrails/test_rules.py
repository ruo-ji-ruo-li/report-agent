"""规则护栏单测(纯代码 4 项检查,零 DB/零网络):诊断用语/处方剂量/必含元素/数值一致性。spec §4.2/§5.5。"""
from report_agent.guardrails.rules import GuardrailContext, Verdict, rule_guardrail


def _ctx(**kw) -> GuardrailContext:
    base = {"allowed_numbers": [6.2, 3.9, 6.1], "require_disclaimer": True,
            "require_critical_warning": False}
    base.update(kw)
    return GuardrailContext(**base)


def test_diagnosis_term_blocks():
    r = rule_guardrail("您可能被确诊为糖尿病,需要治疗。", _ctx())
    assert r.verdict == Verdict.BLOCK


def test_medication_and_dose_blocks():
    r = rule_guardrail("建议每日服用二甲双胍 500mg。", _ctx())
    assert r.verdict == Verdict.BLOCK
    r2 = rule_guardrail("每次2片,每天3次。", _ctx())
    assert r2.verdict == Verdict.BLOCK


def test_missing_disclaimer_blocks():
    r = rule_guardrail("您的血糖正常。", _ctx())
    assert r.verdict == Verdict.BLOCK
    assert any("免责" in f for f in r.findings)


def test_numeric_inconsistency_suspects():
    r = rule_guardrail("您的血糖 9.9 mmol/L,超出参考上限。\n本内容不构成医学诊断。", _ctx())
    assert r.verdict == Verdict.SUSPECT  # 9.9 不在白名单 → 触发审核
    r2 = rule_guardrail("您的血糖 6.2 mmol/L,略高于上限 6.1。\n本内容不构成医学诊断。", _ctx())
    assert r2.verdict == Verdict.PASS  # 数值都在白名单


def test_critical_warning_required():
    r = rule_guardrail("血糖危急,请关注。\n本内容不构成医学诊断。",
                       _ctx(require_critical_warning=True))
    assert r.verdict == Verdict.BLOCK  # 缺"尽快就医"强提醒


def test_diagnosis_negation_context_exempt():
    # 真实 smoke:合规免责句("不构成医学诊断""并非诊断结论")被整词匹配误 BLOCK
    ctx = GuardrailContext(require_disclaimer=False)
    assert rule_guardrail("这不是诊断结论,仅为风险提示。", ctx).verdict == Verdict.PASS
    assert rule_guardrail("本内容不构成医学诊断。", ctx).verdict == Verdict.PASS
    # 肯定语境仍 BLOCK
    assert rule_guardrail("您可能被确诊为糖尿病。", ctx).verdict == Verdict.BLOCK


def test_numeric_consistency_exempts_plain_integers():
    # 真实 smoke:证据常识数字("禁食8小时""2型")被误判越界;带小数检验值仍严格
    ctx = GuardrailContext(allowed_numbers=[7.1])
    assert rule_guardrail("请禁食8小时后再查,可能与2型糖尿病相关。\n本内容不构成医学诊断。",
                          ctx).verdict == Verdict.PASS
    assert rule_guardrail("您的血糖 9.9 mmol/L 偏高。\n本内容不构成医学诊断。",
                          ctx).verdict == Verdict.SUSPECT
