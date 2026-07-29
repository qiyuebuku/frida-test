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
    os.getenv("RUN_ALIYUN_LLM_INTEGRATION") != "1",
    reason="Aliyun Model Studio real API tests are disabled by default",
)


def test_real_aliyun_simple_chat() -> None:
    model = os.getenv("ALIYUN_LLM_DEFAULT_MODEL", "qwen3.7-plus")
    canonical_model = "aliyun-integration-chat"
    provider = OpenAICompatibleProvider(
        name="aliyun",
        base_url=os.getenv(
            "ALIYUN_LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        api_key=os.getenv("ALIYUN_LLM_API_KEY", "")
        or os.getenv("DASHSCOPE_API_KEY", ""),
        default_model=model,
        timeout=float(os.getenv("ALIYUN_LLM_TIMEOUT", "1800")),
        model_mappings={canonical_model: model},
        reasoning_style="aliyun",
    )
    registry = ProviderRegistry()
    registry.register(provider)
    gateway = LLMGatewayService(
        router=ModelRouter(
            ModelRouterConfig(
                default_model=canonical_model,
                default_provider="aliyun",
                model_routes={canonical_model: ["aliyun"]},
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
                provider="aliyun",
                max_tokens=20,
                use_cache=False,
            )
        )
    )

    assert response.text.strip()
    assert response.proxy["provider"] == "aliyun"
    assert response.proxy["resolved_model"] == canonical_model
    assert response.proxy["upstream_model"] == model
