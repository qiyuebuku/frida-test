"""Model-name routing for the LLM proxy gateway."""

from __future__ import annotations

from dataclasses import dataclass

from src.infrastructure.llm_proxy.types import LLMProxyError, LLMRouteDecision


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

    def resolve(self, requested_model: str | None) -> LLMRouteDecision:
        raw_model = (requested_model or "").strip()
        route_reason = "model_exact"
        if not raw_model:
            raw_model = self.config.default_model
            route_reason = "default"

        resolved_model = self.config.model_aliases.get(raw_model, raw_model)
        if resolved_model != raw_model:
            route_reason = "alias"

        candidates = self.config.model_routes.get(resolved_model)
        if not candidates:
            candidates = self._prefix_candidates(resolved_model)
        if not candidates:
            if route_reason == "default":
                candidates = [self.config.default_provider]
            else:
                raise LLMProxyError(
                    f"未配置模型路由: {resolved_model}",
                    error_type="bad_request",
                )

        return LLMRouteDecision(
            requested_model=requested_model,
            resolved_model=resolved_model,
            provider_candidates=list(candidates),
            selected_provider=candidates[0] if candidates else None,
            route_reason=route_reason,
            fallback_allowed=len(candidates) > 1,
        )

    def _prefix_candidates(self, model: str) -> list[str]:
        lowered = model.lower()
        if lowered.startswith("deepseek-"):
            return self.config.model_routes.get("deepseek-*", ["deepseek"])
        if lowered.startswith(("glm-", "glm")):
            return self.config.model_routes.get("glm-*", [self.config.default_provider])
        return []
