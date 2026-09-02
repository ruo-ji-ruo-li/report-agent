from report_agent.config import Settings
from report_agent.knowledge.doc_builder import DocDraft, build_indicator_doc, chunk_documents
from report_agent.knowledge.seed_schemas import IndicatorSeed


def _settings() -> Settings:
    return Settings(short_doc_max_chars=1200, chunk_size=500, chunk_overlap=50)


def test_short_doc_single_chunk():
    seed = IndicatorSeed(code="GLU", name="空腹血糖", narrative="短短的解释")
    doc = build_indicator_doc(seed)
    chunks = chunk_documents([doc], _settings())
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "indicator:GLU:0"
    assert chunks[0].parent_id == "indicator:GLU"
    assert chunks[0].entity_type == "indicator" and chunks[0].entity_id == "GLU"


def test_long_doc_split_by_headings_with_context_header():
    text = "# 空腹血糖\n\n## 指标说明\n" + "糖代谢指标。" * 300 + "\n\n## 参考区间\n成年 3.9-6.1 mmol/L\n"
    doc = DocDraft(entity_type="indicator", entity_id="GLU", title="空腹血糖", text=text)
    chunks = chunk_documents([doc], _settings())
    assert len(chunks) > 1
    # 长文档的每个 chunk 都有上下文头(文档标题 + 章节标题)
    for c in chunks:
        assert "文档标题: 空腹血糖" in c.text
        assert "章节:" in c.text
    # chunk_index/total_chunks 连续
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    assert all(c.total_chunks == len(chunks) for c in chunks)
