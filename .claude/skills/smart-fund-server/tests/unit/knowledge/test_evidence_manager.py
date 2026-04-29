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
