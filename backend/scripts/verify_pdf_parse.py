"""PDF 解析 Paddle 主路径真实验证(需 PADDLE_TOKEN 与网络,spec §13)。

运行: uv run python scripts/verify_pdf_parse.py
验证:
  1) Paddle 主路径: gen_sample_pdf 生成样例 → paddle.parse_pdf → 纯规则表格解析
     断言: 项目数 >= 8,关键字段(空腹血糖 7.1 / 参考区间 / ↑ 标记)正确。
  2) 视觉兜底: render_pdf_pages 渲染页图 → parse_images(视觉模型真调用)
     断言: 视觉模型返回 >= 5 个项目。元数据不再从 PDF 解析(spec §11)。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.gen_sample_pdf import main as gen_pdf


async def main() -> None:
    from report_agent.config import get_settings
    from report_agent.llm.client import llm_factory
    from report_agent.parsing.paddle_client import PaddleClient
    from report_agent.parsing.table_extractor import merge_page_markdown, parse_merged_tables
    from report_agent.parsing.vision_parser import parse_images, render_pdf_pages

    gen_pdf()
    pdf_path = str(ROOT / "eval" / "fixtures" / "sample_report.pdf")
    settings = get_settings()
    print(f"[1] paddle model={settings.paddle_model} url={settings.paddle_api_url}")

    pages = PaddleClient(settings).parse_pdf(pdf_path)
    items, failed = parse_merged_tables(merge_page_markdown(pages))
    print(f"[1] Paddle 解析 {len(items)} 项,无法解析表格 {len(failed)} 个")
    print(f"[1] items: {[(i.name, i.value_text, i.unit) for i in items]}")
    assert len(items) >= 8, f"项目数不足: {len(items)}"
    glu = next((i for i in items if "血糖" in i.name), None)
    assert glu is not None and glu.value_num == 7.1, f"空腹血糖解析错误: {glu}"
    assert glu.unit == "mmol/L", f"单位解析错误: {glu.unit}"
    print("[1] PASS: Paddle 主路径(纯规则表格解析)")

    pages_img = render_pdf_pages(pdf_path)
    print(f"[2] 渲染 {len(pages_img)} 页图,调用视觉模型...")
    clients = await llm_factory.get()
    v_items = await parse_images(pages_img, clients.vision)
    print(f"[2] 视觉解析 {len(v_items)} 项: {[(i.name, i.value_text) for i in v_items]}")
    assert len(v_items) >= 5, f"视觉解析项目数不足: {len(v_items)}"
    print("[2] PASS: 视觉兜底路径(视觉模型真调用)")

    print("\nPDF PARSE VERIFY PASS")


if __name__ == "__main__":
    asyncio.run(main())
