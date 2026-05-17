"""Unit tests for evidence compilation."""

from __future__ import annotations

from src.domain.knowledge.enums import EvidenceType
from src.domain.knowledge.evidence import EvidenceManager
from src.domain.knowledge.schemas import EvidenceDraft


def test_evidence_manager_compiles_refs_and_dedupes() -> None:
    draft = EvidenceDraft(
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="Alice owns Alpha.",
    )

    result = EvidenceManager().compile(adapter_name="toy", version="v1", drafts=[draft, draft])

    assert len(result.evidence) == 1
    evidence_id = result.evidence[0].evidence_id
    assert result.ref_map[evidence_id] == [evidence_id]
    assert result.ref_map["note:n1"] == [evidence_id]
    assert result.ref_map["n1"] == [evidence_id]


def test_evidence_manager_dedupes_same_source_fingerprint() -> None:
    first = EvidenceDraft(
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="old content",
        payload={"title": "old shape"},
        metadata={"fingerprint": "fp-1"},
    )
    second = EvidenceDraft(
        evidence_type=EvidenceType.TEXT_SPAN,
        source_type="note",
        source_id="n1",
        content="new content after extraction shape changed",
        payload={"title": "new shape", "mentioned_entities": [{"name": "Alice"}]},
        metadata={"fingerprint": "fp-1"},
    )

    result = EvidenceManager().compile(adapter_name="toy", version="v1", drafts=[first, second])

    assert len(result.evidence) == 1
    assert result.evidence[0].source_fingerprint == "fp-1"
    assert result.evidence[0].status.value == "active"
    assert result.ref_map["note:n1"] == [result.evidence[0].evidence_id]
