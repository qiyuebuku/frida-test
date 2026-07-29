"""Repository protocol for reading raw rows used by KG source projection."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class KnowledgeSourceProjectionRepository(ABC):
    """Reads business-table raw rows without producing Source Records."""

    @abstractmethod
    def fetch_rows(
        self,
        source: str,
        *,
        limit: int,
        codes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return source rows as plain dictionaries."""
