"""Unit tests for deterministic wiki generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus
from src.domain.knowledge.compiler import KnowledgeCompiler
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode
from src.domain.knowledge.toy_adapter import ToyProjectAdapter
from src.domain.knowledge.wiki import KnowledgeWikiBuilder, lint_wiki_pages


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "knowledge"


@pytest.mark.asyncio
async def test_wiki_builder_generates_entity_and_index_pages() -> None:
    result = await _compile_toy()

    wiki = KnowledgeWikiBuilder().build(
        adapter_name=result.adapter_name,
        version=result.version,
        nodes=result.nodes,
        edges=result.edges,
        evidence=result.evidence,
    )

    assert wiki.issues == []
    assert len(wiki.pages) == 8
    assert {page.page_type for page in wiki.pages} == {"entity_page", "relation_page", "index_page"}
    assert all(page.source_node_ids for page in wiki.pages)
    assert any(page.title == "Alpha" and page.source_edge_ids for page in wiki.pages)
    relation_pages = [page for page in wiki.pages if page.page_type == "relation_page"]
    assert {page.subject_id for page in relation_pages} == {"blocks", "owns", "references"}
    assert any("Alice -> Alpha" in page.content for page in relation_pages)
    assert any("Alice owns Alpha" in page.content for page in wiki.pages)


@pytest.mark.asyncio
async def test_wiki_lint_reports_missing_evidence_reference() -> None:
    result = await _compile_toy()
    wiki = KnowledgeWikiBuilder().build(
        adapter_name=result.adapter_name,
        version=result.version,
        nodes=result.nodes,
        edges=result.edges,
        evidence=result.evidence,
    )
    broken = wiki.pages[0].model_copy(update={"source_evidence_ids": ["missing"]})

    issues = lint_wiki_pages([broken], result.nodes, result.edges, result.evidence)

    assert len(issues) == 1
    assert issues[0].message == "page references missing evidence"


def test_wiki_builder_generates_timeline_page_for_events() -> None:
    event = CompiledNode(
        node_id="kg:financial:event:1",
        adapter_name="financial",
        node_type="event",
        canonical_name="宁德时代技术发布会",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    stock = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edge = CompiledEdge(
        edge_id="kg_edge:financial:mentions:1",
        adapter_name="financial",
        source_node_id=event.node_id,
        target_node_id=stock.node_id,
        relation_type="mentions",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=0.7,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:financial:news:1"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:news:1",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="news_articles",
        source_id="ft_news:74342",
        content="技术迭代驱动多维增长，补能生态加速布局",
        payload={"published_at": "2026-04-23T00:00:00+08:00"},
        version="v1",
    )

    wiki = KnowledgeWikiBuilder().build(
        adapter_name="financial",
        version="v1",
        nodes=[event, stock],
        edges=[edge],
        evidence=[evidence],
    )

    assert wiki.issues == []
    timeline = next(page for page in wiki.pages if page.page_type == "timeline_page")
    assert timeline.subject_type == "timeline"
    assert "2026-04-23T00:00:00+08:00" in timeline.content
    assert "ft_news:74342" in timeline.content


def test_wiki_timeline_sorts_mixed_naive_and_aware_evidence_times() -> None:
    event = CompiledNode(
        node_id="kg:financial:event:1",
        adapter_name="financial",
        node_type="event",
        canonical_name="事件",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    stock = CompiledNode(
        node_id="kg:financial:stock:300750",
        adapter_name="financial",
        node_type="stock",
        canonical_name="宁德时代",
        status=NodeStatus.ACTIVE,
        version="v1",
    )
    edges = [
        CompiledEdge(
            edge_id=f"kg_edge:financial:mentions:{idx}",
            adapter_name="financial",
            source_node_id=event.node_id,
            target_node_id=stock.node_id,
            relation_type="mentions",
            confidence_label=ConfidenceLabel.EXTRACTED,
            confidence_score=0.7,
            status=EdgeStatus.ACTIVE,
            evidence_ids=[evidence_id],
            version="v1",
        )
        for idx, evidence_id in enumerate(
            ["kg_ev:financial:news:aware", "kg_ev:financial:news:naive"], start=1
        )
    ]
    evidence = [
        CompiledEvidence(
            evidence_id="kg_ev:financial:news:aware",
            adapter_name="financial",
            evidence_type=EvidenceType.TEXT_SPAN,
            source_type="news_articles",
            source_id="ft_news:aware",
            content="aware",
            payload={"published_at": "2026-04-23T00:00:00+08:00"},
            version="v1",
        ),
        CompiledEvidence(
            evidence_id="kg_ev:financial:news:naive",
            adapter_name="financial",
            evidence_type=EvidenceType.TEXT_SPAN,
            source_type="news_articles",
            source_id="ft_news:naive",
            content="naive",
            payload={"published_at": "2026-04-24T00:00:00"},
            version="v1",
        ),
    ]

    wiki = KnowledgeWikiBuilder().build(
        adapter_name="financial",
        version="v1",
        nodes=[event, stock],
        edges=edges,
        evidence=evidence,
    )

    assert wiki.issues == []
    timeline = next(page for page in wiki.pages if page.page_type == "timeline_page")
    assert timeline.content.index("ft_news:naive") < timeline.content.index("ft_news:aware")


async def _compile_toy():
    records = json.loads((FIXTURE_DIR / "toy_records.json").read_text(encoding="utf-8"))
    return await KnowledgeCompiler().compile(ToyProjectAdapter(), records)
