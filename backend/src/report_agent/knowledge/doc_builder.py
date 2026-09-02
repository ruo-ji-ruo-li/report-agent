"""KG 事实 → 释义文档 → 分块(派生检索层,spec §4.3)。

分块三级策略(自 C9 移植 + 域适配):
1. 短文档(≤ short_doc_max_chars)整篇单块——KG 派生文档的常态
2. 长文档按 '## ' 二级标题分节,节内超长再滑窗
3. 多块文档的每个 chunk 前置上下文头(文档标题 + 章节标题),保证脱离全文可读
不做父文档回填(spec §4.3 已确认移除)。
"""
import re
from dataclasses import dataclass

from report_agent.config import Settings
from report_agent.knowledge.milvus_client import KnowledgeChunk
from report_agent.knowledge.seed_schemas import IndicatorSeed, SuggestYaml


@dataclass
class DocDraft:
    entity_type: str
    entity_id: str
    title: str
    text: str


def _suggests_lines(items: list[SuggestYaml], label: str) -> str:
    if not items:
        return ""
    lines = [f"## {label}", ""]
    for s in items:
        strength = {"strong": "强", "medium": "中", "weak": "弱"}.get(s.strength, s.strength)
        lines.append(f"- {s.condition}(强度:{strength})")
        if s.note:
            lines.append(f"  - {s.note}")
    return "\n".join(lines) + "\n\n"


def build_indicator_doc(seed: IndicatorSeed) -> DocDraft:
    parts = [f"# {seed.name}"]
    if seed.aliases:
        parts.append(f"别名: {'、'.join(seed.aliases)}")
    parts.append("")
    parts.append("## 指标说明")
    parts.append("")
    if seed.description:
        parts.append(seed.description)
        parts.append("")
    if seed.narrative:
        parts.append(seed.narrative)
        parts.append("")
    if seed.ranges:
        parts.append("## 参考区间")
        parts.append("")
        for r in seed.ranges:
            sex = {"male": "男", "female": "女", "any": "通用"}[r.sex]
            lo = r.low if r.low is not None else "-∞"
            hi = r.high if r.high is not None else "+∞"
            parts.append(f"- {sex} {r.age_min:.0f}-{r.age_max:.0f} 岁: {lo} ~ {hi} {r.unit or seed.unit or ''}".rstrip())
            if r.critical_low is not None or r.critical_high is not None:
                parts.append(f"  危急值: <{r.critical_low} 或 >{r.critical_high}".replace("None", "?"))
        parts.append("")
    parts.append(_suggests_lines(seed.high_suggests, "升高提示"))
    parts.append(_suggests_lines(seed.low_suggests, "降低提示"))
    if seed.interventions:
        parts.append("## 干预建议")
        parts.append("")
        for iv in seed.interventions:
            tf = f",时间窗: {iv.timeframe}" if iv.timeframe else ""
            deps = f",科室: {'、'.join(iv.departments)}" if iv.departments else ""
            parts.append(f"- [{iv.level}] {iv.text}{tf}{deps}")
    return DocDraft(
        entity_type="indicator", entity_id=seed.code, title=seed.name, text="\n".join(parts) + "\n",
    )


def build_condition_docs(seeds: list[IndicatorSeed]) -> list[DocDraft]:
    """按疾病/风险方向聚合所有种子里的提示,生成 Condition 释义文档。"""
    by_cond: dict[str, dict] = {}
    for seed in seeds:
        for s in seed.high_suggests + seed.low_suggests:
            c = by_cond.setdefault(
                s.condition, {"description": s.description, "mentions": []}
            )
            for direction, lst in (("升高", seed.high_suggests), ("降低", seed.low_suggests)):
                if s in lst:
                    c["mentions"].append(f"{seed.name}({seed.code}) {direction}提示")
    docs = []
    for name, c in by_cond.items():
        body = [f"# {name}", ""]
        if c["description"]:
            body += [c["description"], ""]
        body += ["## 关联指标", ""] + [f"- {m}" for m in sorted(set(c["mentions"]))]
        docs.append(DocDraft(entity_type="condition", entity_id=name, title=name,
                             text="\n".join(body) + "\n"))
    return docs


def _sliding(text: str, size: int, overlap: int) -> list[str]:
    parts, start = [], 0
    while start < len(text):
        parts.append(text[start:start + size])
        if start + size >= len(text):
            break
        start += size - overlap
    return parts


def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """按 '\n## ' 分节,返回 [(章节标题, 正文)];首段(无标题)标题为 None。"""
    parts = re.split(r"\n(?=## )", text)
    out = []
    for part in parts:
        m = re.match(r"^## (.+)\n", part)
        if m:
            out.append((m.group(1).strip(), part[m.end():]))
        else:
            out.append((None, part))
    return out


def chunk_documents(docs: list[DocDraft], s: Settings) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    for d in docs:
        if len(d.text) <= s.short_doc_max_chars:
            chunks.append(KnowledgeChunk(
                chunk_id=f"{d.entity_type}:{d.entity_id}:0", text=d.text,
                entity_type=d.entity_type, entity_id=d.entity_id, title=d.title,
                section_title=None, chunk_index=0, total_chunks=1,
                parent_id=f"{d.entity_type}:{d.entity_id}",
            ))
            continue
        sections = _split_sections(d.text)
        parts: list[tuple[str | None, str]] = []
        for sec_title, body in sections:
            if len(body) <= s.chunk_size:
                parts.append((sec_title, body))
            else:
                for piece in _sliding(body, s.chunk_size, s.chunk_overlap):
                    parts.append((sec_title, piece))
        for i, (sec_title, body) in enumerate(parts):
            header = f"文档标题: {d.title}\n章节: {sec_title or '概述'}\n\n"
            chunks.append(KnowledgeChunk(
                chunk_id=f"{d.entity_type}:{d.entity_id}:{i}", text=header + body,
                entity_type=d.entity_type, entity_id=d.entity_id, title=d.title,
                section_title=sec_title, chunk_index=i, total_chunks=len(parts),
                parent_id=f"{d.entity_type}:{d.entity_id}",
            ))
    return chunks
