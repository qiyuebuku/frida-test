"""Small adapter used to prove the generic adapter contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.domain.knowledge.adapter import (
    AdapterSpec,
    EntityTypeSpec,
    RelationTypeSpec,
    SourceTypeSpec,
    WikiPageSpec,
)
from src.domain.knowledge.enums import ConfidenceLabel, EdgeStatus, EvidenceType, InputType
from src.domain.knowledge.schemas import EdgeDraft, EvidenceDraft, KnowledgeInput, NodeDraft


class ToyProjectAdapter:
    spec = AdapterSpec(
        name="toy",
        version="v1",
        entities=[
            EntityTypeSpec(name="person", stable_id_fields=["name"]),
            EntityTypeSpec(name="project", stable_id_fields=["name"]),
            EntityTypeSpec(name="document", stable_id_fields=["title"]),
        ],
        relations=[
            RelationTypeSpec(
                name="owns",
                source_types=["person"],
                target_types=["project"],
                allow_inferred=False,
                requires_evidence=True,
                allowed_confidence_labels=[ConfidenceLabel.EXTRACTED],
                allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
            ),
            RelationTypeSpec(
                name="references",
                source_types=["document"],
                target_types=["project"],
                allow_inferred=False,
                requires_evidence=True,
                allowed_confidence_labels=[ConfidenceLabel.EXTRACTED],
                allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
            ),
            RelationTypeSpec(
                name="blocks",
                source_types=["project"],
                target_types=["project"],
                allow_inferred=False,
                requires_evidence=True,
                allowed_confidence_labels=[ConfidenceLabel.EXTRACTED],
                allowed_statuses=[EdgeStatus.ACTIVE, EdgeStatus.CANDIDATE],
            ),
        ],
        sources=[
            SourceTypeSpec(
                name="toy_record",
                input_type=InputType.STRUCTURED_RECORD,
                required_fields=["owner", "project", "document"],
            )
        ],
        wiki_pages=[WikiPageSpec(page_type="project_page", subject_types=["project"])],
    )

    def normalize(self, raw: Any) -> list[KnowledgeInput]:
        records = raw if isinstance(raw, list) else [raw]
        return [self._normalize_one(record) for record in records]

    async def extract_node_drafts(self, item: KnowledgeInput) -> list[NodeDraft]:
        payload = item.payload
        nodes = [
            NodeDraft(
                node_type="person",
                stable_key=str(payload["owner"]),
                canonical_name=str(payload["owner"]),
                source_refs=[item.source_id],
            ),
            NodeDraft(
                node_type="project",
                stable_key=str(payload["project"]),
                canonical_name=str(payload["project"]),
                source_refs=[item.source_id],
            ),
            NodeDraft(
                node_type="document",
                stable_key=str(payload["document"]),
                canonical_name=str(payload["document"]),
                source_refs=[item.source_id],
            ),
        ]
        for blocked in payload.get("blocks", []):
            nodes.append(
                NodeDraft(
                    node_type="project",
                    stable_key=str(blocked),
                    canonical_name=str(blocked),
                    source_refs=[item.source_id],
                )
            )
        return _dedupe_nodes(nodes)

    async def extract_edge_drafts(self, item: KnowledgeInput, nodes: list[NodeDraft]) -> list[EdgeDraft]:
        payload = item.payload
        evidence_ref = f"{item.source_type}:{item.source_id}"
        edges = [
            EdgeDraft(
                source_ref=str(payload["owner"]),
                target_ref=str(payload["project"]),
                relation_type="owns",
                confidence_label=ConfidenceLabel.EXTRACTED,
                confidence_score=1.0,
                status=EdgeStatus.ACTIVE,
                evidence_refs=[evidence_ref],
            ),
            EdgeDraft(
                source_ref=str(payload["document"]),
                target_ref=str(payload["project"]),
                relation_type="references",
                confidence_label=ConfidenceLabel.EXTRACTED,
                confidence_score=1.0,
                status=EdgeStatus.ACTIVE,
                evidence_refs=[evidence_ref],
            ),
        ]
        for blocked in payload.get("blocks", []):
            edges.append(
                EdgeDraft(
                    source_ref=str(payload["project"]),
                    target_ref=str(blocked),
                    relation_type="blocks",
                    confidence_label=ConfidenceLabel.EXTRACTED,
                    confidence_score=1.0,
                    status=EdgeStatus.ACTIVE,
                    evidence_refs=[evidence_ref],
                )
            )
        return edges

    def extract_evidence_drafts(self, item: KnowledgeInput) -> list[EvidenceDraft]:
        return [
            EvidenceDraft(
                evidence_type=EvidenceType.TEXT_SPAN if item.raw_text else EvidenceType.STRUCTURED_FIELD,
                source_type=item.source_type,
                source_id=item.source_id,
                content=item.raw_text,
                payload=item.payload,
            )
        ]

    def _normalize_one(self, record: dict[str, Any]) -> KnowledgeInput:
        payload = dict(record.get("payload") or {})
        raw_text = record.get("raw_text")
        return KnowledgeInput(
            input_type=InputType.STRUCTURED_RECORD,
            source_type=record.get("source_type", "toy_record"),
            source_id=record["source_id"],
            observed_at=_parse_datetime(record["observed_at"]),
            adapter_name=self.spec.name,
            adapter_version=self.spec.version,
            payload=payload,
            raw_text=raw_text,
        )


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _dedupe_nodes(nodes: list[NodeDraft]) -> list[NodeDraft]:
    seen: set[tuple[str, str]] = set()
    result: list[NodeDraft] = []
    for node in nodes:
        key = (node.node_type, node.stable_key)
        if key in seen:
            continue
        seen.add(key)
        result.append(node)
    return result
