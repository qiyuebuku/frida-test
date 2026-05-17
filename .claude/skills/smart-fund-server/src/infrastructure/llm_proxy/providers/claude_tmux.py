"""Claude CLI tmux provider wrapper."""

from __future__ import annotations

from typing import Any

from src.infrastructure.llm_proxy.types import (
    LLMProxyRequest,
    LLMProxyResponse,
    LLMRouteDecision,
)


class ClaudeTmuxProvider:
    name = "claude_tmux"

    def __init__(self, legacy_service: Any, *, enabled: bool = True):
        self.legacy_service = legacy_service
        self.enabled = enabled

    async def generate(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
    ) -> LLMProxyResponse:
        routed_request = LLMProxyRequest(
            prompt=request.prompt or request.prompt_text(),
            system_prompt=request.system_prompt,
            model=route.resolved_model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            json_schema=request.json_schema,
            response_format=request.response_format,
            tools=request.tools,
            tool_choice=request.tool_choice,
            metadata={**request.metadata, "_llm_provider": self.name},
            timeout=request.timeout,
            use_cache=False,
        )
        response = await self.legacy_service.generate(routed_request)
        response.proxy.update(
            {
                "provider": self.name,
                "requested_model": route.requested_model,
                "resolved_model": route.resolved_model,
                "upstream_model": route.resolved_model,
                "route_reason": route.route_reason,
            }
        )
        response.raw_payload.setdefault("provider", self.name)
        return response

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "backend": getattr(self.legacy_service, "backend", ""),
            "default_model": getattr(self.legacy_service, "default_model", ""),
        }

    def runtime_stats(self) -> dict:
        if hasattr(self.legacy_service, "runtime_stats"):
            return self.legacy_service.runtime_stats()
        return {}

    def supports(self, model: str) -> bool:
        return bool(model)
