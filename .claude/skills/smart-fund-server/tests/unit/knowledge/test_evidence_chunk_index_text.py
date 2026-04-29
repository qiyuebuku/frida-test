"""Tests for evidence chunk index text construction."""

from __future__ import annotations

from types import SimpleNamespace

from src.infrastructure.persistence.repositories.knowledge_repository_impl import _chunk_content


def test_chunk_content_includes_entity_terms_for_vector_indexing() -> None:
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

    assert "技术发布会简析" in content
    assert "宁德时代" in content
    assert "300750" in content
    assert "SZ:300750" in content
    assert "补能生态" in content
