"""parse_report 编排单测:Paddle 成功/不足降级/异常降级/photo(spec §3)。"""
import asyncio

from report_agent.config import Settings
from report_agent.parsing.paddle_client import PaddleError
from report_agent.parsing.parse_report import parse_report
from report_agent.parsing.schemas import RawReportItem

ITEM = RawReportItem(name="白细胞计数", value_text="8.0", value_num=8.0, unit="10^9/L")


class FakePaddle:
    def __init__(self, pages=None, exc=None):
        self._pages = pages or []
        self._exc = exc

    def parse_pdf(self, file_path):
        if self._exc:
            raise self._exc
        return self._pages


HEADER_TABLE = ("<table><tr><td>检查项目</td><td>测量结果</td><td>单位</td><td>异常描述</td>"
                "<td>参考区间</td></tr><tr><td>白细胞计数</td><td>8.0</td><td>10^9/L</td>"
                "<td></td><td>3.5-9.5</td></tr><tr><td>红细胞计数</td><td>5.60</td>"
                "<td>10^12/L</td><td></td><td>4.30-5.80</td></tr><tr><td>血红蛋白</td>"
                "<td>160</td><td>g/L</td><td></td><td>130-175</td></tr></table>")


def test_pdf_paddle_success(monkeypatch):
    monkeypatch.setattr("report_agent.parsing.parse_report.parse_images",
                        lambda pages, client: [])
    out = asyncio.run(parse_report("a.pdf", "pdf", Settings(), None,
                                   paddle=FakePaddle(pages=[HEADER_TABLE])))
    assert out.method == "paddle"  # items=3 ≥ parse_min_items=3,不触发视觉兜底
    assert len(out.items) == 3
    assert out.items[0].name == "白细胞计数" and out.items[0].value_num == 8.0
    assert out.failed_htmls == []


def test_pdf_paddle_failed_tables_recorded(monkeypatch):
    monkeypatch.setattr("report_agent.parsing.parse_report.parse_images",
                        lambda pages, client: [])
    bad = "<table><tr><td>甲</td><td>乙</td></tr><tr><td>x</td><td>1</td></tr></table>"
    out = asyncio.run(parse_report("a.pdf", "pdf", Settings(), None,
                                   paddle=FakePaddle(pages=[bad, HEADER_TABLE])))
    assert out.method == "paddle"
    assert len(out.failed_htmls) == 1  # 第 1 页无表头且无上一页 → unparsed(spec §7.3)
    assert len(out.items) == 3  # 第 2 页正常解析


def test_pdf_paddle_insufficient_falls_back_to_vision(monkeypatch):
    captured = {}

    async def fake_parse_images(pages, client):
        captured["pages"] = pages
        return [ITEM]

    monkeypatch.setattr("report_agent.parsing.parse_report.parse_images", fake_parse_images)
    monkeypatch.setattr("report_agent.parsing.parse_report.render_pdf_pages", lambda p: [b"x"])
    out = asyncio.run(parse_report("a.pdf", "pdf", Settings(), None,
                                   paddle=FakePaddle(pages=["只有文字无表格"])))
    assert out.method == "vision"  # 不足 → 视觉兜底
    assert captured["pages"] == [b"x"]


def test_pdf_paddle_error_falls_back_to_vision(monkeypatch):
    async def fake_parse_images(pages, client):
        return [ITEM]

    monkeypatch.setattr("report_agent.parsing.parse_report.parse_images", fake_parse_images)
    monkeypatch.setattr("report_agent.parsing.parse_report.render_pdf_pages", lambda p: [b"x"])
    out = asyncio.run(parse_report("a.pdf", "pdf", Settings(), None,
                                   paddle=FakePaddle(exc=PaddleError("超时"))))
    assert out.method == "vision"


def test_photo_source_uses_vision(monkeypatch, tmp_path):
    async def fake_parse_images(pages, client):
        return [ITEM]

    monkeypatch.setattr("report_agent.parsing.parse_report.parse_images", fake_parse_images)
    img = tmp_path / "a.jpg"
    img.write_bytes(b"img")
    out = asyncio.run(parse_report(str(img), "photo", Settings(), None))
    assert out.method == "vision" and out.items[0].name == "白细胞计数"
