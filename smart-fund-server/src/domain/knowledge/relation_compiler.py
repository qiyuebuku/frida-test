"""Relation compilation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.knowledge.adapter import AdapterSpec, validate_edge_against_adapter
from src.domain.knowledge.enums import EdgeStatus
from src.domain.knowledge.ids import make_edge_id
from src.domain.knowledge.schemas import (
    CompiledEdge,
    EdgeDraft,
    FailedRecord,
    NodeDraft,
    ValidationIssue,
)


@dataclass(frozen=True)
class RelationCompileResult:
    edges: list[CompiledEdge] = field(default_factory=list)
    failed_records: list[FailedRecord] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)


class RelationCompiler:
    def compile(
        self,
        *,
        adapter_spec: AdapterSpec,
        version: str,
        drafts: list[EdgeDraft],
        draft_by_ref: dict[str, NodeDraft],
        node_id_by_ref: dict[str, str],
        evidence_ref_map: dict[str, list[str]],
    ) -> RelationCompileResult:
        edges_by_id: dict[str, CompiledEdge] = {}
        failed_records: list[FailedRecord] = []
        warnings: list[ValidationIssue] = []

        for draft in drafts:
            source_node = draft_by_ref.get(draft.source_ref)
            target_node = draft_by_ref.get(draft.target_ref)
            source_node_id = node_id_by_ref.get(draft.source_ref)
            target_node_id = node_id_by_ref.get(draft.target_ref)
            if not source_node or not target_node or not source_node_id or not target_node_id:
                failed_records.append(
                    FailedRecord(
                        source_type="edge",
                        source_id=draft.relation_type,
                        reason="edge endpoint cannot be resolved",
                        details={
                            "source_ref": draft.source_ref,
                            "target_ref": draft.target_ref,
                            "source_resolved": bool(source_node and source_node_id),
                            "target_resolved": bool(target_node and target_node_id),
                            "source_type": source_node.node_type if source_node else _ref_type(draft.source_ref),
                            "target_type": target_node.node_type if target_node else _ref_type(draft.target_ref),
                            "source_node_id": source_node_id,
                            "target_node_id": target_node_id,
                            "edge_properties": draft.properties,
                            "evidence_refs": draft.evidence_refs,
                        },
                    )
                )
                continue

            validation = validate_edge_against_adapter(
                adapter_spec,
                draft,
                source_node,
                target_node,
            )
            warnings.extend(validation.issues if validation.ok else [])
            if not validation.ok:
                failed_records.append(
                    FailedRecord(
                        source_type="edge",
                        source_id=draft.relation_type,
                        reason="edge validation failed",
                        details={
                            "source_ref": draft.source_ref,
                            "target_ref": draft.target_ref,
                            "source_type": source_node.node_type,
                            "target_type": target_node.node_type,
                            "issues": [issue.model_dump() for issue in validation.issues],
                        },
                    )
                )
                continue

            evidence_ids = _evidence_ids_for_refs(draft.evidence_refs, evidence_ref_map)
            if draft.status == EdgeStatus.ACTIVE and not evidence_ids:
                failed_records.append(
                    FailedRecord(
                        source_type="edge",
                        source_id=draft.relation_type,
                        reason="edge evidence cannot be resolved",
                        details={"evidence_refs": draft.evidence_refs},
                    )
                )
                continue
            edge_id = make_edge_id(
                adapter_spec.name,
                draft.relation_type,
                source_node_id,
                target_node_id,
                evidence_ids,
            )
            edges_by_id[edge_id] = CompiledEdge(
                edge_id=edge_id,
                adapter_name=adapter_spec.name,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                relation_type=draft.relation_type,
                properties=draft.properties,
                confidence_label=draft.confidence_label,
                confidence_score=draft.confidence_score,
                status=draft.status,
                evidence_ids=evidence_ids,
                valid_from=draft.valid_from,
                valid_to=draft.valid_to,
                version=version,
            )

        return RelationCompileResult(
            edges=list(edges_by_id.values()),
            failed_records=failed_records,
            warnings=warnings,
        )


def _evidence_ids_for_refs(refs: list[str], evidence_ref_map: dict[str, list[str]]) -> list[str]:
    result: list[str] = []
    for ref in refs:
        for evidence_id in evidence_ref_map.get(ref, []):
            if evidence_id not in result:
                result.append(evidence_id)
    return result


def _ref_type(ref: str) -> str | None:
    if ":" not in ref:
        return None
    prefix = ref.split(":", 1)[0].strip()
    return prefix or None
