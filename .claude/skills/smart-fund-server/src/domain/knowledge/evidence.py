"""Evidence compilation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.knowledge.ids import make_evidence_id
from src.domain.knowledge.schemas import CompiledEvidence, EvidenceDraft


@dataclass(frozen=True)
class EvidenceCompileResult:
    evidence: list[CompiledEvidence] = field(default_factory=list)
    ref_map: dict[str, list[str]] = field(default_factory=dict)


class EvidenceManager:
    def compile(
        self,
        *,
        adapter_name: str,
        version: str,
        drafts: list[EvidenceDraft],
    ) -> EvidenceCompileResult:
        evidence_by_id: dict[str, CompiledEvidence] = {}
        ref_map: dict[str, list[str]] = {}

        for draft in drafts:
            evidence_id = make_evidence_id(
                adapter_name,
                draft.source_type,
                draft.source_id,
                draft.evidence_type,
                draft.content,
                draft.payload,
            )
            evidence = CompiledEvidence(
                evidence_id=evidence_id,
                adapter_name=adapter_name,
                evidence_type=draft.evidence_type,
                source_type=draft.source_type,
                source_id=draft.source_id,
                content=draft.content,
                payload=draft.payload,
                span_start=draft.span_start,
                span_end=draft.span_end,
                version=version,
                metadata=draft.metadata,
            )
            evidence_by_id[evidence_id] = evidence

            for ref in _refs_for_draft(draft, evidence_id):
                ref_map.setdefault(ref, [])
                if evidence_id not in ref_map[ref]:
                    ref_map[ref].append(evidence_id)

        return EvidenceCompileResult(
            evidence=list(evidence_by_id.values()),
            ref_map=ref_map,
        )


def _refs_for_draft(draft: EvidenceDraft, evidence_id: str) -> list[str]:
    refs = [
        evidence_id,
        f"{draft.source_type}:{draft.source_id}",
        draft.source_id,
    ]
    return [item for item in refs if item]
