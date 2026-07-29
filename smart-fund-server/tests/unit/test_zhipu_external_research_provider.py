from __future__ import annotations

import pytest

from src.infrastructure.external_research.zhipu_coding_plan import (
    ZhipuCodingPlanMcpProvider,
)


class _FakeMcpClient:
    def __init__(self, result) -> None:
        self.result = result
        self.calls = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return self.result


def _provider() -> ZhipuCodingPlanMcpProvider:
    return ZhipuCodingPlanMcpProvider(
        api_key="key",
        search_url="https://example.com/search",
        reader_url="https://example.com/reader",
        repository_url="https://example.com/repository",
        timeout_seconds=30,
    )


@pytest.mark.asyncio
async def test_zhipu_search_is_normalized_to_canonical_items() -> None:
    provider = _provider()
    client = _FakeMcpClient(
        [
            {
                "title": "Title",
                "link": "https://news.example.com/a",
                "content": "Summary",
                "refer": "ref_1",
            }
        ]
    )
    provider._search_client = client

    items = await provider.search_web(
        query="query",
        domain="",
        recency="oneWeek",
        content_size="medium",
        location="cn",
        limit=5,
    )

    assert len(items) == 1
    assert items[0].title == "Title"
    assert items[0].url == "https://news.example.com/a"
    assert items[0].source == "news.example.com"
    assert items[0].metadata == {"reference": "ref_1"}
    assert client.calls[0][0] == "web_search_prime"


@pytest.mark.asyncio
async def test_zhipu_reader_discards_image_payloads() -> None:
    provider = _provider()
    client = _FakeMcpClient(
        {
            "title": "Page",
            "url": "https://example.com/page",
            "content": "# Page",
            "metadata": {"author": "A"},
        }
    )
    provider._reader_client = client

    content = await provider.read_web(
        url="https://example.com/page",
        no_cache=False,
    )

    assert content.title == "Page"
    assert content.content == "# Page"
    assert content.media_type == "text/markdown"
    assert client.calls == [
        (
            "webReader",
            {
                "url": "https://example.com/page",
                "no_cache": False,
                "return_format": "markdown",
                "retain_images": False,
                "keep_img_data_url": False,
                "with_images_summary": False,
                "with_links_summary": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_zhipu_repository_tool_names_are_hidden_by_adapter() -> None:
    provider = _provider()
    client = _FakeMcpClient("repository result")
    provider._repository_client = client

    result = await provider.search_repository(
        repository="openai/codex",
        query="model catalog",
        language="en",
    )

    assert result.content == "repository result"
    assert client.calls == [
        (
            "search_doc",
            {
                "repo_name": "openai/codex",
                "query": "model catalog",
                "language": "en",
            },
        )
    ]
