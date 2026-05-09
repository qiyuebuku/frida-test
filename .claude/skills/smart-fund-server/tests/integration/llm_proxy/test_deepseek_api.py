import asyncio
import os

import pytest

from src.infrastructure.llm_proxy.providers.deepseek_openai import DeepSeekOpenAIProvider
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMRouteDecision


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_INTEGRATION") != "1",
    reason="DeepSeek real API tests are disabled by default",
)


def _route(model="deepseek-v4-flash"):
    return LLMRouteDecision(
        requested_model=model,
        resolved_model=model,
        provider_candidates=["deepseek"],
        selected_provider="deepseek",
        route_reason="model_exact",
    )


def test_real_deepseek_simple_chat():
    provider = DeepSeekOpenAIProvider(
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        default_model="deepseek-v4-flash",
        timeout=60,
    )

    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(
                prompt="请只回答 ok",
                model="deepseek-v4-flash",
                max_tokens=20,
                use_cache=False,
            ),
            _route(),
        )
    )

    assert response.text
    assert response.proxy["provider"] == "deepseek"


def test_real_deepseek_json_object():
    provider = DeepSeekOpenAIProvider(
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        default_model="deepseek-v4-flash",
        timeout=60,
    )

    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(
                prompt='请输出 JSON：{"ok": true}',
                model="deepseek-v4-flash",
                json_schema={"type": "object"},
                max_tokens=100,
                use_cache=False,
            ),
            _route(),
        )
    )

    assert isinstance(response.structured_output, dict)
