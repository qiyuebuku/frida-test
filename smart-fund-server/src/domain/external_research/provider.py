"""Provider protocol for web and repository research capabilities."""

from __future__ import annotations

from typing import Protocol

from src.domain.external_research.models import (
    ExternalContent,
    ExternalSearchItem,
)


class ExternalResearchProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def search_web(
        self,
        *,
        query: str,
        domain: str,
        recency: str,
        content_size: str,
        location: str,
        limit: int,
    ) -> list[ExternalSearchItem]: ...

    async def read_web(
        self,
        *,
        url: str,
        no_cache: bool,
    ) -> ExternalContent: ...

    async def search_repository(
        self,
        *,
        repository: str,
        query: str,
        language: str,
    ) -> ExternalContent: ...

    async def get_repository_structure(
        self,
        *,
        repository: str,
        directory: str,
    ) -> ExternalContent: ...

    async def read_repository_file(
        self,
        *,
        repository: str,
        path: str,
    ) -> ExternalContent: ...
