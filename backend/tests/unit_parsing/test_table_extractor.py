"""纯规则表格提取与解析单测(spec §5-§7)。"""
from report_agent.parsing.table_extractor import (
    clean_cell,
    extract_tables,
    merge_page_markdown,
    parse_merged_tables,
)

HEADER_TABLE = """<table border=1><tr><td>检查项目</td><td>缩写</td><td>测量结果</td><td>提示</td><td>参考区间</td><td>单位</td></tr><tr><td>白细胞计数</td><td>WBC</td><td>8.0</td><td></td><td>3.5—9.5</td><td>$ 10^{{9}}/L $</td></tr><tr><td>红细胞计数</td><td>RBC</td><td>5.60</td><td></td><td>4.30—5.80</td><td>$ 10^{{12}}/L $</td></tr></table>"""

TRUNCATED_TABLE = """<table border=1><tr><td>平均红细胞体积</td><td>MCV</td><td>85.4</td><td>82.0-100.0</td><td>fL</td></tr><tr><td>血小板计数</td><td>PLT</td><td>253</td><td>125-350</td><td>$ 10^{{9}}/L $</td></tr><tr><td>小结</td><td colspan="4">未见明显异常</td></tr></table>"""

SEEN_TABLE = """<table border=1><tr><td>检查项目</td><td>检查所见</td><td>单位</td></tr><tr><td>肝脏彩超</td><td>肝实质回声均匀,未见占位</td><td></td></tr></table>"""


def test_clean_cell_latex_variants():
    assert clean_cell("$ 10^{{9}}/L $") == "10^9/L"
    assert clean_cell(r"$ \times 10^{{9}}/L $") == "×10^9/L"
    assert clean_cell(" 3.5—9.5 ") == "3.5—9.5"


def test_merge_page_markdown():
    merged = merge_page_markdown(["页一", "页二"])
    assert "<!-- page 1 -->" in merged and "<!-- page 2 -->" in merged
    assert merged.index("页一") < merged.index("页二")


def test_extract_tables_page_attribution():
    merged = merge_page_markdown(["", HEADER_TABLE + TRUNCATED_TABLE])
    tables = extract_tables(merged)
    assert [t.page_no for t in tables] == [2, 2]  # 两表都在第 2 页(第 1 页为空)
    assert tables[0].header == ["检查项目", "缩写", "测量结果", "提示", "参考区间", "单位"]
    assert tables[1].header is None  # 截断表无表头
    assert len(tables[1].rows) == 2  # 小结 colspan 行被剔除


def test_parse_header_table_with_code():
    items, failed = parse_merged_tables(HEADER_TABLE)
    assert failed == []
    assert [i.name for i in items] == ["白细胞计数", "红细胞计数"]
    assert items[0].code == "WBC"
    assert items[0].value_num == 8.0 and items[0].unit == "10^9/L"
    assert items[0].ref_range_text == "3.5—9.5"
    assert items[0].abnormal_flag is None  # 空提示列


def test_parse_truncated_table_aligns_by_code_column():
    # 第 1 页表头 6 列,第 2 页截断表 5 列(Paddle 裁掉空提示列)
    # → 代码列锚点对齐(spec §7 2a,上一页最后一个表格的表头)
    merged = merge_page_markdown([HEADER_TABLE, TRUNCATED_TABLE])
    items, failed = parse_merged_tables(merged)
    assert failed == []
    names = [i.name for i in items]
    assert "平均红细胞体积" in names and "血小板计数" in names
    mcv = next(i for i in items if i.name == "平均红细胞体积")
    assert mcv.code == "MCV" and mcv.value_num == 85.4
    assert mcv.ref_range_text == "82.0-100.0" and mcv.unit == "fL"
    assert "小结" not in names


def test_parse_seen_table_no_value_num():
    items, _ = parse_merged_tables(SEEN_TABLE)
    assert len(items) == 1
    assert items[0].name == "肝脏彩超"
    assert items[0].value_text == "肝实质回声均匀,未见占位"
    assert items[0].value_num is None and items[0].code is None


def test_unparseable_table_collected_as_failed_html():
    bad = """<table><tr><td>甲</td><td>乙</td></tr><tr><td>x</td><td>1</td></tr></table>"""
    items, failed = parse_merged_tables(bad)
    assert items == [] and len(failed) == 1
    assert "<table>" in failed[0]
