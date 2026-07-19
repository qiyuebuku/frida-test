"""Provider protocol for the LLM proxy gateway."""

from __future__ import annotations

from typing import Protocol

from src.infrastructure.llm_proxy.types import (
    LLMProxyRequest,
    LLMProxyResponse,
    LLMRouteDecision,
)


class LLMProvider(Protocol):
    name: str
    enabled: bool

    async def generate(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
    ) -> LLMProxyResponse:
        ...

    def health(self) -> dict:
        ...

    def runtime_stats(self) -> dict:
        ...

    def supports(self, model: str) -> bool:
        ...

    def resolve_model(self, model: str) -> str | None:
        ...

    def available(self) -> bool:
        ...
