"""Deterministic node compilation and reference resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.domain.knowledge.adapter import AdapterSpec, validate_node_against_adapter
from src.domain.knowledge.ids import make_node_id
from src.domain.knowledge.schemas import (
    CompiledNode,
    FailedRecord,
    NodeDraft,
    ValidationIssue,
)


@dataclass(frozen=True)
class NodeResolutionResult:
    nodes: list[CompiledNode] = field(default_factory=list)
    draft_by_ref: dict[str, NodeDraft] = field(default_factory=dict)
    node_id_by_ref: dict[str, str] = field(default_factory=dict)
    failed_records: list[FailedRecord] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)


class EntityResolver:
    def resolve(
        self,
        *,
        adapter_spec: AdapterSpec,
        version: str,
        drafts: list[NodeDraft],
    ) -> NodeResolutionResult:
        nodes_by_id: dict[str, CompiledNode] = {}
        draft_by_ref: dict[str, NodeDraft] = {}
        node_id_by_ref: dict[str, str] = {}
        failed_records: list[FailedRecord] = []
        warnings: list[ValidationIssue] = []
        stable_key_counts = _stable_key_counts(drafts)

        for draft in drafts:
            validation = validate_node_against_adapter(adapter_spec, draft)
            warnings.extend(validation.issues if validation.ok else [])
            if not validation.ok:
                failed_records.append(
                    FailedRecord(
                        source_type="node",
                        source_id=draft.stable_key,
                        reason="node validation failed",
                        details={"issues": [issue.model_dump() for issue in validation.issues]},
                    )
                )
                continue

            node_id = make_node_id(adapter_spec.name, draft.node_type, draft.stable_key)
            existing = nodes_by_id.get(node_id)
            if existing is None:
                nodes_by_id[node_id] = CompiledNode(
                    node_id=node_id,
                    adapter_name=adapter_spec.name,
                    node_type=draft.node_type,
                    canonical_name=draft.canonical_name,
                    aliases=draft.aliases,
                    external_ids=draft.external_ids,
                    properties=draft.properties,
                    status=draft.status,
                    version=version,
                )

            typed_ref = f"{draft.node_type}:{draft.stable_key}"
            draft_by_ref[typed_ref] = draft
            node_id_by_ref[typed_ref] = node_id
            if stable_key_counts[draft.stable_key] == 1:
                draft_by_ref[draft.stable_key] = draft
                node_id_by_ref[draft.stable_key] = node_id

        return NodeResolutionResult(
            nodes=list(nodes_by_id.values()),
            draft_by_ref=draft_by_ref,
            node_id_by_ref=node_id_by_ref,
            failed_records=failed_records,
            warnings=warnings,
        )


def _stable_key_counts(drafts: list[NodeDraft]) -> dict[str, int]:
    types_by_key: dict[str, set[str]] = {}
    for draft in drafts:
        types_by_key.setdefault(draft.stable_key, set()).add(draft.node_type)
    return {stable_key: len(node_types) for stable_key, node_types in types_by_key.items()}
