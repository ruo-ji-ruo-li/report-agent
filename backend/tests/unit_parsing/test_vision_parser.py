import asyncio

from report_agent.config import Settings
from report_agent.parsing.parse_report import parse_report
from report_agent.parsing.vision_parser import parse_images


def test_parse_images_returns_items_only(monkeypatch):
    pages = [b"png1", b"png2"]
    responses = [
        {"items": [
            {"name": "白细胞计数", "code": "WBC", "value_text": "6.2",
             "value_num": 6.2, "unit": "10^9/L", "ref_range_text": "3.5-9.5",
             "abnormal_flag": None}]},
        {"items": [
            {"name": "血红蛋白", "code": "Hb", "value_text": "150",
             "value_num": 150, "unit": "g/L", "ref_range_text": "130-175",
             "abnormal_flag": None}]},
    ]

    class FakeClient:
        async def complete_json(self, messages, retry_feedback=True):
            return responses.pop(0)

    items = asyncio.run(parse_images(pages, FakeClient()))
    assert [i.name for i in items] == ["白细胞计数", "血红蛋白"]
    assert items[0].code == "WBC"


def test_parse_report_pdf_falls_back_to_vision(monkeypatch):
    async def fake_parse_images(pages, client):
        from report_agent.parsing.schemas import RawReportItem
        return [RawReportItem(name="白细胞计数", value_text="6.2", value_num=6.2,
                              unit="10^9/L", ref_range_text="3.5-9.5", abnormal_flag=None)]

    monkeypatch.setattr("report_agent.parsing.parse_report.parse_pdf_text", lambda p, s: None)
    monkeypatch.setattr("report_agent.parsing.parse_report.parse_images", fake_parse_images)
    monkeypatch.setattr("report_agent.parsing.parse_report.render_pdf_pages", lambda p: [b"x"])

    out = asyncio.run(parse_report("x.pdf", "pdf", Settings(), None))
    assert out.method == "vision"  # 文字层不足 → 视觉兜底
    assert out.items[0].name == "白细胞计数"
