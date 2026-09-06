"""拍照/扫描件与视觉兜底:页图 → 多模态 LLM → items;元数据来自表单,不再提取(spec §8)。"""
import base64

from report_agent.llm.client import DeepSeekClient
from report_agent.llm.prompts import load_prompt
from report_agent.observability import get_logger
from report_agent.parsing.schemas import RawReportItem

log = get_logger(__name__)


def render_pdf_pages(path: str) -> list[bytes]:
    import pymupdf

    doc = pymupdf.open(path)
    try:
        return [page.get_pixmap(dpi=150).tobytes("png") for page in doc]
    finally:
        doc.close()


def _page_message(page: bytes) -> list[dict]:
    b64 = base64.b64encode(page).decode("ascii")
    return [
        {"role": "system", "content": load_prompt("parse_vision")},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]},
    ]


async def parse_images(pages: list[bytes], client: DeepSeekClient) -> list[RawReportItem]:
    items: list[RawReportItem] = []
    for i, page in enumerate(pages):
        data = await client.complete_json(_page_message(page))
        for it in data.get("items", []):
            items.append(RawReportItem(
                name=it.get("name", ""), value_text=it.get("value_text"),
                value_num=it.get("value_num"), unit=it.get("unit"),
                ref_range_text=it.get("ref_range_text"), abnormal_flag=it.get("abnormal_flag"),
                code=it.get("code"),
            ))
        log.info("vision_page_parsed", page=i + 1, items=len(data.get("items", [])))
    return items
