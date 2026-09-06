from dataclasses import dataclass


@dataclass
class ReportMeta:
    institution: str | None = None
    report_date: str | None = None
    sex: str | None = None  # male / female
    age: float | None = None
    source: str = "pdf"  # pdf / photo / manual


@dataclass
class RawReportItem:
    section: str | None = None
    name: str = ""
    value_text: str | None = None
    value_num: float | None = None
    unit: str | None = None
    ref_range_text: str | None = None
    abnormal_flag: str | None = None
    code: str | None = None  # 缩写列(表头 2.3),归一化优先使用(spec §10)


@dataclass
class NormalizedItem:
    raw_index: int
    section: str | None
    name: str
    indicator_code: str | None  # None = unmapped
    value_text: str | None
    value_num: float | None     # 标准单位数值(未换算则为原值)
    unit: str | None            # 标准单位(未换算则保留原单位)
    raw_value_num: float | None  # 换算前原值(与报告区间同单位,判定用)
    raw_unit: str | None
    ref_range_text: str | None
    range_from: str | None  # "report" 或 None(compare 阶段补 "kg")
