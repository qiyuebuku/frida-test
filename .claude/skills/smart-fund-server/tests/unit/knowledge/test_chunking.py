from __future__ import annotations

import re

from src.domain.knowledge.chunking import build_chunks_for_compiled_evidence, build_evidence_chunks
from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.schemas import CompiledEvidence


def test_build_evidence_chunks_splits_long_chinese_text_with_manifest_metadata() -> None:
    text = "标题\n" + "第一段内容。" * 80 + "\n\n" + "第二段内容。" * 80

    chunks = build_evidence_chunks(
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:1",
        content=text,
        payload={"status": "active"},
        max_chars=120,
    )

    assert len(chunks) > 1
    assert chunks[0].chunk_id == "kg_chunk:kg_ev:financial:news:1:0"
    assert chunks[0].chunk_index == 0
    assert chunks[0].start_offset == 0
    assert chunks[0].next_chunk_id == chunks[1].chunk_id
    assert chunks[1].previous_chunk_id == chunks[0].chunk_id
    assert chunks[0].text_hash
    assert chunks[0].chunker_version == "recursive_zh_v1"
    assert all(len(chunk.content) <= 120 for chunk in chunks)
    assert all(chunk.end_offset <= len(text.strip()) for chunk in chunks if chunk.end_offset is not None)
    assert all(
        _compact(chunk.content) in _compact(text[chunk.start_offset:chunk.end_offset])
        for chunk in chunks
    )


def test_build_evidence_chunks_keeps_short_text_as_single_chunk() -> None:
    chunks = build_evidence_chunks(
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:short",
        content="短新闻内容。",
        payload={"status": "active"},
    )

    assert [chunk.chunk_id for chunk in chunks] == ["kg_chunk:kg_ev:financial:news:short:0"]
    assert chunks[0].previous_chunk_id == ""
    assert chunks[0].next_chunk_id == ""
    assert chunks[0].content == "短新闻内容。"


def test_build_chunks_for_compiled_evidence_offsets_use_raw_evidence_content() -> None:
    content = "正文第一段。" * 80 + "\n\n" + "正文第二段。" * 80
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:offset",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="news:offset",
        content=content,
        version="v1",
        payload={
            "title": "这个标题不应该改变 chunk offset",
            "summary": "摘要也不应该改变 chunk offset",
            "published_at": "2026-05-30T00:00:00+00:00",
        },
    )

    chunks = build_chunks_for_compiled_evidence(evidence)

    assert len(chunks) > 1
    assert all(chunk.end_offset <= len(content.strip()) for chunk in chunks if chunk.end_offset is not None)
    assert all(
        _compact(chunk.content) in _compact(content[chunk.start_offset:chunk.end_offset])
        for chunk in chunks
    )
    assert chunks[0].payload["published_at"] == "2026-05-30T00:00:00+00:00"


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")
