"""Repository contracts for the generic knowledge domain."""

from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.domain.knowledge.repositories.knowledge_source_projection_repository import (
    KnowledgeSourceProjectionRepository,
)

__all__ = ["KnowledgeRepository", "KnowledgeSourceProjectionRepository"]
