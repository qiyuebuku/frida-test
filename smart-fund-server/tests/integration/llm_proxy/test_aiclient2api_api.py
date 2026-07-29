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
    os.getenv("RUN_AICLIENT2API_LLM_INTEGRATION") != "1",
    reason="AIClient2API real API tests are disabled by default",
)


def _gateway() -> LLMGatewayService:
    model = os.getenv("AICLIENT2API_LLM_DEFAULT_MODEL", "glm-5.2")
    provider = OpenAICompatibleProvider(
        name="aiclient2api",
        base_url=os.getenv(
            "AICLIENT2API_LLM_BASE_URL",
            "http://119.23.227.187:13000/v1",
        ),
        api_key=os.getenv("AICLIENT2API_LLM_API_KEY", "")
        or os.getenv("AICLIENT2API_API_KEY", ""),
        default_model=model,
        timeout=float(os.getenv("AICLIENT2API_LLM_TIMEOUT", "1800")),
        model_mappings={model: model},
        reasoning_style="aiclient2api",
    )
    registry = ProviderRegistry()
    registry.register(provider)
    return LLMGatewayService(
        router=ModelRouter(
            ModelRouterConfig(
                default_model=model,
                default_provider="aiclient2api",
                model_routes={model: ["aiclient2api"]},
                model_aliases={},
            )
        ),
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=4,
    )


def test_real_aiclient2api_simple_chat() -> None:
    response = asyncio.run(
        _gateway().generate(
            LLMProxyRequest(
                prompt="请只回答 AICLIENT_OK",
                model="glm-5.2",
                provider="aiclient2api",
                max_tokens=32,
                use_cache=False,
            )
        )
    )

    assert "AICLIENT_OK" in response.text
    assert response.proxy["provider"] == "aiclient2api"
    assert response.proxy["upstream_model"] == "glm-5.2"


def test_real_aiclient2api_json_object() -> None:
    response = asyncio.run(
        _gateway().generate(
            LLMProxyRequest(
                prompt='返回 {"ok": true}',
                system_prompt="只输出合法 JSON 对象。",
                model="glm-5.2",
                provider="aiclient2api",
                max_tokens=64,
                json_schema={
                    "type": "object",
                    "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                use_cache=False,
            )
        )
    )

    assert response.structured_output == {"ok": True}
    assert response.proxy["provider"] == "aiclient2api"


def test_real_aiclient2api_thinking() -> None:
    response = asyncio.run(
        _gateway().generate(
            LLMProxyRequest(
                prompt=(
                    "某商品库存连续下降、现货价格却同步下跌。"
                    "请判断这是否矛盾，并只用一句话说明最可能的解释。"
                ),
                model="glm-5.2",
                provider="aiclient2api",
                max_tokens=2048,
                provider_options={
                    "thinking_type": "enabled",
                    "reasoning_effort": "medium",
                },
                use_cache=False,
            )
        )
    )

    assert response.text.strip()
    assert response.reasoning_content.strip()
    assert response.proxy["provider"] == "aiclient2api"
