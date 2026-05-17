"""Unit tests for the generic knowledge repository contract."""

from __future__ import annotations

import ast
from pathlib import Path

from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
    _edge_values,
    _evidence_values,
    _node_values,
)
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, NodeStatus


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPO_CONTRACT = (
    PROJECT_ROOT / "src" / "domain" / "knowledge" / "repositories" / "knowledge_repository.py"
)


def test_knowledge_repository_contract_methods() -> None:
    assert {
        "upsert_nodes",
        "upsert_edges",
        "upsert_evidence",
        "attach_edge_evidence",
        "get_node",
        "get_edge",
        "get_evidence",
        "get_edge_evidence",
        "create_compilation_run",
        "finish_compilation_run",
    }.issubset(KnowledgeRepository.__abstractmethods__)


def test_knowledge_repository_contract_does_not_import_persistence_or_drivers() -> None:
    imports = _imports(REPO_CONTRACT)

    forbidden = ["src.infrastructure", "sqlalchemy", "psycopg2", "asyncpg"]
    violations = [
        item
        for item in imports
        for prefix in forbidden
        if item == prefix or item.startswith(f"{prefix}.")
    ]
    assert not violations


def test_repository_node_value_mapping_uses_generic_schema() -> None:
    node = CompiledNode(
        node_id="kg:toy:project:alpha",
        adapter_name="toy",
        node_type="project",
        canonical_name="Alpha",
        aliases=["A"],
        external_ids={"source": "alpha"},
        properties={"kind": "demo"},
        status=NodeStatus.ACTIVE,
        version="v1",
    )

    values = _node_values(node)

    assert values["node_id"] == node.node_id
    assert values["stable_key"] == node.node_id
    assert values["status"] == "active"
    assert values["properties"] == {"kind": "demo"}


def test_repository_edge_and_evidence_value_mapping_uses_generic_schema() -> None:
    edge = CompiledEdge(
        edge_id="kg_edge:toy:owns:abc",
        adapter_name="toy",
        source_node_id="kg:toy:person:alice",
        target_node_id="kg:toy:project:alpha",
        relation_type="owns",
        confidence_label=ConfidenceLabel.EXTRACTED,
        confidence_score=1.0,
        status=EdgeStatus.ACTIVE,
        evidence_ids=["kg_ev:toy:note:n1:abc"],
        version="v1",
    )
    evidence = CompiledEvidence(
        evidence_id="kg_ev:toy:note:n1:abc",
        adapter_name="toy",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="Alice owns Alpha.",
        version="v1",
    )

    edge_values = _edge_values(edge)
    evidence_values = _evidence_values(evidence)

    assert edge_values["confidence_label"] == "EXTRACTED"
    assert edge_values["status"] == "active"
    assert evidence_values["evidence_type"] == "text_span"
    assert evidence_values["status"] == "active"
    assert evidence_values["superseded_by"] is None
    assert evidence_values["metadata_"] == {}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names
