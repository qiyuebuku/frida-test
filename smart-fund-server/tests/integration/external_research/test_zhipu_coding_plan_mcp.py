from __future__ import annotations

import os

import pytest

from src.application.services.external_research_service import (
    ExternalResearchService,
)
from src.infrastructure.external_research import (
    RedisExternalContentStore,
    ZhipuCodingPlanMcpProvider,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_ZHIPU_MCP_INTEGRATION") != "1",
    reason="Zhipu Coding Plan MCP real API tests are disabled by default",
)


def _service() -> ExternalResearchService:
    api_key = (
        os.getenv("ZHIPU_CODING_PLAN_API_KEY", "")
        or os.getenv("Z_AI_API_KEY", "")
        or os.getenv("ZHIPU_ANTHROPIC_TOKEN", "")
    )
    provider = ZhipuCodingPlanMcpProvider(
        api_key=api_key,
        search_url=os.getenv(
            "ZHIPU_WEB_SEARCH_MCP_URL",
            "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp",
        ),
        reader_url=os.getenv(
            "ZHIPU_WEB_READER_MCP_URL",
            "https://open.bigmodel.cn/api/mcp/web_reader/mcp",
        ),
        repository_url=os.getenv(
            "ZHIPU_ZREAD_MCP_URL",
            "https://open.bigmodel.cn/api/mcp/zread/mcp",
        ),
        timeout_seconds=90,
    )
    return ExternalResearchService(
        providers={"zhipu": provider},
        routes={
            "web_search": "zhipu",
            "web_read": "zhipu",
            "repository": "zhipu",
        },
        content_store=RedisExternalContentStore(
            redis_url=os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
            ttl_seconds=120,
        ),
        preview_chars=500,
        max_page_chars=2000,
    )


@pytest.mark.asyncio
async def test_real_zhipu_search_reader_repository_and_content_handle() -> None:
    service = _service()

    search = await service.search_web(
        query="存储芯片涨价 手机厂商 产品策略",
        limit=2,
    )
    assert search["provider"] == "zhipu"
    assert search["results"]
    assert all(item["url"].startswith(("http://", "https://")) for item in search["results"])

    opened = await service.read_web(
        url="https://docs.bigmodel.cn/cn/coding-plan/mcp/vision-mcp-server"
    )
    assert opened["provider"] == "zhipu"
    assert opened["content_handle"].startswith("external_content:")
    assert opened["content_length"] > 0

    page = await service.read_content(
        handle=opened["content_handle"],
        offset=0,
        max_chars=300,
    )
    assert page["content"]
    assert len(page["content"]) <= 300

    repository = await service.get_repository_structure(
        repository="openai/codex",
        directory="codex-rs",
    )
    assert repository["provider"] == "zhipu"
    assert repository["content_length"] > 0
