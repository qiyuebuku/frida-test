"""Tests for evidence chunk index text construction."""

from __future__ import annotations

from types import SimpleNamespace

from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.schemas import CompiledEvidence
from src.domain.knowledge.schemas import EvidenceChunk
from src.domain.knowledge.semantic_index_materials import build_semantic_vector_documents
from src.infrastructure.persistence.models.knowledge import KnowledgeEvidence
from src.infrastructure.persistence.models.knowledge import KnowledgeEvidenceChunk
from src.infrastructure.persistence.repositories.knowledge_repository_impl import _chunk_content
from src.infrastructure.persistence.repositories.knowledge_repository_impl import _chunk_schema
from src.infrastructure.persistence.repositories.knowledge_repository_impl import _chunk_values_from_schema
from src.infrastructure.persistence.repositories.knowledge_repository_impl import _evidence_values


def test_chunk_content_uses_raw_evidence_content_for_offsets() -> None:
    row = SimpleNamespace(
        content="技术迭代驱动多维增长，补能生态加速布局。",
        payload={
            "title": "技术发布会简析",
            "mentioned_entities": [
                {"type": "stock", "exchange": "SZ", "code": "300750", "name": "宁德时代"}
            ],
        },
    )

    content = _chunk_content(row)

    assert content == "技术迭代驱动多维增长，补能生态加速布局。"
    assert "补能生态" in content


def test_semantic_chunk_document_includes_metadata_terms_without_pg_payload_duplication() -> None:
    chunk = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:1:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:1",
        content="技术迭代驱动多维增长，补能生态加速布局。",
        payload={
            "title": "技术发布会简析",
            "summary": "补能生态推进",
            "source_type": "news_articles",
            "source_id": "news:1",
            "mentioned_entities": [
                {"type": "stock", "exchange": "SZ", "code": "300750", "name": "宁德时代"}
            ],
        },
    )

    documents = build_semantic_vector_documents(chunks=[chunk], nodes=[], edges=[], include_community=False)
    text = documents[0].text

    assert "技术发布会简析" in text
    assert "宁德时代" in text
    assert "300750" in text
    assert "SZ:300750" in text
    assert "补能生态" in text


def test_chunk_manifest_row_does_not_store_payload_in_pg() -> None:
    chunk = EvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:1:0",
        adapter_name="financial",
        evidence_id="kg_ev:financial:news:1",
        content="这是一段完整 chunk 文本。",
        chunk_index=0,
        start_offset=0,
        end_offset=12,
        text_hash="hash",
        chunker_version="recursive_zh_v1",
        payload={
            "title": "新闻标题",
            "summary": "短摘要",
            "text": "完整正文不应该进入 kg_evidence_chunks.payload",
            "content": "完整内容不应该进入 kg_evidence_chunks.payload",
            "raw_text": "原始文本不应该进入 kg_evidence_chunks.payload",
            "source_id": "news:1",
        },
    )

    row = _chunk_values_from_schema(chunk)

    assert "content" not in row
    assert "payload" not in row


def test_chunk_schema_restores_evidence_metadata_without_storing_chunk_payload() -> None:
    evidence = KnowledgeEvidence(
        evidence_id="kg_ev:financial:news:1",
        adapter_name="financial",
        source_type="news_articles",
        source_id="news:1",
        evidence_type="text_span",
        content="第一段内容。第二段内容。",
        payload={
            "title": "新闻标题",
            "published_at": "2026-05-30T00:00:00+00:00",
        },
        version="v1",
        status="active",
    )
    row = KnowledgeEvidenceChunk(
        chunk_id="kg_chunk:kg_ev:financial:news:1:0",
        adapter_name="financial",
        evidence_id=evidence.evidence_id,
        chunk_index=0,
        start_offset=0,
        end_offset=5,
        previous_chunk_id="",
        next_chunk_id="kg_chunk:kg_ev:financial:news:1:1",
        text_hash="hash",
        chunker_version="recursive_zh_v1",
    )

    chunk = _chunk_schema(row, evidence)

    assert chunk.content == "第一段内容"
    assert chunk.payload["published_at"] == "2026-05-30T00:00:00+00:00"
    assert chunk.payload["source_type"] == "news_articles"
    assert chunk.payload["source_id"] == "news:1"
    assert chunk.payload["chunk_index"] == 0


def test_evidence_payload_does_not_duplicate_readable_content() -> None:
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:1",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="news:1",
        content="完整正文保存在 content 字段。",
        version="v1",
        payload={
            "title": "新闻标题",
            "summary": "短摘要",
            "text": "正文不应该重复进入 kg_evidence.payload",
            "raw_text": "原始正文不应该重复进入 kg_evidence.payload",
            "url": "https://example.local/news/1",
        },
    )

    row = _evidence_values(evidence)

    assert row["content"] == "完整正文保存在 content 字段。"
    assert row["payload"] == {
        "title": "新闻标题",
        "summary": "短摘要",
        "url": "https://example.local/news/1",
    }
