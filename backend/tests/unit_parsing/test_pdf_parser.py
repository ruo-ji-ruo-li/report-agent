from report_agent.config import Settings
from report_agent.parsing.pdf_parser import parse_pdf_text


class FakeTableElement:
    category = "Table"

    def __init__(self, html):
        self.metadata = type("M", (), {"text_as_html": html})()


class FakeTextElement:
    category = "NarrativeText"

    def __init__(self, text):
        self.text = text


def _fake_partition(elements):
    return lambda **kw: elements


def test_parse_pdf_text_extracts_table_rows(monkeypatch):
    html = (
        "<table><tr><td>项目</td><td>结果</td><td>单位</td><td>参考区间</td><td>标记</td></tr>"
        "<tr><td>白细胞计数</td><td>6.2</td><td>10^9/L</td><td>3.5-9.5</td><td></td></tr>"
        "<tr><td>空腹血糖</td><td>7.1</td><td>mmol/L</td><td>3.9-6.1</td><td>↑</td></tr></table>"
    )
    monkeypatch.setattr(
        "report_agent.parsing.pdf_parser.partition_pdf", _fake_partition([FakeTableElement(html)])
    )
    s = Settings(parse_min_items=2)
    items, _ = parse_pdf_text("x.pdf", s)
    assert [i.name for i in items] == ["白细胞计数", "空腹血糖"]
    assert items[1].value_num == 7.1 and items[1].abnormal_flag == "↑"
    assert items[1].ref_range_text == "3.9-6.1"


def test_parse_pdf_text_insufficient_returns_none(monkeypatch):
    html = "<table><tr><td>项目</td><td>结果</td></tr><tr><td>某项</td><td>6.2</td></tr></table>"
    monkeypatch.setattr(
        "report_agent.parsing.pdf_parser.partition_pdf", _fake_partition([FakeTableElement(html)])
    )
    assert parse_pdf_text("x.pdf", Settings(parse_min_items=3)) is None  # 有效项不足


def test_parse_pdf_text_extracts_meta_from_narrative(monkeypatch):
    monkeypatch.setattr(
        "report_agent.parsing.pdf_parser.partition_pdf",
        _fake_partition([FakeTextElement("某某医院 体检中心  性别: 男 年龄: 45 岁")]),
    )
    _, meta = parse_pdf_text("x.pdf", Settings(parse_min_items=0))
    assert meta.sex == "male" and meta.age == 45.0
    assert meta.institution == "某某医院"
