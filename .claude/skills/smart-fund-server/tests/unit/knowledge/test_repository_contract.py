"""当前知识仓储契约测试。"""

from __future__ import annotations

import ast
from pathlib import Path

from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.repositories import KnowledgeRepository
from src.domain.knowledge.schemas import CompiledEvidence
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
    _evidence_values,
)


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REPO_CONTRACT = (
    PROJECT_ROOT / "src" / "domain" / "knowledge" / "repositories" / "knowledge_repository.py"
)


def test_knowledge_repository_contract_matches_current_evidence_card_pipeline() -> None:
    assert {
        "upsert_evidence",
        "get_evidence",
        "list_evidence",
        "upsert_evidence_chunks",
        "replace_atomic_cognitive_cards_for_evidence",
        "list_atomic_cognitive_card_manifests",
        "create_compilation_run",
        "finish_compilation_run",
    }.issubset(KnowledgeRepository.__abstractmethods__)
    assert not {
        "upsert_nodes",
        "upsert_edges",
        "attach_edge_evidence",
        "get_node",
        "get_edge",
    }.intersection(KnowledgeRepository.__abstractmethods__)


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


def test_repository_evidence_value_mapping_uses_current_schema() -> None:
    evidence = CompiledEvidence(
        evidence_id="kg_ev:financial:note:n1:abc",
        adapter_name="financial",
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="原始证据。",
        version="v1",
    )

    values = _evidence_values(evidence)

    assert values["evidence_type"] == "text_span"
    assert values["status"] == "active"
    assert values["superseded_by"] is None
    assert values["metadata_"] == {}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names
