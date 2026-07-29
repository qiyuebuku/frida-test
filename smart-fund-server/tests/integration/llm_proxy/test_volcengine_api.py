import asyncio
import os

import pytest

from src.infrastructure.llm_proxy.providers.openai_compatible import (
    OpenAICompatibleProvider,
)
from src.infrastructure.llm_proxy.registry import ProviderRegistry
from src.infrastructure.llm_proxy.router import ModelRouter, ModelRouterConfig
from src.infrastructure.llm_proxy.service import LLMGatewayService
from src.infrastructure.llm_proxy.types import LLMProxyRequest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_VOLCENGINE_LLM_INTEGRATION") != "1",
    reason="Volcengine Ark real API tests are disabled by default",
)


def test_real_volcengine_simple_chat() -> None:
    upstream_model = os.getenv(
        "VOLCENGINE_LLM_UPSTREAM_MODEL",
        "doubao-seed-2-1-turbo-260628",
    )
    canonical_model = os.getenv(
        "VOLCENGINE_LLM_CANONICAL_MODEL",
        "doubao-seed-2.1-turbo",
    )
    provider = OpenAICompatibleProvider(
        name="volcengine",
        base_url=os.getenv(
            "VOLCENGINE_LLM_BASE_URL",
            "https://ark.cn-beijing.volces.com/api/v3",
        ),
        api_key=os.getenv("VOLCENGINE_LLM_API_KEY", "")
        or os.getenv("VOLCENGINE_ARK_API_KEY", "")
        or os.getenv("ARK_API_KEY", ""),
        default_model="doubao-seed-2.1-turbo",
        timeout=float(os.getenv("VOLCENGINE_LLM_TIMEOUT", "1800")),
        model_mappings={canonical_model: upstream_model},
        reasoning_style="volcengine",
    )
    registry = ProviderRegistry()
    registry.register(provider)
    gateway = LLMGatewayService(
        router=ModelRouter(
            ModelRouterConfig(
                default_model=canonical_model,
                default_provider="volcengine",
                model_routes={canonical_model: ["volcengine"]},
                model_aliases={},
            )
        ),
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=4,
    )

    response = asyncio.run(
        gateway.generate(
            LLMProxyRequest(
                prompt="请只回答 ok",
                model=canonical_model,
                provider="volcengine",
                max_tokens=20,
                use_cache=False,
            )
        )
    )

    assert response.text.strip()
    assert response.proxy["provider"] == "volcengine"
    assert response.proxy["resolved_model"] == canonical_model
    assert response.proxy["upstream_model"] == upstream_model
