"""Provider registry for the LLM proxy gateway."""

from __future__ import annotations

from src.infrastructure.llm_proxy.providers.base import LLMProvider
from src.infrastructure.llm_proxy.types import LLMProxyError


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, LLMProvider] = {}

    def register(self, provider: LLMProvider) -> None:
        self._providers[provider.name] = provider

    def get(self, name: str) -> LLMProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise LLMProxyError(f"未注册 LLM provider: {name}", error_type="bad_request")
        if not provider.enabled:
            raise LLMProxyError(f"LLM provider 未启用: {name}", error_type="bad_request")
        return provider

    def select_first_available(self, candidates: list[str]) -> LLMProvider:
        errors: list[str] = []
        for name in candidates:
            try:
                return self.get(name)
            except LLMProxyError as exc:
                errors.append(str(exc))
        raise LLMProxyError(
            f"没有可用 LLM provider: {', '.join(candidates)}; {'; '.join(errors)}",
            error_type="bad_request",
        )

    def health(self) -> dict[str, dict]:
        return {name: provider.health() for name, provider in sorted(self._providers.items())}

    def runtime_stats(self) -> dict[str, dict]:
        return {
            name: provider.runtime_stats()
            for name, provider in sorted(self._providers.items())
        }

    def names(self) -> list[str]:
        return sorted(self._providers)
