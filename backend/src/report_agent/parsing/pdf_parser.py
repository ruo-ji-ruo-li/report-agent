"""电子 PDF(有文字层)解析:Unstructured hi_res + 表格结构推断(spec §5.1)。

有效项 < parse_min_items 时返回 None,由 parse_report 降级视觉路径。
"""
import re

from bs4 import BeautifulSoup

from report_agent.config import Settings
from report_agent.observability import get_logger
from report_agent.parsing.schemas import RawReportItem, ReportMeta

# 模块级绑定而非函数内导入:测试 monkeypatch 的目标必须是模块属性。
# unstructured.partition.pdf 在部分环境(如 win32 + py3.13,lockfile marker 排除
# unstructured-inference)导入即失败,兜住 ImportError 保证模块可导入、真实调用时给出明确报错。
try:
    from unstructured.partition.pdf import partition_pdf
except ImportError:  # pragma: no cover —— 缺 [local-inference] 依赖的环境(本机即属此类)
    partition_pdf = None  # type: ignore[assignment]

log = get_logger(__name__)

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_HEADER_CELLS = {"项目", "检验项目", "指标", "项目名称", "name", "item"}


def _cell_texts(html: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if cells:
            rows.append(cells)
    return rows


def _row_to_item(cells: list[str]) -> RawReportItem | None:
    """表行 → 检验项。第 1 列项目名,后续列中找数值/单位/区间/标记。"""
    if not cells or not cells[0]:
        return None
    name = cells[0]
    if name in _HEADER_CELLS:
        return None
    rest = cells[1:]
    value_text = next((c for c in rest if c and _NUM_RE.match(c)), None)
    if value_text is None and len(rest) == 1:
        value_text = rest[0]  # 定性结果列(阴性/阳性)
    value_num = float(value_text) if value_text and _NUM_RE.match(value_text) else None
    unit = next((c for c in rest if c and re.search(r"[a-zA-Z/%×^]", c) and c != value_text), None)
    ref = next((c for c in rest if c and re.search(r"[\d<≤>~]", c) and "-" in c), None) or \
        next((c for c in rest if c and re.search(r"^[<≤>~]", c)), None)
    flag = next((c for c in rest if c and c in {"↑", "↓", "H", "L", "高", "低"}), None)
    return RawReportItem(
        name=name, value_text=value_text, value_num=value_num, unit=unit,
        ref_range_text=ref, abnormal_flag=flag,
    )


def _extract_meta(text: str, meta: ReportMeta) -> None:
    if not meta.institution:
        m = re.search(r"^(.{2,20}?(?:医院|中心|门诊部|诊所|检验所))", text)
        if m:
            meta.institution = m.group(1)
    if not meta.sex:
        m = re.search(r"性别[:：]?\s*(男|女)", text)
        if m:
            meta.sex = "male" if m.group(1) == "男" else "female"
    if meta.age is None:
        m = re.search(r"年龄[:：]?\s*(\d+(?:\.\d+)?)\s*岁", text)
        if m:
            meta.age = float(m.group(1))
    if not meta.report_date:
        m = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", text)
        if m:
            meta.report_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"


def parse_pdf_text(path: str, settings: Settings) -> tuple[list[RawReportItem], ReportMeta] | None:
    if partition_pdf is None:
        raise RuntimeError("PDF 文字层解析依赖缺失:unstructured_inference 未安装,无法真跑(测试外)")

    elements = partition_pdf(
        filename=path,
        strategy=settings.unstructured_strategy,
        infer_table_structure=settings.unstructured_infer_table,
        languages=settings.unstructured_ocr_languages,
    )
    items, meta = [], ReportMeta(source="pdf")
    for el in elements:
        if el.category == "Table":
            table_html = el.metadata.text_as_html or ""
            for cells in _cell_texts(table_html):
                item = _row_to_item(cells)
                if item:
                    items.append(item)
                else:
                    # 体检报告的元信息行(姓名/性别/年龄)常与检验表合并为同一 Table
                    # 区域(hi_res 布局检测实测如此):行不是数据项时,行文本仍可能
                    # 携带 meta,逐行尝试提取(_extract_meta 对已填字段幂等)
                    _extract_meta(" ".join(cells), meta)
            if meta.sex is None or meta.age is None:
                # 表格重建的 OCR cell 切分碎(实测"性别"可被误识为"PER:"),meta 兜底
                # 优先用表格文字层文本(电子 PDF 有完整文字层);无文字层时退回去标签
                # 的表格 HTML
                _extract_meta(el.text or "", meta)
                _extract_meta(re.sub(r"<[^>]+>", " ", table_html), meta)
        else:
            _extract_meta(el.text or "", meta)
    if len(items) < settings.parse_min_items:
        log.warning("pdf_text_parse_insufficient", count=len(items), threshold=settings.parse_min_items)
        return None
    log.info("pdf_text_parsed", count=len(items), method=settings.unstructured_strategy)
    return items, meta
