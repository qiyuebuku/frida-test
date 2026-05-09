import asyncio

from src.infrastructure.llm_proxy.registry import ProviderRegistry
from src.infrastructure.llm_proxy.router import ModelRouter, ModelRouterConfig
from src.infrastructure.llm_proxy.service import LLMGatewayService
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMProxyResponse


class EchoProvider:
    def __init__(self, name):
        self.name = name
        self.enabled = True
        self.calls = []

    async def generate(self, request, route):
        self.calls.append((request, route))
        return LLMProxyResponse(
            text=f"{self.name}:{route.resolved_model}",
            structured_output=None,
            usage={"input_tokens": 1, "output_tokens": 1},
            session_id=None,
            duration_ms=1,
            raw_payload={},
            proxy={"provider": self.name},
        )

    def health(self):
        return {"enabled": self.enabled}

    def runtime_stats(self):
        return {}

    def supports(self, model):
        return True


def _service():
    registry = ProviderRegistry()
    deepseek = EchoProvider("deepseek")
    claude = EchoProvider("claude_tmux")
    registry.register(deepseek)
    registry.register(claude)
    router = ModelRouter(
        ModelRouterConfig(
            default_model="glm-5.1",
            default_provider="claude_tmux",
            model_routes={
                "deepseek-v4-flash": ["deepseek"],
                "glm-5.1": ["claude_tmux"],
            },
            model_aliases={"glm5.1": "glm-5.1"},
        )
    )
    return LLMGatewayService(
        router=router,
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=16,
    ), deepseek, claude


def test_gateway_routes_deepseek_model_to_deepseek_provider():
    service, deepseek, claude = _service()

    response = asyncio.run(
        service.generate(LLMProxyRequest(prompt="hello", model="deepseek-v4-flash"))
    )

    assert response.text == "deepseek:deepseek-v4-flash"
    assert len(deepseek.calls) == 1
    assert len(claude.calls) == 0


def test_gateway_routes_glm_alias_to_claude_tmux_provider():
    service, _deepseek, claude = _service()

    response = asyncio.run(service.generate(LLMProxyRequest(prompt="hello", model="glm5.1")))

    assert response.text == "claude_tmux:glm-5.1"
    assert response.proxy["resolved_model"] == "glm-5.1"
    assert response.proxy["route_reason"] == "alias"
    assert len(claude.calls) == 1


def test_gateway_cache_key_includes_provider():
    service, deepseek, _claude = _service()
    request = LLMProxyRequest(prompt="hello", model="deepseek-v4-flash")

    first = asyncio.run(service.generate(request))
    second = asyncio.run(service.generate(request))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(deepseek.calls) == 1


def test_gateway_health_lists_routes_and_providers():
    service, _deepseek, _claude = _service()

    health = service.health()

    assert health["model_routes"]["deepseek-v4-flash"] == ["deepseek"]
    assert sorted(health["providers"]) == ["claude_tmux", "deepseek"]
