import pytest

from src.infrastructure.llm_proxy.registry import ProviderRegistry
from src.infrastructure.llm_proxy.types import LLMProxyError


class DummyProvider:
    def __init__(self, name="dummy", enabled=True):
        self.name = name
        self.enabled = enabled

    async def generate(self, request, route):
        raise NotImplementedError

    def health(self):
        return {"enabled": self.enabled}

    def runtime_stats(self):
        return {"name": self.name}

    def supports(self, model):
        return True


def test_register_and_get_provider():
    registry = ProviderRegistry()
    provider = DummyProvider()

    registry.register(provider)

    assert registry.get("dummy") is provider


def test_unknown_provider_raises():
    with pytest.raises(LLMProxyError):
        ProviderRegistry().get("missing")


def test_disabled_provider_not_selected():
    registry = ProviderRegistry()
    registry.register(DummyProvider(enabled=False))

    with pytest.raises(LLMProxyError):
        registry.get("dummy")


def test_select_first_available_skips_missing_provider():
    registry = ProviderRegistry()
    provider = DummyProvider("second")
    registry.register(provider)

    assert registry.select_first_available(["missing", "second"]) is provider


def test_provider_health_aggregated():
    registry = ProviderRegistry()
    registry.register(DummyProvider("a"))
    registry.register(DummyProvider("b", enabled=False))

    assert registry.health() == {
        "a": {"enabled": True},
        "b": {"enabled": False},
    }
