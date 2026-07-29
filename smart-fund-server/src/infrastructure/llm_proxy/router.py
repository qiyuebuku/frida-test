"""Model-name routing for the LLM proxy gateway."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from src.infrastructure.llm_proxy.types import LLMRouteDecision


@dataclass(frozen=True)
class ModelRouterConfig:
    default_model: str
    default_provider: str
    model_routes: dict[str, list[str]]
    model_aliases: dict[str, str]


class ModelRouter:
    """Resolve a requested model name to provider candidates."""

    def __init__(self, config: ModelRouterConfig):
        self.config = config

    def resolve(
        self,
        requested_model: str | None,
        requested_provider: str | None = None,
    ) -> LLMRouteDecision:
        raw_model = (requested_model or "").strip()
        route_reason = "model_exact"
        if not raw_model:
            raw_model = self.config.default_model
            route_reason = "default"

        resolved_model = self.config.model_aliases.get(raw_model, raw_model)
        if resolved_model != raw_model:
            route_reason = "alias"

        explicit_provider = str(requested_provider or "").strip()
        if explicit_provider:
            candidates = [explicit_provider]
            route_reason = "provider_explicit"
        else:
            candidates = self.config.model_routes.get(resolved_model)
            if not candidates:
                candidates = self._glob_candidates(resolved_model)
                if candidates and route_reason == "model_exact":
                    route_reason = "model_pattern"
            if not candidates:
                candidates = []
                if route_reason == "model_exact":
                    route_reason = "provider_catalog"

        return LLMRouteDecision(
            requested_model=requested_model,
            resolved_model=resolved_model,
            provider_candidates=list(candidates),
            selected_provider=candidates[0] if candidates else None,
            route_reason=route_reason,
            fallback_allowed=len(candidates) > 1,
            requested_provider=explicit_provider or None,
        )

    def _glob_candidates(self, model: str) -> list[str]:
        matches: list[tuple[int, list[str]]] = []
        lowered = model.lower()
        for pattern, candidates in self.config.model_routes.items():
            if not any(token in pattern for token in ("*", "?", "[")):
                continue
            if fnmatch.fnmatchcase(lowered, pattern.lower()):
                literal_length = len(pattern.replace("*", "").replace("?", ""))
                matches.append((literal_length, candidates))
        if not matches:
            return []
        matches.sort(key=lambda item: item[0], reverse=True)
        return list(matches[0][1])
