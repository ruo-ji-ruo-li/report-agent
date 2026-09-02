"""生成样例电子 PDF(文字层 + 简单表格),供 smoke 与解析演示。
运行: uv run python scripts/gen_sample_pdf.py [输出路径,默认 eval/fixtures/sample_report.pdf]
"""
import sys
from pathlib import Path

import pymupdf


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("eval/fixtures/sample_report.pdf")
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    lines = [
        "阳光体检中心", "姓名: 张三  性别: 男  年龄: 45  日期: 2026-08-20",
        "----------------------------------------------------------------",
        "检验项目            结果        单位      参考区间      标记",
        "白细胞计数          6.2         10^9/L    3.5-9.5",
        "红细胞计数          5.1         10^12/L   4.3-5.8",
        "血红蛋白            150         g/L       130-175",
        "空腹血糖            7.1         mmol/L    3.9-6.1    ↑",
        "总胆固醇            5.4         mmol/L    2.8-5.7",
        "甘油三酯            2.5         mmol/L    0.4-1.7    ↑",
        "谷丙转氨酶          80          U/L       9-50    ↑",
        "肌酐                72          umol/L    57-97",
        "----------------------------------------------------------------",
        "注: 本报告仅供临床参考。",
    ]
    y = 72
    for text in lines:
        # (brief 偏差,最小修正: insert_text 默认 base-14 helv 无 CJK 字形,pymupdf
        #  1.28 下所有中文以 notdef 记入内容流,提取为不可读字符 —— 样例 PDF 文字层
        #  必须可读才能喂解析演示与 smoke;改用 MuPDF 内置中文字体 china-s,零外部依赖。
        #  另:行尾标记 ↑ 与原文 8/11 空格留白会在 china-s@11pt 下越过 595pt 页宽,
        #  字形被 MuPDF 丢弃(渲染与提取均不可见),统一收窄为 4 空格,纯外观微调)
        page.insert_text((50, y), text, fontsize=11, fontname="china-s")
        y += 26
    doc.save(out)
    doc.close()
    print(f"[done] {out}")


if __name__ == "__main__":
    main()
