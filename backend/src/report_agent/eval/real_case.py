"""真实评测 case 装载与解析对齐(评测升级 spec §7)。

真实 case = eval/real/r_real.md(脱敏 Paddle markdown,逐表带页标记)
         + eval/real/gt.json(全量逐项标注 + 溯源说明)。
评测时 md 走真实解析(parse_merged_tables),解析产物与 gt 按 name 对齐,
解析维度 = 项级 F1(spec §7.3);其余维度与合成 case 同口径合并计算(spec §4)。
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from report_agent.parsing.schemas import NormalizedItem, RawReportItem

REAL_MD = "r_real.md"
GT_FILE = "gt.json"
REAL_CASE_ID = "r_real"


@dataclass
class RealGTItem:
    name: str
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    ref_range_text: str | None = None
    code: str | None = None
    status: str = "unmapped"
    note: str = ""


@dataclass
class RealCaseGT:
    meta: dict
    items: list[RealGTItem]


@dataclass
class AlignResult:
    pairs: list[tuple[RawReportItem, RealGTItem]] = field(default_factory=list)
    unmatched_parsed: list[RawReportItem] = field(default_factory=list)
    unmatched_gt: list[RealGTItem] = field(default_factory=list)


def load_real_case(real_dir: Path) -> tuple[str, RealCaseGT] | None:
    """目录缺 r_real.md 或 gt.json → None(跳过真实 case 维度,spec §7.1)。"""
    md = real_dir / REAL_MD
    gt_file = real_dir / GT_FILE
    if not md.exists() or not gt_file.exists():
        return None
    data = json.loads(gt_file.read_text("utf-8"))
    return md.read_text("utf-8"), RealCaseGT(
        meta=data["meta"], items=[RealGTItem(**i) for i in data["items"]])


def align_parsed(parsed: list[RawReportItem], gt_items: list[RealGTItem]) -> AlignResult:
    """解析产物与 gt 按 name 精确对齐;重名解析项只对齐首个(spec §7.3)。"""
    gt_by_name = {i.name: i for i in gt_items}
    pairs: list[tuple[RawReportItem, RealGTItem]] = []
    unmatched_parsed: list[RawReportItem] = []
    seen: set[str] = set()
    for p in parsed:
        g = gt_by_name.get(p.name)
        if g is None or p.name in seen:
            unmatched_parsed.append(p)
        else:
            pairs.append((p, g))
            seen.add(p.name)
    unmatched_gt = [g for g in gt_items if g.name not in seen]
    return AlignResult(pairs=pairs, unmatched_parsed=unmatched_parsed, unmatched_gt=unmatched_gt)


def compute_parse_f1(align: AlignResult) -> float:
    """解析维度项级 F1:precision = 对齐数/解析产物数,recall = 对齐数/gt 项数(spec §7.3)。"""
    n_matched = len(align.pairs)
    n_parsed = n_matched + len(align.unmatched_parsed)
    n_gt = n_matched + len(align.unmatched_gt)
    if n_parsed == 0 or n_gt == 0:
        return 0.0
    p, r = n_matched / n_parsed, n_matched / n_gt
    return round(2 * p * r / (p + r), 4) if p + r else 0.0


def gt_to_normalized(gt: RealCaseGT) -> list[NormalizedItem]:
    """gt 项 → NormalizedItem(规则层判定直接喂 gt,绕过归一化波动,同合成 case 口径)。"""
    return [
        NormalizedItem(
            raw_index=i, name=item.name, indicator_code=item.code,
            value_text=item.value_text, value_num=item.value_num, unit=item.unit,
            raw_value_num=item.value_num, raw_unit=item.unit,
            ref_range_text=item.ref_range_text,
            # (brief 偏差,最小修正:brief 实现为 "report" if ref_range_text else None,
            #  与其自带单测(无 ref_range_text 项断言 range_from=="report")及 docstring
            #  「同合成 case 口径」自相矛盾;合成 case(runner.py)无条件 range_from="report",
            #  规则层不消费 range_from,纯溯源元数据 → 统一无条件 "report")
            range_from="report",
        )
        for i, item in enumerate(gt.items)
    ]


def gt_statuses(gt: RealCaseGT) -> dict[str, str]:
    return {item.code: item.status for item in gt.items if item.code}


def normalized_pair_codes(
    align: AlignResult, norm_by_name: dict[str, NormalizedItem]
) -> tuple[list[str | None], list[str | None]]:
    """真实 case 归一化 F1 口径(spec §4):按 name 对齐的解析产物 code vs gt code。"""
    pred: list[str | None] = []
    gt_codes: list[str | None] = []
    for parsed_item, gt_item in align.pairs:
        norm = norm_by_name.get(parsed_item.name)
        pred.append(norm.indicator_code if norm else None)
        gt_codes.append(gt_item.code)
    return pred, gt_codes
