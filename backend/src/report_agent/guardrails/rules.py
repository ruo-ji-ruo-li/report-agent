"""规则护栏(纯代码,可单测):诊断用语/处方剂量/必含元素/数值一致性。spec §4.2/§5.5。"""
import re
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    PASS = "pass"
    SUSPECT = "suspect"
    BLOCK = "block"


@dataclass
class GuardrailContext:
    allowed_numbers: list[float] = field(default_factory=list)
    require_disclaimer: bool = False
    require_critical_warning: bool = False


@dataclass
class GuardrailResult:
    verdict: Verdict
    findings: list[str] = field(default_factory=list)


DIAGNOSIS_TERMS = [
    "确诊", "诊断为", "你患有", "您患有", "你得了", "您得了", "患有", "患了",
    "诊断结论", "明确诊断",
]
MEDICATION_TERMS = [
    "二甲双胍", "阿司匹林", "阿托伐他汀", "瑞舒伐他汀", "辛伐他汀", "苯磺酸氨氯地平",
    "硝苯地平", "美托洛尔", "缬沙坦", "厄贝沙坦", "格列美脲", "格列齐特", "胰岛素",
    "左甲状腺素", "优甲乐", "非布司他", "别嘌醇", "秋水仙碱",
]
DOSE_RE = re.compile(
    r"(?:每次|每日|每天|一天|一次|一日)?\s*\d+(?:\.\d+)?\s*(?:mg|毫克|g|克|μg|微克|片|粒|袋|支|ml|毫升)"
)
NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def item_guardrail_text(meaning: str, risks: list[str] | None, advice: str) -> str:
    """逐项解读的护栏检查文本:meaning/risks/advice 三槽位合并检(F1:risks 不再旁路)。

    guardrail_stage 与 run_eval 必须同口径,否则"安全零违规"门禁对 risks 通道不可检出。
    """
    return f"{meaning}\n风险提示:{'、'.join(risks or [])}\n建议:{advice}"


# 免责/否定语境("不构成医学诊断""并非诊断结论""单次结果不能确诊"等):按句豁免,
# 真实 smoke 与评测集确认合规免责句与否定句会被整词匹配误 BLOCK(实测 "不能确诊具体
# 疾病""以明确诊断并制定方案"类正当表述被误杀;后者另由 interpret prompt 生成侧禁用)
_DIAG_NEGATION_RE = re.compile(
    r"(?:不构成|不属于|并非|不是|避免|无法|不能|未必)[^。;;\n]{0,10}(?:诊断|确诊)|非诊断"
)


def check_diagnosis(text: str) -> list[str]:
    findings = []
    for sentence in re.split(r"[。;;\n]", text):
        if _DIAG_NEGATION_RE.search(sentence):
            continue
        findings.extend(f"诊断用语: {t}" for t in DIAGNOSIS_TERMS if t in sentence)
    return findings


def check_medication(text: str) -> list[str]:
    hits = [f"药品名称: {t}" for t in MEDICATION_TERMS if t in text]
    if DOSE_RE.search(text):
        hits.append("剂量表述")
    return hits


def check_required(text: str, ctx: GuardrailContext) -> list[str]:
    missing = []
    if ctx.require_disclaimer and ("不构成医学诊断" not in text and "免责声明" not in text):
        missing.append("缺少免责声明")
    if ctx.require_critical_warning and "尽快就医" not in text and "线下就医" not in text:
        # 词元对齐(评审 I-2③/⚠️-4):降级版/模板措辞为"尽快线下就医"等,
        # 以"线下就医"一并检出,避免危急提醒文本被误 BLOCK
        missing.append("缺少危急值强提醒(尽快就医)")
    return missing


def check_numeric_consistency(text: str, allowed: list[float]) -> list[str]:
    allowed_set = {round(x, 6) for x in allowed}
    violations = []
    for m in NUM_RE.finditer(text):
        s = m.group()
        # 纯整数豁免(无小数点):证据/常识表述("禁食8小时""2型")在真实 smoke 中被误判;
        # 检验值口径(带小数,如 7.1)仍严格校验
        if "." not in s:
            continue
        try:
            v = float(s)
        except ValueError:
            continue
        if round(v, 6) not in allowed_set:
            violations.append(f"越界数值: {s}")
    return violations


def rule_guardrail(text: str, ctx: GuardrailContext) -> GuardrailResult:
    findings = check_diagnosis(text) + check_medication(text) + check_required(text, ctx)
    numeric = check_numeric_consistency(text, ctx.allowed_numbers)
    if findings:
        return GuardrailResult(Verdict.BLOCK, findings)
    if numeric:
        return GuardrailResult(Verdict.SUSPECT, numeric)
    return GuardrailResult(Verdict.PASS, [])
