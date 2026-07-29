"""Storage contract for large external research content."""

from __future__ import annotations

from typing import Protocol

from src.domain.external_research.models import ExternalContent


class ExternalContentStore(Protocol):
    async def save(self, content: ExternalContent, *, provider: str) -> str: ...

    async def load(self, handle: str) -> tuple[ExternalContent, str] | None: ...
