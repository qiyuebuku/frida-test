from __future__ import annotations

import pytest

from src.application.services.external_research_service import (
    ExternalResearchService,
)
from src.domain.external_research.models import (
    ExternalContent,
    ExternalSearchItem,
)


class _FakeProvider:
    name = "fake"

    async def search_web(self, **kwargs):
        return [
            ExternalSearchItem(
                title="Result",
                url="https://example.com/result",
                snippet="Snippet",
                source="example.com",
            )
        ]

    async def read_web(self, **kwargs):
        return ExternalContent(
            title="Example",
            url=kwargs["url"],
            content="0123456789",
            media_type="text/markdown",
        )

    async def search_repository(self, **kwargs):
        return ExternalContent(content="repository search")

    async def get_repository_structure(self, **kwargs):
        return ExternalContent(content="repository structure")

    async def read_repository_file(self, **kwargs):
        return ExternalContent(content="repository file")


class _MemoryStore:
    def __init__(self) -> None:
        self.values = {}

    async def save(self, content, *, provider):
        handle = f"external_content:{len(self.values) + 1}"
        self.values[handle] = (content, provider)
        return handle

    async def load(self, handle):
        return self.values.get(handle)


def _service() -> ExternalResearchService:
    return ExternalResearchService(
        providers={"fake": _FakeProvider()},
        routes={
            "web_search": "fake",
            "web_read": "fake",
            "repository": "fake",
        },
        content_store=_MemoryStore(),
        preview_chars=4,
        max_page_chars=6,
    )


@pytest.mark.asyncio
async def test_search_web_returns_canonical_provider_neutral_results() -> None:
    result = await _service().search_web(query=" query ", limit=3)

    assert result == {
        "operation": "external_web_search",
        "query": "query",
        "provider": "fake",
        "results": [
            {
                "title": "Result",
                "url": "https://example.com/result",
                "snippet": "Snippet",
                "source": "example.com",
            }
        ],
        "next_operations": ["external_web_read"],
    }


@pytest.mark.asyncio
async def test_web_read_returns_preview_and_incremental_content_handle() -> None:
    service = _service()

    opened = await service.read_web(url="https://example.com/article")
    page = await service.read_content(
        handle=opened["content_handle"],
        offset=4,
        max_chars=3,
    )

    assert opened["preview"] == "0123"
    assert opened["content_length"] == 10
    assert opened["truncated"] is True
    assert page["content"] == "456"
    assert page["next_offset"] == 7
    assert page["truncated"] is True


@pytest.mark.asyncio
async def test_repository_operations_share_the_same_stable_contract() -> None:
    service = _service()

    searched = await service.search_repository(
        repository="openai/codex",
        query="model catalog",
    )
    structured = await service.get_repository_structure(
        repository="openai/codex",
    )
    opened = await service.read_repository_file(
        repository="openai/codex",
        path="README.md",
    )

    assert searched["operation"] == "external_repo_search"
    assert structured["operation"] == "external_repo_structure"
    assert opened["operation"] == "external_repo_read"
    assert searched["provider"] == "fake"
    assert searched["content_handle"].startswith("external_content:")
