"""归一化:词典匹配(规则) → 单位换算(按指标)。未命中保持 unmapped ——
LLM alias_map 兜底已删除(KG 点查设计 §6.2),每次解析不再向 LLM 发送全量目录。"""
import re

from report_agent.knowledge.kg_client import IndicatorEntry, _norm_text
from report_agent.parsing.schemas import NormalizedItem, RawReportItem


def _match_keys(entry: IndicatorEntry) -> list[str]:
    """别名产生两个匹配键:原样归一化 + 去掉括号内容的归一化。"""
    keys = []
    for alias in [entry.name, *entry.aliases]:
        k = _norm_text(alias)
        keys.append(k)
        no_paren = re.sub(r"[\(（][^)）]*[\)）]", "", alias)
        if no_paren != alias:
            keys.append(_norm_text(no_paren))
    return keys


def match_indicator(name: str, entries: list[IndicatorEntry]) -> str | None:
    key = _norm_text(name)
    for e in entries:
        if key in _match_keys(e):
            return e.code
    no_paren = re.sub(r"[\(（][^)）]*[\)）]", "", name)
    if no_paren != name:
        # 括号变体候选:先试去掉括号后的外层名,再试括号内内容。
        # 例:报告写 "空腹葡萄糖(空腹血糖)",外层不在目录,但括号内 "空腹血糖" 是 GLU 名称。
        for cand in [no_paren, *re.findall(r"[\(（]([^)）]*)[\)）]", name)]:
            key2 = _norm_text(cand)
            if not key2:
                continue
            for e in entries:
                if key2 in _match_keys(e):
                    return e.code
    return None


def convert_value(value: float, from_unit: str | None, entry: IndicatorEntry) -> tuple[float, str | None]:
    """value_standard = value_from × factor(seed YAML 的 unit_conversions 语义)。"""
    if from_unit is None or entry.unit is None or from_unit == entry.unit:
        return value, from_unit
    if from_unit in entry.unit_conversions:
        return value * entry.unit_conversions[from_unit], entry.unit
    return value, from_unit  # 无换算表 → 原值原单位,判定用报告区间


class Normalizer:
    def __init__(self, entries: list[IndicatorEntry]):
        self._entries = entries

    async def normalize(
        self, raw_items: list[RawReportItem]
    ) -> list[NormalizedItem]:
        out: list[NormalizedItem] = []
        codes = {e.code for e in self._entries}
        for idx, raw in enumerate(raw_items):
            # spec §10: raw.code 优先 —— 缩写列直接作为 indicator_code;
            # 不在 KG 目录中 → 回退 name 匹配;不中则 unmapped(KG 点查设计 §6.2)
            code = raw.code if raw.code in codes else None
            if code is None:
                code = match_indicator(raw.name, self._entries)
            item = NormalizedItem(
                raw_index=idx, name=raw.name, indicator_code=code,
                value_text=raw.value_text, value_num=raw.value_num, unit=raw.unit,
                raw_value_num=raw.value_num, raw_unit=raw.unit,
                ref_range_text=raw.ref_range_text,
                range_from="report" if raw.ref_range_text else None,
            )
            if code is not None:
                entry = next(e for e in self._entries if e.code == code)
                if raw.value_num is not None:
                    item.value_num, item.unit = convert_value(raw.value_num, raw.unit, entry)
            out.append(item)
        return out
