"""规则判定层:区间解析/区间选择/数值比对/危急值/组合模式。全部纯代码,禁止 LLM。

单位语义(spec §5.2 与 Task 6 约定):
- 报告区间与 raw_value_num/raw_unit 同单位 → 报告区间对原始值比
- KG 区间与 value_num/unit(标准单位)同单位 → KG 区间对标准值比
- 危急值永远来自 KG(spec §2 决策表)
"""
import re
from dataclasses import dataclass
from enum import Enum

from report_agent.knowledge.kg_client import PatternSpec, RangeSpec
from report_agent.parsing.schemas import NormalizedItem, ReportMeta


class ItemStatus(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL_HIGH = "critical_high"
    CRITICAL_LOW = "critical_low"
    UNKNOWN = "unknown"
    UNMAPPED = "unmapped"


@dataclass
class ItemJudgment:
    indicator_code: str | None
    name: str
    value_num: float | None
    value_text: str | None
    unit: str | None
    status: ItemStatus
    ref_low: float | None
    ref_high: float | None
    critical: bool
    range_source: str | None  # report / kg


_QUAL_NEG = {"阴性", "negative", "neg", "(-)", "-", "未见异常"}
_QUAL_POS = {"阳性", "positive", "pos", "(+)", "+"}


def parse_range_text(text: str | None) -> tuple[float | None, float | None]:
    """'3.5-9.5' / '3.5~9.5' / '<5.0' / '>1.0' / '≤2.0',带单位也可;无法解析返回 (None, None)。"""
    if not text or not text.strip():
        return None, None
    t = text.strip()
    if any(m in t for m in "<≤>"):  # 方向式
        m = re.search(r"\d+(?:\.\d+)?", t)
        if not m:
            return None, None
        n = float(m.group())
        return (n, None) if ">" in t else (None, n)
    parts = re.split(r"[-~—～]", t)
    if len(parts) >= 2:
        m1, m2 = re.search(r"\d+(?:\.\d+)?", parts[0]), re.search(r"\d+(?:\.\d+)?", parts[1])
        if m1 and m2:
            a, b = float(m1.group()), float(m2.group())
            return min(a, b), max(a, b)
    return None, None


def select_range(specs: list[RangeSpec], sex: str | None, age: float | None) -> RangeSpec | None:
    """sex 精确匹配优先于 any;age 落入 [age_min, age_max);多条命中取最窄年龄窗。"""
    def in_window(r: RangeSpec) -> bool:
        return age is None or (r.age_min <= age < r.age_max)

    cands = [r for r in specs if r.sex in (sex, "any") and in_window(r)]
    if not cands:
        return None
    if sex is not None:
        exact = [r for r in cands if r.sex == sex]
        if exact:
            cands = exact
    return min(cands, key=lambda r: r.age_max - r.age_min)


def judge_item(item: NormalizedItem, spec: RangeSpec | None) -> ItemJudgment:
    j = ItemJudgment(
        indicator_code=item.indicator_code, name=item.name, value_num=item.value_num,
        value_text=item.value_text, unit=item.unit, status=ItemStatus.UNKNOWN,
        ref_low=None, ref_high=None, critical=False, range_source=None,
    )
    if item.indicator_code is None:
        j.status = ItemStatus.UNMAPPED
        return j

    if item.value_num is None:  # 定性结果
        vt = (item.value_text or "").strip().lower().replace(" ", "")
        if vt in _QUAL_NEG:
            j.status = ItemStatus.NORMAL
        elif vt in _QUAL_POS:
            j.status = ItemStatus.HIGH  # 阳性记为异常方向,触发解读
        else:
            j.status = ItemStatus.UNKNOWN
        return j

    # 报告区间优先(对原始值比)
    report_low, report_high = parse_range_text(item.ref_range_text)
    bounds: tuple[float | None, float | None] | None = None
    if report_low is not None or report_high is not None:
        bounds = (report_low, report_high)
        j.range_source = "report"
    elif spec is not None and (spec.low is not None or spec.high is not None):
        bounds = (spec.low, spec.high)
        j.range_source = "kg"
    if bounds is None:
        return j

    low, high = bounds
    j.ref_low, j.ref_high = low, high
    v = item.raw_value_num if (j.range_source == "report" and item.raw_value_num is not None) \
        else item.value_num

    # 危急值永远来自 KG(对标准值比)
    if spec is not None and item.value_num is not None:
        if spec.critical_high is not None and item.value_num >= spec.critical_high:
            j.status, j.critical = ItemStatus.CRITICAL_HIGH, True
            return j
        if spec.critical_low is not None and item.value_num <= spec.critical_low:
            j.status, j.critical = ItemStatus.CRITICAL_LOW, True
            return j

    if high is not None and v > high:
        j.status = ItemStatus.HIGH
    elif low is not None and v < low:
        j.status = ItemStatus.LOW
    else:
        j.status = ItemStatus.NORMAL
    return j


def judge_all(
    items: list[NormalizedItem], specs_by_code: dict[str, list[RangeSpec]], meta: ReportMeta
) -> list[ItemJudgment]:
    return [
        judge_item(it, select_range(specs_by_code.get(it.indicator_code or "", []), meta.sex, meta.age))
        for it in items
    ]


def match_patterns(
    judgments: list[ItemJudgment], patterns: list[PatternSpec]
) -> list[tuple[PatternSpec, list[ItemJudgment]]]:
    """全部 criteria 满足才算命中;critical 计入方向(CRITICAL_HIGH→high)。"""
    status_high = {ItemStatus.HIGH, ItemStatus.CRITICAL_HIGH}
    status_low = {ItemStatus.LOW, ItemStatus.CRITICAL_LOW}
    matched = []
    for p in patterns:
        hits: list[ItemJudgment] = []
        ok = True
        for c in p.criteria:
            hit = next(
                (
                    j for j in judgments
                    if j.indicator_code == c.indicator_code
                    and (
                        (c.direction == "high" and j.status in status_high)
                        or (c.direction == "low" and j.status in status_low)
                    )
                ),
                None,
            )
            if hit is None:
                ok = False
                break
            hits.append(hit)
        if ok:
            matched.append((p, hits))
    return matched


def is_critical(j: ItemJudgment) -> bool:
    return j.critical
