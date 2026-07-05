"""LLM-led maintenance for Cognitive Community topics."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.application.services.cognitive_index_service import (
    AssignmentCandidateOrderStore,
    _assignment_candidate_from_graph_community,
)
from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.cognitive_index import (
    COMMUNITY_PROJECTION,
    CommunityDraft,
    _absorb_existing_communities,
    _drafts_from_existing,
    _graph_community_from_draft,
)
from src.domain.knowledge.graph_index import GraphIndexCommunity, GraphIndexVectorDocument
from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.domain.knowledge.semantic_index_materials import SEMANTIC_COLLECTION_COMMUNITY, SemanticVectorDocument
from src.domain.knowledge.retrieval_profile import profile_span
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.vector_store.semantic_hybrid_retriever import SemanticHybridRetriever


COMMUNITY_MAINTENANCE_SYSTEM_PROMPT = """你是金融知识图谱的 Community Topic 维护裁决器。

你会收到当前 active L0 community 的压缩目录。你的任务是发现是否存在应该合并的重复或父子主题。

只允许基于输入目录判断，不添加外部事实。

合并原则：
- 如果多个 L0 实际表达同一稳定对象、风险来源、产业链、政策机制、市场机制或经营机制，应合并。
- 如果一个 L0 是另一个更宽 L0 的子方向、阶段、动作、局部风险或局部影响，应合并到更宽 L0。
- 如果现有多个窄 L0 都属于同一个更稳定父主题，且没有合适的现有父主题，可以创建新父主题并吸收这些窄 L0。
- 不要因为共享过粗上位类就合并；底层对象、机制或风险来源不同，应保留独立主题。
- 合并后的父主题不能成为泛化桶；必须有明确覆盖对象、稳定驱动机制、可复用边界和排除边界。
- 不要把所有公司动作、所有政策、所有行情表现、所有市场情绪合成一个大桶。
- 宽泛 seed 不是垃圾桶。不能只因为一个主题也属于“政策、经营基本面、行业景气、风险偏好”等上位维度，就并入 seed。
- 如果某个 community 已经有清晰行业、资产、技术链条、区域风险或商品对象，并且未来可独立承接多条资料，应保留为独立 L0；它可以在 assignment 阶段低权重关联 seed，但不应在维护阶段被 seed 吸收。
- 只有当被吸收主题本身缺少稳定对象边界，或只是目标 community 下的局部动作/阶段/表达时，才合并到更宽主题。
- seed community 可以作为合并目标；不要把 seed community 吸收到新主题里，除非输入中明确显示 seed 边界本身错误。

输出要求：
- 顶层只能包含 merge_groups 和 skipped_groups。
- merge_groups 只输出高置信、低争议的合并。
- 如果没有需要合并的，merge_groups 输出空数组。
- 输出必须符合 JSON Schema，不要 Markdown。"""


COMMUNITY_MAINTENANCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "merge_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "target_mode": {"type": "string", "enum": ["existing", "new_parent"]},
                    "target_community_id": {"type": "string"},
                    "new_title": {"type": "string"},
                    "new_scope": {"type": "string"},
                    "absorb_community_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "reason": {"type": "string"},
                },
                "required": [
                    "target_mode",
                    "target_community_id",
                    "new_title",
                    "new_scope",
                    "absorb_community_ids",
                    "confidence",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
        "skipped_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "community_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["community_ids", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["merge_groups", "skipped_groups"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class CommunityMaintenanceCommand:
    adapter_name: str = "financial"
    target: str = "prod"
    limit: int = 120
    min_confidence: float = 0.78
    dry_run: bool = True


@dataclass
class CommunityMaintenanceResult:
    adapter_name: str
    target: str
    dry_run: bool
    inspected_communities: int
    merge_groups: list[dict[str, Any]] = field(default_factory=list)
    skipped_groups: list[dict[str, Any]] = field(default_factory=list)
    applied_groups: list[dict[str, Any]] = field(default_factory=list)
    rejected_groups: list[dict[str, Any]] = field(default_factory=list)
    pg_results: list[dict[str, Any]] = field(default_factory=list)
    milvus_deleted: int = 0
    milvus_upserted: int = 0
    ledger_redirects: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "adapter_name": self.adapter_name,
            "target": self.target,
            "dry_run": self.dry_run,
            "inspected_communities": self.inspected_communities,
            "merge_groups": self.merge_groups,
            "skipped_groups": self.skipped_groups,
            "applied_groups": self.applied_groups,
            "rejected_groups": self.rejected_groups,
            "pg_results": self.pg_results,
            "milvus_deleted": self.milvus_deleted,
            "milvus_upserted": self.milvus_upserted,
            "ledger_redirects": self.ledger_redirects,
        }


class CommunityMaintenanceService:
    """Review active L0 communities and apply LLM-approved merges."""

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        semantic_retriever: SemanticHybridRetriever,
        candidate_order_store: AssignmentCandidateOrderStore | None = None,
        model: str | None = None,
    ) -> None:
        self._repository = repository
        self._semantic_retriever = semantic_retriever
        self._candidate_order_store = candidate_order_store
        self._model = model or resolve_kg_llm_model("kg_community_assignment")
        self._llm = get_llm_gateway_service()

    async def review_and_apply(self, command: CommunityMaintenanceCommand) -> CommunityMaintenanceResult:
        communities = [
            item
            for item in self._repository.list_graph_communities(command.adapter_name)
            if item.projection == COMMUNITY_PROJECTION and item.status == "active"
        ]
        communities = sorted(communities, key=_community_review_sort_key)[: max(1, command.limit)]
        result = CommunityMaintenanceResult(
            adapter_name=command.adapter_name,
            target=command.target,
            dry_run=command.dry_run,
            inspected_communities=len(communities),
        )
        if not communities:
            return result

        decision = await self._decide(command=command, communities=communities)
        result.merge_groups = decision.get("merge_groups") or []
        result.skipped_groups = decision.get("skipped_groups") or []
        valid_groups, rejected_groups = _validated_merge_groups(
            result.merge_groups,
            communities=communities,
            min_confidence=command.min_confidence,
        )
        result.rejected_groups.extend(rejected_groups)
        if command.dry_run or not valid_groups:
            return result

        drafts = _drafts_from_existing(communities)
        all_by_id = {item.community_id: item for item in communities}
        consumed_absorb_ids: set[str] = set()
        for group in valid_groups:
            absorb_ids = [
                community_id
                for community_id in group["absorb_community_ids"]
                if community_id in drafts and community_id not in consumed_absorb_ids
            ]
            if not absorb_ids:
                result.rejected_groups.append({**group, "reject_reason": "all_absorb_ids_already_consumed"})
                continue
            target_draft = self._target_draft(
                command=command,
                group=group,
                drafts=drafts,
                all_by_id=all_by_id,
            )
            if target_draft.community_id in absorb_ids:
                absorb_ids = [item for item in absorb_ids if item != target_draft.community_id]
            if not absorb_ids:
                result.rejected_groups.append({**group, "reject_reason": "target_equals_all_absorb_ids"})
                continue
            _absorb_existing_communities(parent=target_draft, absorbed_ids=absorb_ids, communities=drafts)
            consumed_absorb_ids.update(absorb_ids)
            updated = _graph_community_from_draft(command.adapter_name, target_draft)
            pg_result = self._repository.replace_graph_index_scope(
                command.adapter_name,
                remove_community_ids=absorb_ids,
                communities=[updated],
                findings=[],
                deltas=[],
                unassigned_signals=[],
            )
            migrated = self._repository.migrate_community_assignments(
                command.adapter_name,
                community_id_map={old_id: updated.community_id for old_id in absorb_ids},
            )
            pg_result["migrated_assignments"] = migrated
            result.pg_results.append(pg_result)
            stale_target_ids = [str(item) for item in pg_result.get("stale_target_ids") or [] if str(item)]
            if stale_target_ids:
                result.milvus_deleted += await self._semantic_retriever.delete_documents_by_role(
                    collection_role=SEMANTIC_COLLECTION_COMMUNITY,
                    adapter_name=command.adapter_name,
                    target=command.target,
                    target_ids=stale_target_ids,
                )
            result.milvus_upserted += await self._semantic_retriever.upsert_semantic_documents(
                adapter_name=command.adapter_name,
                target=command.target,
                documents=[_semantic_document_from_graph_index_document(_community_vector_document(updated))],
                kg_version=updated.version_id,
            )
            if self._candidate_order_store is not None:
                redirect_result = self._candidate_order_store.record_community_redirects(
                    adapter_name=command.adapter_name,
                    redirects=[
                        {"from_community_id": old_id, "to_community_id": updated.community_id}
                        for old_id in absorb_ids
                    ],
                    target_candidates=[_assignment_candidate_from_graph_community(updated)],
                )
                result.ledger_redirects.append(redirect_result)
            result.applied_groups.append({**group, "target_community_id": updated.community_id, "absorbed": absorb_ids})
        return result

    async def _decide(
        self,
        *,
        command: CommunityMaintenanceCommand,
        communities: list[GraphIndexCommunity],
    ) -> dict[str, Any]:
        prompt = {
            "communities": [_community_review_payload(item) for item in communities],
            "max_merge_groups": 12,
            "min_confidence": command.min_confidence,
        }
        request = LLMProxyRequest(
            model=self._model,
            system_prompt=COMMUNITY_MAINTENANCE_SYSTEM_PROMPT,
            prompt=json.dumps(prompt, ensure_ascii=False, separators=(",", ":")),
            temperature=0,
            max_tokens=4000,
            json_schema=COMMUNITY_MAINTENANCE_SCHEMA,
            metadata={
                "task": "kg_community_maintenance",
                "adapter_name": command.adapter_name,
                "target": command.target,
                "communities": len(communities),
                "dry_run": command.dry_run,
            },
            use_cache=False,
        )
        with profile_span("kg_community_maintenance.llm", communities=len(communities), dry_run=command.dry_run):
            response = await self._llm.generate(request)
        decision = response.structured_output
        if not isinstance(decision, dict):
            raise RuntimeError("community maintenance output is not object")
        if not isinstance(decision.get("merge_groups"), list) or not isinstance(decision.get("skipped_groups"), list):
            raise RuntimeError("community maintenance output missing merge_groups/skipped_groups")
        return decision

    def _target_draft(
        self,
        *,
        command: CommunityMaintenanceCommand,
        group: dict[str, Any],
        drafts: dict[str, CommunityDraft],
        all_by_id: dict[str, GraphIndexCommunity],
    ) -> CommunityDraft:
        if group["target_mode"] == "existing":
            target_id = str(group["target_community_id"])
            return drafts[target_id]
        title = str(group["new_title"]).strip()
        scope = str(group["new_scope"]).strip()
        community_id = self._repository.allocate_graph_community_id(command.adapter_name, level=0)
        draft = CommunityDraft(
            community_id=community_id,
            title=title,
            scope=scope,
            level=0,
            summary=scope,
            future_coverage=[title],
        )
        drafts[community_id] = draft
        all_by_id[community_id] = _graph_community_from_draft(command.adapter_name, draft)
        return draft


def _validated_merge_groups(
    groups: list[dict[str, Any]],
    *,
    communities: list[GraphIndexCommunity],
    min_confidence: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    community_ids = {item.community_id for item in communities}
    seed_ids = {
        item.community_id
        for item in communities
        if str((item.metrics or {}).get("origin") or "") == "seed"
    }
    valid: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            rejected.append({"group": group, "reject_reason": "group_not_object"})
            continue
        confidence = float(group.get("confidence") or 0.0)
        target_mode = str(group.get("target_mode") or "")
        target_id = str(group.get("target_community_id") or "")
        absorb_ids = _ordered_unique(str(item).strip() for item in group.get("absorb_community_ids") or [] if str(item).strip())
        if confidence < min_confidence:
            rejected.append({**group, "reject_reason": "confidence_below_threshold"})
            continue
        if target_mode not in {"existing", "new_parent"}:
            rejected.append({**group, "reject_reason": "invalid_target_mode"})
            continue
        if target_mode == "existing" and target_id not in community_ids:
            rejected.append({**group, "reject_reason": "target_not_found"})
            continue
        if target_mode == "new_parent" and not str(group.get("new_title") or "").strip():
            rejected.append({**group, "reject_reason": "new_parent_missing_title"})
            continue
        invalid_absorb = [item for item in absorb_ids if item not in community_ids]
        if invalid_absorb:
            rejected.append({**group, "reject_reason": "absorb_not_found", "invalid_absorb_ids": invalid_absorb})
            continue
        if target_mode == "new_parent" and any(item in seed_ids for item in absorb_ids):
            rejected.append({**group, "reject_reason": "new_parent_cannot_absorb_seed"})
            continue
        if target_mode == "existing":
            absorb_ids = [item for item in absorb_ids if item != target_id]
        if not absorb_ids:
            rejected.append({**group, "reject_reason": "empty_absorb_ids"})
            continue
        valid.append({**group, "absorb_community_ids": absorb_ids, "confidence": confidence})
    return valid, rejected


def _community_review_payload(community: GraphIndexCommunity) -> dict[str, Any]:
    metrics = community.metrics or {}
    return {
        "community_id": community.community_id,
        "title": community.title,
        "origin": str(metrics.get("origin") or ""),
        "scope": str(metrics.get("scope") or community.summary or "")[:420],
        "summary": community.summary[:420],
        "source_count": int(metrics.get("source_count") or len(community.evidence_ids or [])),
        "assigned_intent_count": int(metrics.get("assigned_intent_count") or 0),
        "canonical_labels": _list(metrics.get("canonical_labels"))[:12],
        "parent_themes": _list(metrics.get("parent_themes"))[:10],
        "topic_tags": _list(metrics.get("topic_tags"))[:14],
        "risk_tags": _list(metrics.get("risk_tags"))[:8],
        "impact_tags": _list(metrics.get("impact_tags"))[:10],
        "event_threads": _list(metrics.get("event_threads"))[:8],
    }


def _community_review_sort_key(community: GraphIndexCommunity) -> tuple[int, int, str]:
    metrics = community.metrics or {}
    origin = str(metrics.get("origin") or "")
    source_count = int(metrics.get("source_count") or 0)
    return (0 if origin == "emergent" else 1, source_count, community.community_id)


def _community_vector_document(community: GraphIndexCommunity) -> GraphIndexVectorDocument:
    metrics = community.metrics or {}
    text = "\n".join(
        part
        for part in [
            "Document Type: Community Report",
            f"Community: {community.title}",
            f"Projection: {community.projection}",
            f"Community Level: {community.level}",
            f"Origin: {metrics.get('origin') or ''}",
            f"Maturity: {metrics.get('maturity_level') or ''}",
            f"Directory Scope: {metrics.get('scope') or ''}",
            f"Include Rules: {'；'.join(metrics.get('include_rules') or [])}",
            f"Exclude Rules: {'；'.join(metrics.get('exclude_rules') or [])}",
            f"Granularity Note: {metrics.get('granularity_note') or ''}",
            f"Coverage Contract: {metrics.get('coverage_contract') or ''}",
            f"Canonical Labels: {'；'.join(metrics.get('canonical_labels') or [])}",
            f"Assigned Intent Count: {metrics.get('assigned_intent_count') or 0}",
            f"Source Count: {metrics.get('source_count') or 0}",
            f"Summary: {community.summary}",
            f"Parent Themes: {'；'.join(metrics.get('parent_themes') or [])}",
            f"Topic Tags: {'；'.join(metrics.get('topic_tags') or [])}",
            f"Future Coverage: {'；'.join(metrics.get('future_coverage') or [])}",
            f"Impact Tags: {'；'.join(metrics.get('impact_tags') or [])}",
            f"Risk Tags: {'；'.join(metrics.get('risk_tags') or [])}",
            f"Event Threads: {'；'.join(metrics.get('event_threads') or [])}",
            f"Expandable Handles: community_id={community.community_id}",
        ]
        if part and not part.endswith(": ")
    )
    return GraphIndexVectorDocument(
        document_id=community.community_id,
        document_type="community_report",
        collection_role=SEMANTIC_COLLECTION_COMMUNITY,
        source_type="kg_community_report",
        source_id=community.community_id,
        evidence_id=community.evidence_ids[0] if community.evidence_ids else "",
        text=text,
        metadata={
            "community_id": community.community_id,
            "community_version_id": community.version_id,
            "community_title": community.title,
            "community_level": community.level,
            "projection": community.projection,
            "parent_community_id": community.parent_community_id,
            "cited_evidence_ids": community.evidence_ids,
            "cited_chunk_ids": community.chunk_ids,
            "metrics": metrics,
            "maturity_level": metrics.get("maturity_level") or "",
            "cognitive_card_ids": metrics.get("cognitive_card_ids") or [],
            "earliest_source_published_at": metrics.get("earliest_source_published_at") or "",
            "latest_source_published_at": metrics.get("latest_source_published_at") or "",
            "event_time_start": metrics.get("earliest_source_published_at") or "",
            "event_time_end": metrics.get("latest_source_published_at") or "",
        },
    )


def _semantic_document_from_graph_index_document(document: GraphIndexVectorDocument) -> SemanticVectorDocument:
    return SemanticVectorDocument(
        document_id=document.document_id,
        document_type=document.document_type,
        collection_role=document.collection_role,
        source_type=document.source_type,
        source_id=document.source_id,
        evidence_id=document.evidence_id,
        text=document.text,
        metadata=document.metadata,
    )


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value is None:
        return []
    return [str(value)] if str(value).strip() else []


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
