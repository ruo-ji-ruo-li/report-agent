"""解析编排:电子 PDF → 文字层(Unstructured),不足降级视觉;拍照 → 视觉。

失败语义:视觉解析 LLMError 向上抛(调用方重试后仍失败则任务 failed);
返回 None 仅用于无法解析的来源(非 pdf/photo),视觉兜底一旦触发必然返回 ParseOutput。
"""
from dataclasses import dataclass

from report_agent.config import Settings
from report_agent.observability import get_logger
from report_agent.parsing.pdf_parser import parse_pdf_text
from report_agent.parsing.schemas import RawReportItem, ReportMeta
from report_agent.parsing.vision_parser import parse_images, render_pdf_pages

log = get_logger(__name__)


@dataclass
class ParseOutput:
    items: list[RawReportItem]
    meta: ReportMeta
    method: str  # unstructured / vision / manual


async def parse_report(file_path: str | None, source: str, settings: Settings, llms) -> ParseOutput | None:
    # llms 仅视觉路径使用;为 None 时(测试打桩 parse_images 后)跳过属性解引用
    vision_client = llms.vision if llms is not None else None
    if source == "photo":
        pages = [open(file_path, "rb").read()]  # noqa: SIM115, ASYNC230 —— 单页图片
        items, meta = await parse_images(pages, vision_client)
        return ParseOutput(items=items, meta=meta, method="vision")
    if source == "pdf":
        parsed = parse_pdf_text(file_path, settings)
        if parsed is not None:
            items, meta = parsed
            return ParseOutput(items=items, meta=meta, method="unstructured")
        log.warning("pdf_text_insufficient_falling_back_to_vision", path=file_path)
        pages = render_pdf_pages(file_path)
        items, meta = await parse_images(pages, vision_client)
        return ParseOutput(items=items, meta=meta, method="vision")
    return None
