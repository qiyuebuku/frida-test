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


class CatalogProvider(DummyProvider):
    def __init__(self, name, mappings, enabled=True, available=True):
        super().__init__(name, enabled=enabled)
        self.mappings = mappings
        self.is_available = available

    def supports(self, model):
        return model in self.mappings

    def resolve_model(self, model):
        return self.mappings.get(model)

    def available(self):
        return self.enabled and self.is_available


def test_register_and_get_provider():
    registry = ProviderRegistry()
    provider = DummyProvider()

    registry.register(provider)

    assert registry.get("dummy") is provider


def test_unknown_provider_raises():
    with pytest.raises(LLMProxyError):
        ProviderRegistry().get("missing")


def test_duplicate_provider_name_raises():
    registry = ProviderRegistry()
    registry.register(DummyProvider("same"))

    with pytest.raises(LLMProxyError):
        registry.register(DummyProvider("same"))


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


def test_same_canonical_model_resolves_to_vendor_specific_upstream_ids():
    registry = ProviderRegistry()
    official = CatalogProvider("deepseek", {"deepseek-v4-pro": "deepseek-v4-pro"})
    aliyun = CatalogProvider(
        "aliyun",
        {"deepseek-v4-pro": "vanchin/deepseek-v4-pro"},
    )
    registry.register(official)
    registry.register(aliyun)

    selected, upstream = registry.select_for_model(["aliyun"], "deepseek-v4-pro")

    assert selected is aliyun
    assert upstream == "vanchin/deepseek-v4-pro"


def test_catalog_selection_rejects_model_unsupported_by_all_vendors():
    registry = ProviderRegistry()
    registry.register(CatalogProvider("aliyun", {"qwen3.7-plus": "qwen3.7-plus"}))

    with pytest.raises(LLMProxyError):
        registry.select_for_model([], "qwen3.7-text-embedding")


def test_catalog_selection_skips_unavailable_vendor_and_keeps_same_model():
    registry = ProviderRegistry()
    registry.register(
        CatalogProvider(
            "deepseek",
            {"deepseek-v4-pro": "deepseek-v4-pro"},
            available=False,
        )
    )
    aliyun = CatalogProvider(
        "aliyun",
        {"deepseek-v4-pro": "vanchin/deepseek-v4-pro"},
    )
    registry.register(aliyun)

    selected, upstream = registry.select_for_model(
        ["deepseek", "aliyun"],
        "deepseek-v4-pro",
    )

    assert selected is aliyun
    assert upstream == "vanchin/deepseek-v4-pro"
