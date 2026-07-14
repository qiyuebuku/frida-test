"""Repository contract for generic knowledge persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.domain.knowledge.graph_index import (
    GraphIndexCommunity,
    GraphIndexDelta,
    GraphIndexFinding,
    GraphIndexUnassignedSignal,
)
from src.domain.knowledge.atomic_cognitive_card import AtomicCognitiveCard, CognitiveCardManifest
from src.domain.knowledge.cognitive_index import CommunityAssignment
from src.domain.knowledge.quality import ReviewAction, ReviewEntry
from src.domain.knowledge.retrieval_eval import (
    RetrievalEvalMetric,
    RetrievalEvalRun,
    RetrievalLabel,
    RetrievalTraceSnapshot,
)
from src.domain.knowledge.schemas import CompiledEvidence, EvidenceChunk


class KnowledgeRepository(ABC):
    @abstractmethod
    def ping(self) -> None:
        """Verify repository connectivity with a lightweight query."""

    @abstractmethod
    def upsert_evidence(self, evidence: list[CompiledEvidence]) -> int:
        """Insert or update evidence records and return affected row count."""

    @abstractmethod
    def get_evidence(self, evidence_id: str) -> CompiledEvidence | None:
        """Load one compiled evidence record by ID."""

    @abstractmethod
    def list_evidence(self, adapter_name: str, *, include_inactive: bool = False) -> list[CompiledEvidence]:
        """Load evidence records for one adapter."""

    @abstractmethod
    def cleanup_evidence_versions(self, adapter_name: str) -> dict[str, Any]:
        """Mark stale same-source evidence versions inactive/superseded."""

    @abstractmethod
    def rebuild_evidence_chunks(self, adapter_name: str) -> int:
        """Replace generated evidence chunks for one adapter."""

    @abstractmethod
    def upsert_evidence_chunks(self, evidence: list[CompiledEvidence]) -> int:
        """Insert or update generated evidence chunks for changed evidence."""

    @abstractmethod
    def list_evidence_chunks(self, adapter_name: str) -> list[EvidenceChunk]:
        """Load generated evidence chunks for one adapter."""

    def list_evidence_chunks_by_refs(
        self,
        adapter_name: str,
        *,
        chunk_ids: list[str],
        evidence_ids: list[str],
    ) -> list[EvidenceChunk]:
        """Load generated evidence chunks matching chunk IDs or evidence IDs."""
        return [
            chunk
            for chunk in self.list_evidence_chunks(adapter_name)
            if chunk.chunk_id in set(chunk_ids) or chunk.evidence_id in set(evidence_ids)
        ]

    @abstractmethod
    def replace_atomic_cognitive_cards_for_evidence(
        self,
        adapter_name: str,
        *,
        evidence_ids: list[str],
        cards: list[AtomicCognitiveCard],
    ) -> dict[str, Any]:
        """按 Evidence 替换原子 Card manifest，并返回新旧 ID 差异。"""

    @abstractmethod
    def list_atomic_cognitive_card_ids_for_inactive_evidence(
        self,
        adapter_name: str,
    ) -> list[str]:
        """列出 Evidence 已失效但 Card manifest 尚未清理的 Card ID。"""

    @abstractmethod
    def delete_atomic_cognitive_cards_by_ids(
        self,
        adapter_name: str,
        *,
        cognitive_card_ids: list[str],
    ) -> int:
        """按稳定 ID 批量删除已完成外部清理的 Card manifest。"""

    @abstractmethod
    def list_atomic_cognitive_card_manifests(
        self,
        adapter_name: str,
        *,
        status: str = "active",
    ) -> list[CognitiveCardManifest]:
        """读取原子 Card manifest；可读 Card 内容由 Milvus 提供。"""

    def list_atomic_cognitive_card_manifests_by_ids(
        self,
        adapter_name: str,
        *,
        cognitive_card_ids: list[str],
        status: str = "active",
    ) -> list[CognitiveCardManifest]:
        """按 ID 读取原子 Card manifest。"""
        ids = set(cognitive_card_ids)
        return [
            card
            for card in self.list_atomic_cognitive_card_manifests(adapter_name, status=status)
            if card.cognitive_card_id in ids
        ]

    def list_atomic_cognitive_card_manifests_by_chunk_refs(
        self,
        adapter_name: str,
        *,
        chunk_ids: list[str],
        evidence_ids: list[str],
        status: str = "active",
    ) -> list[CognitiveCardManifest]:
        """按 Chunk 或 Evidence 指针读取原子 Card manifest。"""
        chunk_set = set(chunk_ids)
        evidence_set = set(evidence_ids)
        return [
            card
            for card in self.list_atomic_cognitive_card_manifests(adapter_name, status=status)
            if card.evidence_id in evidence_set
            or card.primary_chunk_id in chunk_set
            or bool(set(card.chunk_ids).intersection(chunk_set))
        ]

    @abstractmethod
    def replace_community_assignments_for_cards(
        self,
        adapter_name: str,
        *,
        cognitive_card_ids: list[str],
        assignments: list[CommunityAssignment],
    ) -> int:
        """Replace Community Assignment decisions for Cognitive Cards."""

    @abstractmethod
    def migrate_community_assignments(
        self,
        adapter_name: str,
        *,
        community_id_map: dict[str, str],
    ) -> int:
        """Rewrite Community Assignment community_id refs after community absorption."""

    @abstractmethod
    def count_graph_index_materials(self, adapter_name: str) -> dict[str, int]:
        """Count chunk/card materials used by high-level index planning."""

    @abstractmethod
    def list_graph_index_materials(
        self,
        adapter_name: str,
        *,
        node_ids: list[str],
        edge_ids: list[str],
        evidence_ids: list[str],
        chunk_ids: list[str],
    ) -> dict[str, list[Any]]:
        """Load scoped chunks for high-level index local rebuild."""

    @abstractmethod
    def list_graph_communities(self, adapter_name: str) -> list[GraphIndexCommunity]:
        """Load current Graph Index communities for one adapter."""

    def list_graph_communities_by_ids(
        self,
        adapter_name: str,
        *,
        community_ids: list[str],
    ) -> list[GraphIndexCommunity]:
        """Load scoped Graph Index communities by ID."""
        ids = set(community_ids)
        return [community for community in self.list_graph_communities(adapter_name) if community.community_id in ids]

    def list_graph_communities_by_card_ids(
        self,
        adapter_name: str,
        *,
        cognitive_card_ids: list[str],
    ) -> list[GraphIndexCommunity]:
        """Load scoped Graph Index communities containing any Cognitive Card refs."""
        ids = set(cognitive_card_ids)
        return [
            community
            for community in self.list_graph_communities(adapter_name)
            if ids.intersection(set((community.metrics or {}).get("cognitive_card_ids") or []))
        ]

    @abstractmethod
    def allocate_graph_community_id(self, adapter_name: str, *, level: int = 0) -> str:
        """Allocate a monotonic community id for a new Graph Index community."""

    @abstractmethod
    def list_graph_findings(self, adapter_name: str) -> list[GraphIndexFinding]:
        """Load current Graph Index findings for one adapter."""

    @abstractmethod
    def list_graph_deltas(self, adapter_name: str) -> list[GraphIndexDelta]:
        """Load current Graph Index rolling deltas for one adapter."""

    @abstractmethod
    def list_graph_unassigned_signals(
        self,
        adapter_name: str,
        *,
        status: str = "active",
    ) -> list[GraphIndexUnassignedSignal]:
        """Load weak graph signals that are not yet publishable communities."""

    @abstractmethod
    def mark_graph_index_dirty(self, adapter_name: str, *, reason: str) -> int:
        """Mark current Graph Index state as needing rebuild after a failed refresh."""

    @abstractmethod
    def replace_graph_index(
        self,
        adapter_name: str,
        *,
        communities: list[GraphIndexCommunity],
        findings: list[GraphIndexFinding],
        deltas: list[GraphIndexDelta] | None = None,
        unassigned_signals: list[GraphIndexUnassignedSignal] | None = None,
    ) -> dict[str, Any]:
        """Replace generated graph-index communities/findings/deltas for one adapter."""

    @abstractmethod
    def replace_graph_index_scope(
        self,
        adapter_name: str,
        *,
        remove_community_ids: list[str],
        communities: list[GraphIndexCommunity],
        findings: list[GraphIndexFinding],
        deltas: list[GraphIndexDelta] | None = None,
        unassigned_signals: list[GraphIndexUnassignedSignal] | None = None,
        promoted_signals: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Replace one dirty Graph Index scope for one adapter."""

    @abstractmethod
    def save_retrieval_trace_snapshot(self, snapshot: RetrievalTraceSnapshot) -> str:
        """Persist a replayable retrieval quality snapshot and return its ID."""

    @abstractmethod
    def list_retrieval_trace_snapshots(
        self,
        *,
        adapter_name: str | None = None,
        target: str | None = None,
        query_hash: str | None = None,
        limit: int = 50,
    ) -> list[RetrievalTraceSnapshot]:
        """Load retrieval quality snapshots for replay or inspection."""

    @abstractmethod
    def save_retrieval_label(self, label: RetrievalLabel) -> str:
        """Persist a human retrieval label and return its ID."""

    @abstractmethod
    def list_retrieval_labels(
        self,
        *,
        snapshot_id: str | None = None,
        case_id: str | None = None,
        limit: int = 100,
    ) -> list[RetrievalLabel]:
        """Load human retrieval labels."""

    @abstractmethod
    def save_retrieval_eval_run(self, run: RetrievalEvalRun) -> str:
        """Create or update a retrieval evaluation run and return its ID."""

    @abstractmethod
    def finish_retrieval_eval_run(
        self,
        run_id: str,
        *,
        status: str,
        aggregate_metrics: dict[str, Any],
    ) -> None:
        """Finish a retrieval evaluation run."""

    @abstractmethod
    def upsert_retrieval_eval_metrics(self, metrics: list[RetrievalEvalMetric]) -> int:
        """Insert or update case-level retrieval evaluation metrics."""

    @abstractmethod
    def list_retrieval_eval_metrics(
        self,
        run_id: str,
        *,
        case_id: str | None = None,
    ) -> list[RetrievalEvalMetric]:
        """Load case-level retrieval evaluation metrics for one run."""

    @abstractmethod
    def upsert_review_entries(self, entries: list[ReviewEntry]) -> int:
        """Insert or update review queue entries."""

    @abstractmethod
    def list_review_entries(self, status: str | None = None) -> list[ReviewEntry]:
        """Load review queue entries."""

    @abstractmethod
    def apply_review_action(self, review_id: str, action: ReviewAction) -> None:
        """Record a review action."""

    @abstractmethod
    def create_compilation_run(self, run: dict[str, Any]) -> str:
        """Create a compile run record and return its ID."""

    @abstractmethod
    def finish_compilation_run(self, run_id: str, result: dict[str, Any]) -> None:
        """Mark a compile run as finished."""

    @abstractmethod
    def get_compilation_run(self, run_id: str) -> dict[str, Any] | None:
        """Load one compile/task run record."""

    @abstractmethod
    def list_compilation_runs(
        self,
        *,
        adapter_name: str | None = None,
        source_batch_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Load compile/task run records for recovery or inspection."""
