"""Provider-neutral external web and repository research service."""

from __future__ import annotations

from dataclasses import asdict
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse

from src.domain.external_research.models import ExternalContent
from src.domain.external_research.provider import ExternalResearchProvider
from src.domain.external_research.store import ExternalContentStore
from src.infrastructure.config import settings
from src.infrastructure.external_research import (
    RedisExternalContentStore,
    ZhipuCodingPlanMcpProvider,
)


class ExternalResearchService:
    def __init__(
        self,
        *,
        providers: dict[str, ExternalResearchProvider],
        routes: dict[str, str],
        content_store: ExternalContentStore,
        preview_chars: int,
        max_page_chars: int,
    ) -> None:
        self._providers = providers
        self._routes = routes
        self._content_store = content_store
        self._preview_chars = preview_chars
        self._max_page_chars = max_page_chars

    async def search_web(
        self,
        *,
        query: str,
        domain: str = "",
        recency: str = "noLimit",
        content_size: str = "medium",
        location: str = "cn",
        limit: int = 10,
    ) -> dict[str, Any]:
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        if limit < 1 or limit > 20:
            raise ValueError("limit must be between 1 and 20")
        provider = self._provider("web_search")
        items = await provider.search_web(
            query=normalized_query,
            domain=domain.strip(),
            recency=recency,
            content_size=content_size,
            location=location,
            limit=limit,
        )
        return {
            "operation": "external_web_search",
            "query": normalized_query,
            "provider": provider.name,
            "results": [
                {
                    key: value
                    for key, value in asdict(item).items()
                    if value not in ("", None, {})
                }
                for item in items
            ],
            "next_operations": ["external_web_read"],
        }

    async def read_web(
        self,
        *,
        url: str,
        no_cache: bool = False,
    ) -> dict[str, Any]:
        normalized_url = _validate_http_url(url)
        provider = self._provider("web_read")
        content = await provider.read_web(
            url=normalized_url,
            no_cache=no_cache,
        )
        return await self._content_result(
            operation="external_web_read",
            provider=provider,
            content=content,
        )

    async def search_repository(
        self,
        *,
        repository: str,
        query: str,
        language: str = "zh",
    ) -> dict[str, Any]:
        normalized_repository = _validate_repository(repository)
        normalized_query = query.strip()
        if not normalized_query:
            raise ValueError("query must not be empty")
        provider = self._provider("repository")
        content = await provider.search_repository(
            repository=normalized_repository,
            query=normalized_query,
            language=language,
        )
        return await self._content_result(
            operation="external_repo_search",
            provider=provider,
            content=content,
        )

    async def get_repository_structure(
        self,
        *,
        repository: str,
        directory: str = "/",
    ) -> dict[str, Any]:
        normalized_repository = _validate_repository(repository)
        provider = self._provider("repository")
        content = await provider.get_repository_structure(
            repository=normalized_repository,
            directory=directory.strip() or "/",
        )
        return await self._content_result(
            operation="external_repo_structure",
            provider=provider,
            content=content,
        )

    async def read_repository_file(
        self,
        *,
        repository: str,
        path: str,
    ) -> dict[str, Any]:
        normalized_repository = _validate_repository(repository)
        normalized_path = path.strip().lstrip("/")
        if not normalized_path or normalized_path.startswith("../"):
            raise ValueError("path must be a repository-relative file path")
        provider = self._provider("repository")
        content = await provider.read_repository_file(
            repository=normalized_repository,
            path=normalized_path,
        )
        return await self._content_result(
            operation="external_repo_read",
            provider=provider,
            content=content,
        )

    async def read_content(
        self,
        *,
        handle: str,
        offset: int = 0,
        max_chars: int = 12000,
    ) -> dict[str, Any]:
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if max_chars < 1 or max_chars > self._max_page_chars:
            raise ValueError(
                f"max_chars must be between 1 and {self._max_page_chars}"
            )
        stored = await self._content_store.load(handle)
        if stored is None:
            raise ValueError("content handle is invalid or expired")
        content, provider_name = stored
        end = min(len(content.content), offset + max_chars)
        next_offset = end if end < len(content.content) else None
        return {
            "operation": "external_content_read",
            "provider": provider_name,
            "content_handle": handle,
            "title": content.title,
            "url": content.url,
            "media_type": content.media_type,
            "offset": offset,
            "content": content.content[offset:end],
            "content_length": len(content.content),
            "next_offset": next_offset,
            "truncated": next_offset is not None,
        }

    def _provider(self, capability: str) -> ExternalResearchProvider:
        provider_name = self._routes.get(capability, "").strip()
        provider = self._providers.get(provider_name)
        if provider is None:
            raise RuntimeError(
                f"external research provider is unavailable: "
                f"{capability} -> {provider_name or '<empty>'}"
            )
        return provider

    async def _content_result(
        self,
        *,
        operation: str,
        provider: ExternalResearchProvider,
        content: ExternalContent,
    ) -> dict[str, Any]:
        handle = await self._content_store.save(
            content,
            provider=provider.name,
        )
        preview_end = min(len(content.content), self._preview_chars)
        return {
            "operation": operation,
            "provider": provider.name,
            "title": content.title,
            "url": content.url,
            "media_type": content.media_type,
            "content_handle": handle,
            "preview": content.content[:preview_end],
            "content_length": len(content.content),
            "truncated": preview_end < len(content.content),
            "next_operations": ["external_content_read"],
        }


@lru_cache(maxsize=1)
def create_external_research_service() -> ExternalResearchService:
    zhipu = ZhipuCodingPlanMcpProvider(
        api_key=settings.ZHIPU_CODING_PLAN_API_KEY,
        search_url=settings.ZHIPU_WEB_SEARCH_MCP_URL,
        reader_url=settings.ZHIPU_WEB_READER_MCP_URL,
        repository_url=settings.ZHIPU_ZREAD_MCP_URL,
        timeout_seconds=settings.EXTERNAL_RESEARCH_TIMEOUT_SECONDS,
    )
    return ExternalResearchService(
        providers={"zhipu": zhipu},
        routes={
            "web_search": settings.EXTERNAL_WEB_SEARCH_PROVIDER,
            "web_read": settings.EXTERNAL_WEB_READ_PROVIDER,
            "repository": settings.EXTERNAL_REPOSITORY_PROVIDER,
        },
        content_store=RedisExternalContentStore(
            redis_url=settings.REDIS_URL,
            ttl_seconds=settings.EXTERNAL_CONTENT_TTL_SECONDS,
        ),
        preview_chars=settings.EXTERNAL_CONTENT_PREVIEW_CHARS,
        max_page_chars=settings.EXTERNAL_CONTENT_MAX_PAGE_CHARS,
    )


def _validate_http_url(value: str) -> str:
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute HTTP or HTTPS URL")
    return normalized


def _validate_repository(value: str) -> str:
    normalized = value.strip().strip("/")
    parts = normalized.split("/")
    if len(parts) != 2 or not all(parts):
        raise ValueError("repository must use the owner/repo format")
    return normalized
