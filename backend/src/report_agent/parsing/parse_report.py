"""解析编排:Paddle 纯规则主路径(spec §3)→ 不足/失败降级视觉;照片 → 视觉。

失败语义:PaddleError → 视觉兜底;视觉解析 LLMError 向上抛(调用方重试后仍失败则
任务 failed);返回 None 仅用于无法解析的来源(非 pdf/photo)。
中间产物(合并 markdown/Paddle 输出)不落盘,只保留用户上传原始文件(spec §3)。
"""
import asyncio
from dataclasses import dataclass

from report_agent.config import Settings
from report_agent.observability import get_logger
from report_agent.parsing.paddle_client import PaddleClient, PaddleError
from report_agent.parsing.schemas import RawReportItem
from report_agent.parsing.table_extractor import merge_page_markdown, parse_merged_tables
from report_agent.parsing.vision_parser import parse_images, render_pdf_pages

log = get_logger(__name__)


@dataclass
class ParseOutput:
    items: list[RawReportItem]
    method: str  # paddle / vision
    failed_htmls: list[str]  # 无法解析表格的原始 HTML(spec §7.1,仅 Paddle 路径产生)


async def parse_report(file_path: str | None, source: str, settings: Settings, llms,
                       paddle: PaddleClient | None = None) -> ParseOutput | None:
    # llms 仅视觉路径使用;为 None 时(测试打桩 parse_images 后)跳过属性解引用
    vision_client = llms.vision if llms is not None else None
    if source == "photo":
        pages = [open(file_path, "rb").read()]  # noqa: SIM115, ASYNC230 —— 单页图片
        items = await parse_images(pages, vision_client)
        return ParseOutput(items=items, method="vision", failed_htmls=[])
    if source == "pdf":
        paddle = paddle or PaddleClient(settings)
        items: list[RawReportItem] = []
        failed: list[str] = []
        try:
            pages_md = await asyncio.to_thread(paddle.parse_pdf, file_path)
            merged = merge_page_markdown(pages_md)
            items, failed = parse_merged_tables(merged)
            if len(items) >= settings.parse_min_items:
                return ParseOutput(items=items, method="paddle", failed_htmls=failed)
            log.warning("paddle_insufficient_falling_back_to_vision", path=file_path,
                        count=len(items), threshold=settings.parse_min_items)
        except PaddleError as e:
            log.warning("paddle_failed_falling_back_to_vision", path=file_path, error=str(e))
        pages = render_pdf_pages(file_path)
        items = await parse_images(pages, vision_client)
        return ParseOutput(items=items, method="vision", failed_htmls=[])
    return None
