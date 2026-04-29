"""Repository contract for generic knowledge persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.domain.knowledge.quality import ReviewAction, ReviewEntry
from src.domain.knowledge.schemas import CompiledEdge, CompiledEvidence, CompiledNode, EvidenceChunk
from src.domain.knowledge.wiki import WikiPage


class KnowledgeRepository(ABC):
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
    def list_evidence(self, adapter_name: str) -> list[CompiledEvidence]:
        """Load evidence records for one adapter."""

    @abstractmethod
    def rebuild_wiki_pages(self, adapter_name: str, pages: list[WikiPage]) -> int:
        """Replace generated wiki pages for one adapter."""

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
    def get_neighbors(self, node_id: str, adapter_name: str | None = None) -> list[str]:
        """Load direct neighbor node IDs."""

    @abstractmethod
    def rebuild_evidence_chunks(self, adapter_name: str) -> int:
        """Replace generated evidence chunks for one adapter."""

    @abstractmethod
    def list_evidence_chunks(self, adapter_name: str) -> list[EvidenceChunk]:
        """Load generated evidence chunks for one adapter."""

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
