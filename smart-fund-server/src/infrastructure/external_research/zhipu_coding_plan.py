"""GLM Coding Plan MCP adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from src.domain.external_research.models import (
    ExternalContent,
    ExternalSearchItem,
)
from src.infrastructure.external_research.mcp_client import RemoteMcpToolClient


class ZhipuCodingPlanMcpProvider:
    def __init__(
        self,
        *,
        api_key: str,
        search_url: str,
        reader_url: str,
        repository_url: str,
        timeout_seconds: float,
    ) -> None:
        self._search_client = RemoteMcpToolClient(
            url=search_url,
            bearer_token=api_key,
            timeout_seconds=timeout_seconds,
        )
        self._reader_client = RemoteMcpToolClient(
            url=reader_url,
            bearer_token=api_key,
            timeout_seconds=timeout_seconds,
        )
        self._repository_client = RemoteMcpToolClient(
            url=repository_url,
            bearer_token=api_key,
            timeout_seconds=timeout_seconds,
        )

    @property
    def name(self) -> str:
        return "zhipu"

    async def search_web(
        self,
        *,
        query: str,
        domain: str,
        recency: str,
        content_size: str,
        location: str,
        limit: int,
    ) -> list[ExternalSearchItem]:
        arguments = {
            "search_query": query,
            "search_recency_filter": recency,
            "content_size": content_size,
            "location": location,
        }
        if domain:
            arguments["search_domain_filter"] = domain
        raw_results = await self._search_client.call_tool(
            "web_search_prime",
            arguments,
        )
        if not isinstance(raw_results, list):
            raise RuntimeError("Zhipu web search returned a non-list payload")
        items: list[ExternalSearchItem] = []
        for raw_item in raw_results[:limit]:
            if not isinstance(raw_item, Mapping):
                continue
            url = str(raw_item.get("link") or raw_item.get("url") or "").strip()
            if not url:
                continue
            items.append(
                ExternalSearchItem(
                    title=str(raw_item.get("title") or "").strip(),
                    url=url,
                    snippet=str(
                        raw_item.get("content")
                        or raw_item.get("snippet")
                        or ""
                    ).strip(),
                    source=_source_name(url),
                    metadata={
                        "reference": str(raw_item.get("refer") or "").strip()
                    },
                )
            )
        return items

    async def read_web(
        self,
        *,
        url: str,
        no_cache: bool,
    ) -> ExternalContent:
        raw_result = await self._reader_client.call_tool(
            "webReader",
            {
                "url": url,
                "no_cache": no_cache,
                "return_format": "markdown",
                "retain_images": False,
                "keep_img_data_url": False,
                "with_images_summary": False,
                "with_links_summary": False,
            },
        )
        if not isinstance(raw_result, Mapping):
            raise RuntimeError("Zhipu web reader returned a non-object payload")
        return ExternalContent(
            title=str(raw_result.get("title") or "").strip(),
            url=str(raw_result.get("url") or url).strip(),
            content=str(raw_result.get("content") or ""),
            media_type="text/markdown",
            metadata=dict(raw_result.get("metadata") or {}),
        )

    async def search_repository(
        self,
        *,
        repository: str,
        query: str,
        language: str,
    ) -> ExternalContent:
        content = await self._repository_text(
            "search_doc",
            {
                "repo_name": repository,
                "query": query,
                "language": language,
            },
        )
        return ExternalContent(
            title=f"{repository}: {query}",
            url=f"https://github.com/{repository}",
            content=content,
            media_type="text/markdown",
            metadata={"repository": repository, "operation": "search"},
        )

    async def get_repository_structure(
        self,
        *,
        repository: str,
        directory: str,
    ) -> ExternalContent:
        arguments = {"repo_name": repository}
        if directory:
            arguments["dir_path"] = directory
        content = await self._repository_text(
            "get_repo_structure",
            arguments,
        )
        return ExternalContent(
            title=f"{repository}: {directory or '/'}",
            url=f"https://github.com/{repository}",
            content=content,
            media_type="text/plain",
            metadata={"repository": repository, "directory": directory or "/"},
        )

    async def read_repository_file(
        self,
        *,
        repository: str,
        path: str,
    ) -> ExternalContent:
        content = await self._repository_text(
            "read_file",
            {
                "repo_name": repository,
                "file_path": path,
            },
        )
        return ExternalContent(
            title=f"{repository}/{path}",
            url=f"https://github.com/{repository}/blob/HEAD/{path}",
            content=content,
            media_type="text/plain",
            metadata={"repository": repository, "path": path},
        )

    async def _repository_text(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        raw_result = await self._repository_client.call_tool(
            tool_name,
            arguments,
        )
        if isinstance(raw_result, str):
            return raw_result
        return str(raw_result)


def _source_name(url: str) -> str:
    return urlparse(url).netloc.lower()
