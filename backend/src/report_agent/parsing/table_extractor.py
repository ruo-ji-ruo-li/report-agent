"""Paddle markdown → 表格块提取与纯规则解析(spec §5-§7)。

纯函数模块:输入合并后的 markdown 字符串,输出 (items, failed_htmls)。
无外部依赖调用,便于单测。
"""
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from report_agent.parsing.schemas import RawReportItem

PAGE_MARK_RE = re.compile(r"<!--\s*page\s+(\d+)\s*-->")

# 列角色关键字(spec §6.1)
HEADER_KEYWORDS: dict[str, set[str]] = {
    "name": {"检查项目", "项目", "检验项目", "项目名称"},
    "code": {"缩写", "代码"},
    "value": {"测量结果", "结果", "检查所见", "所见", "描述", "测定值"},
    "unit": {"单位"},
    "abnormal_flag": {"异常描述", "提示", "标志", "异常"},
    "ref_range": {"参考区间", "参考范围", "正常范围", "参考值"},
}

# 跳行关键字(spec §6.3)
META_ROW_KEYWORDS = {"姓名", "性别", "年龄", "编号", "送检"}
SUMMARY_ROW_KEYWORDS = {"小结", "备注", "说明"}

_NUM_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
_CODE_RE = re.compile(r"^[A-Z][A-Za-z0-9%\-_]{0,15}$")  # 缩写形态:大写开头(spec §7)
_FLAG_SET = {"↑", "↓", "H", "L", "高", "低"}
_RANGE_RE = re.compile(r"^[^A-Za-z一-鿿]*\d+(?:\.\d+)?[^A-Za-z一-鿿]*\d+(?:\.\d+)?[^A-Za-z一-鿿]*$")
_SINGLE_BOUND_RE = re.compile(r"^[<>≤≥]\s*\d+(?:\.\d+)?$")
_UNIT_RE = re.compile(r"[A-Za-z%×^/]")

# 列角色 → 允许的单元格模式(spec §7,首行 body 为探针)
_ROLE_PATTERNS: dict[str, set[str]] = {
    "name": {"TEXT"},
    "code": {"CODE"},
    "value": {"NUMERIC", "TEXT"},
    "abnormal_flag": {"FLAG"},
    "ref_range": {"RANGE", "TEXT"},
    "unit": {"UNIT", "TEXT"},
}


@dataclass
class TableBlock:
    page_no: int
    header: list[str] | None   # 表头单元格(已清洗);无表头 → None
    rows: list[list[str]]      # body 行(已清洗,不含 colspan 汇总行)
    raw_html: str              # 原始 HTML(供 unparsed_tables 落库)


def clean_cell(text: str) -> str:
    """去空白、剥 LaTeX($/花括号)、\\times → ×(spec §5.2)。"""
    s = re.sub(r"\s+", "", text)
    s = s.replace("\\times", "×").replace("$", "").replace("{", "").replace("}", "")
    return s.strip()


def merge_page_markdown(pages: list[str]) -> str:
    """逐页 markdown 拼接为单文档,页间插页标记(spec §5.1)。"""
    return "\n\n".join(f"<!-- page {i} -->\n{page}" for i, page in enumerate(pages, start=1))


def _identify_roles(cells: list[str]) -> dict[str, int] | None:
    """表头行 → 列角色映射 {role: col_idx};非表头行返回 None(spec §6.1)。"""
    roles: dict[str, int] = {}
    for idx, cell in enumerate(cells):
        for role, keywords in HEADER_KEYWORDS.items():
            if cell in keywords:
                roles[role] = idx
                break
    if "name" not in roles or len(roles) < 2:
        return None
    return roles


def table_type(roles: dict[str, int]) -> str:
    """A 数值表 / B 缩写表 / C 所见表(spec §6.2,code 优先判定)。"""
    if "code" in roles:
        return "B"
    if "ref_range" in roles or "abnormal_flag" in roles:
        return "A"
    return "C"


def _cell_pattern(text: str) -> str:
    """列模式细类(spec §7):CODE / NUMERIC / RANGE / UNIT / FLAG / TEXT。"""
    if not text:
        return "FLAG"
    if _CODE_RE.match(text):
        return "CODE"
    if _NUM_RE.match(text):
        return "NUMERIC"
    if text in _FLAG_SET:
        return "FLAG"
    if _RANGE_RE.match(text) or _SINGLE_BOUND_RE.match(text):
        return "RANGE"
    if _UNIT_RE.search(text) and not re.search(r"[一-鿿]{2,}", text):
        return "UNIT"
    return "TEXT"


def _rows_from_html_table(table) -> tuple[list[list[str]], str]:
    """BS table 元素 → 行列表(colspan 汇总行剔除)+ 原始 HTML 字符串。"""
    raw_html = str(table)
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if any(t.get("colspan") for t in tds):  # 汇总行,spec §6.3
            continue
        cells = [clean_cell(td.get_text()) for td in tds]
        if cells and any(cells):
            rows.append(cells)
    return rows, raw_html


def _rows_from_pipe_table(lines: list[str]) -> tuple[list[list[str]], str]:
    """markdown 管道表格兜底(spec §5.2)。"""
    rows = []
    for line in lines:
        if re.match(r"^\s*\|?[\s:|-]+\|?\s*$", line):  # |---|---| 分隔行
            continue
        cells = [clean_cell(c) for c in line.strip().strip("|").split("|")]
        if cells and any(cells):
            rows.append(cells)
    raw_html = "<table>" + "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows
    ) + "</table>"
    return rows, raw_html


def _to_block(page_no: int, rows: list[list[str]], raw_html: str) -> TableBlock:
    """首行命中表头关键字 → 表头;否则视为无表头截断表(spec §7)。"""
    header = rows[0] if _identify_roles(rows[0]) is not None else None
    body = rows[1:] if header is not None else rows
    return TableBlock(page_no=page_no, header=header, rows=body, raw_html=raw_html)


def extract_tables(merged: str) -> list[TableBlock]:
    """合并文档 → 表格块列表(文档顺序 + 页码归属)(spec §5)。"""
    blocks: list[TableBlock] = []
    page_no = 1
    for seg in PAGE_MARK_RE.split(merged):
        if not seg.strip():
            continue
        # PAGE_MARK_RE 含捕获组 → re.split 把页码数字作为独立段返回(如 "1"/"2"),
        # 此处直接识别纯数字段为页标记;整段页标记不会出现在 split 结果中。
        m = re.fullmatch(r"\d+", seg.strip())
        if m:
            page_no = int(m.group())
            continue
        soup = BeautifulSoup(seg, "html.parser")
        tables = soup.find_all("table")
        if tables:
            for t in tables:
                rows, raw = _rows_from_html_table(t)
                if rows:
                    blocks.append(_to_block(page_no, rows, raw))
            continue
        # 管道表格兜底:连续 | 行构成一个表格块
        pipe_lines: list[str] = []
        for line in seg.splitlines():
            if line.strip().startswith("|"):
                pipe_lines.append(line)
            elif pipe_lines:
                rows, raw = _rows_from_pipe_table(pipe_lines)
                if rows:
                    blocks.append(_to_block(page_no, rows, raw))
                pipe_lines = []
        if pipe_lines:
            rows, raw = _rows_from_pipe_table(pipe_lines)
            if rows:
                blocks.append(_to_block(page_no, rows, raw))
    return blocks


def _detect_code_column(rows: list[list[str]]) -> int | None:
    """无表头表中的代码列:非空单元格 ≥60% 为缩写形态且至少 2 个不同值(spec §7)。"""
    n_cols = len(rows[0])
    for idx in range(n_cols):
        cells = [row[idx] for row in rows if idx < len(row) and row[idx]]
        if len(cells) >= 2 and sum(_CODE_RE.match(c) is not None for c in cells) / len(cells) >= 0.6 \
                and len({c for c in cells}) >= 2:
            return idx
    return None


def try_align_with_prev(block: TableBlock, prev_roles: dict[str, int], prev_cols: int,
                        prev_first_row: list[str] | None) -> dict[str, int] | None:
    """无表头表对齐上一页表头(spec §7 2a/2b)。返回列角色映射或 None。"""
    rows = block.rows
    if not rows:
        return None
    n_cols = len(rows[0])
    # 2a: 代码列锚点对齐(截断表列数可能与原表不一致,空列被裁)
    code_col = _detect_code_column(rows)
    if code_col is not None and "code" in prev_roles:
        prev_code_idx = prev_roles["code"]
        if code_col > prev_code_idx or (n_cols - 1 - code_col) > (prev_cols - 1 - prev_code_idx):
            return None  # 锚点两侧列数超出表头 → 无法判断
        ordered = sorted(prev_roles.items(), key=lambda kv: kv[1])
        left_roles = [r for r, _ in ordered if prev_roles[r] < prev_code_idx]
        right_roles = [r for r, _ in ordered if prev_roles[r] > prev_code_idx]
        if code_col != len(left_roles):
            return None
        roles: dict[str, int] = {}
        for col, role in enumerate(left_roles):
            if _cell_pattern(rows[0][col]) not in _ROLE_PATTERNS[role]:
                return None
            roles[role] = col
        roles["code"] = code_col
        r_idx = 0
        for col in range(code_col + 1, n_cols):
            pat = _cell_pattern(rows[0][col])
            while r_idx < len(right_roles) and pat not in _ROLE_PATTERNS[right_roles[r_idx]]:
                r_idx += 1  # 缺失列允许跳过
            if r_idx >= len(right_roles):
                return None
            roles[right_roles[r_idx]] = col
            r_idx += 1
        return roles
    # 2b: 无代码列 → 列数一致 + 首行模式一致
    if (prev_first_row is not None and n_cols == len(prev_first_row)
            and all(_cell_pattern(rows[0][i]) == _cell_pattern(prev_first_row[i])
                    for i in range(n_cols))):
        return dict(prev_roles)
    return None


def parse_rows(rows: list[list[str]], roles: dict[str, int], ttype: str) -> list[RawReportItem]:
    """body 行 → 检验项(spec §6.3)。"""
    items: list[RawReportItem] = []

    def cell(row: list[str], role: str) -> str | None:
        if role not in roles or roles[role] >= len(row):
            return None
        return row[roles[role]] or None

    for row in rows:
        name = cell(row, "name")
        if not name or name in META_ROW_KEYWORDS or name in SUMMARY_ROW_KEYWORDS:
            continue
        value_text = cell(row, "value")
        if not any(cell(row, r) for r in ("value", "unit", "abnormal_flag", "ref_range", "code")):
            continue  # 除 name 外全空 → 跳过(spec §6.3)
        value_num = None
        if ttype != "C" and value_text and _NUM_RE.match(value_text):
            value_num = float(value_text)  # 纯数值:与 value_text 同值(spec §6.3)
        items.append(RawReportItem(
            name=name, value_text=value_text, value_num=value_num,
            unit=cell(row, "unit"), ref_range_text=cell(row, "ref_range"),
            abnormal_flag=cell(row, "abnormal_flag"), code=cell(row, "code"),
        ))
    return items


def parse_merged_tables(merged: str) -> tuple[list[RawReportItem], list[str]]:
    """文档顺序逐表解析(含跨页截断,spec §7)。返回 (items, failed_htmls)。"""
    items: list[RawReportItem] = []
    failed: list[str] = []
    prev_roles: dict[str, int] | None = None
    prev_cols = 0
    prev_first_row: list[str] | None = None
    prev_page = 0
    for block in extract_tables(merged):
        roles = _identify_roles(block.header) if block.header is not None else None
        if roles is None:
            # 无表头 → 截断候选(spec §7):取上一页最后一个表格的表头对齐
            aligned = None
            if prev_roles is not None and prev_page < block.page_no:
                aligned = try_align_with_prev(block, prev_roles, prev_cols, prev_first_row)
            if aligned is None:
                failed.append(block.raw_html)  # 无法判断 → 不做处理,落库
                continue
            roles = aligned
        items.extend(parse_rows(block.rows, roles, table_type(roles)))
        # 只有解析成功的表才能作为后续截断表的表头来源
        prev_roles = roles
        prev_cols = max(roles.values()) + 1
        prev_first_row = block.rows[0] if block.rows else None
        prev_page = block.page_no
    return items, failed
