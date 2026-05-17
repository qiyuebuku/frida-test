"""Repository contract for generic knowledge persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.domain.knowledge.quality import ReviewAction, ReviewEntry
from src.domain.knowledge.retrieval_document import RetrievalDocument, RetrievalDocumentVersion
from src.domain.knowledge.retrieval_eval import (
    RetrievalEvalMetric,
    RetrievalEvalRun,
    RetrievalLabel,
    RetrievalTraceSnapshot,
)
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode, EvidenceChunk
from src.domain.knowledge.wiki import WikiPage


class KnowledgeRepository(ABC):
    @abstractmethod
    def ping(self) -> None:
        """Verify repository connectivity with a lightweight query."""

    @abstractmethod
    def upsert_nodes(self, nodes: list[CompiledNode]) -> int:
        """Insert or update nodes and return affected row count."""

    @abstractmethod
    def upsert_edges(self, edges: list[CompiledEdge]) -> int:
        """Insert or update edges and return affected row count."""

    @abstractmethod
    def upsert_evidence(self, evidence: list[CompiledEvidence]) -> int:
        """Insert or update evidence records and return affected row count."""

    @abstractmethod
    def attach_edge_evidence(self, edge_id: str, evidence_ids: list[str]) -> int:
        """Attach evidence records to an edge and return affected row count."""

    @abstractmethod
    def get_node(self, node_id: str) -> CompiledNode | None:
        """Load one compiled node by ID."""

    @abstractmethod
    def get_edge(self, edge_id: str) -> CompiledEdge | None:
        """Load one compiled edge by ID."""

    @abstractmethod
    def get_evidence(self, evidence_id: str) -> CompiledEvidence | None:
        """Load one compiled evidence record by ID."""

    @abstractmethod
    def get_edge_evidence(self, edge_id: str) -> list[CompiledEvidence]:
        """Load evidence records attached to an edge."""

    @abstractmethod
    def list_nodes(self, adapter_name: str) -> list[CompiledNode]:
        """Load nodes for one adapter."""

    @abstractmethod
    def list_edges(self, adapter_name: str) -> list[CompiledEdge]:
        """Load edges for one adapter."""

    @abstractmethod
    def list_evidence(self, adapter_name: str, *, include_inactive: bool = False) -> list[CompiledEvidence]:
        """Load evidence records for one adapter."""

    @abstractmethod
    def cleanup_evidence_versions(self, adapter_name: str) -> dict[str, Any]:
        """Mark stale same-source evidence versions inactive/superseded."""

    @abstractmethod
    def rebuild_wiki_pages(self, adapter_name: str, pages: list[WikiPage]) -> int:
        """Replace generated wiki pages for one adapter."""

    @abstractmethod
    def upsert_wiki_pages(self, adapter_name: str, pages: list[WikiPage]) -> int:
        """Insert or update generated wiki pages for one adapter."""

    @abstractmethod
    def list_wiki_pages(self, adapter_name: str) -> list[WikiPage]:
        """Load generated wiki pages for one adapter."""

    @abstractmethod
    def search_wiki_pages(self, adapter_name: str, query: str, limit: int = 20) -> list[WikiPage]:
        """Search generated wiki pages."""

    @abstractmethod
    def rebuild_graph_adjacency(self, adapter_name: str) -> int:
        """Replace generated adjacency records for one adapter."""

    @abstractmethod
    def upsert_graph_adjacency(self, edges: list[CompiledEdge]) -> int:
        """Insert or update generated adjacency records for changed edges."""

    @abstractmethod
    def get_neighbors(self, node_id: str, adapter_name: str | None = None) -> list[str]:
        """Load direct neighbor node IDs."""

    @abstractmethod
    def rebuild_evidence_chunks(self, adapter_name: str) -> int:
        """Replace generated evidence chunks for one adapter."""

    @abstractmethod
    def upsert_evidence_chunks(self, evidence: list[CompiledEvidence]) -> int:
        """Insert or update generated evidence chunks for changed evidence."""

    @abstractmethod
    def list_evidence_chunks(self, adapter_name: str) -> list[EvidenceChunk]:
        """Load generated evidence chunks for one adapter."""

    @abstractmethod
    def upsert_retrieval_documents(self, documents: list[RetrievalDocument]) -> int:
        """Insert or update generated retrieval documents."""

    @abstractmethod
    def list_retrieval_documents(self, adapter_name: str, *, target: str = "prod") -> list[RetrievalDocument]:
        """Load generated retrieval documents for one adapter and target."""

    @abstractmethod
    def search_retrieval_documents(
        self,
        adapter_name: str,
        query: str,
        *,
        target: str = "prod",
        limit: int = 20,
    ) -> list[RetrievalDocument]:
        """Lexically search generated retrieval documents."""

    @abstractmethod
    def save_retrieval_document_version(self, version: RetrievalDocumentVersion) -> str:
        """Persist retrieval document generation metadata and return its version ID."""

    @abstractmethod
    def list_retrieval_document_versions(
        self,
        adapter_name: str,
        *,
        target: str = "prod",
        limit: int = 20,
    ) -> list[RetrievalDocumentVersion]:
        """Load retrieval document generation metadata."""

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
