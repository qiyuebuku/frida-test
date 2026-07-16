"""Shared types for the LLM proxy gateway."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


class LLMProxyError(RuntimeError):
    """LLM proxy layer error."""

    def __init__(self, message: str, *, error_type: str = "upstream_error"):
        super().__init__(message)
        self.error_type = error_type


@dataclass(frozen=True)
class LLMProxyRequest:
    prompt: str = ""
    system_prompt: str | None = None
    model: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = 0.0
    max_tokens: int | None = None
    json_schema: dict[str, Any] | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    provider_options: dict[str, Any] = field(default_factory=dict)
    timeout: float | None = None
    use_cache: bool = True

    def prompt_text(self) -> str:
        if self.prompt:
            return self.prompt
        parts: list[str] = []
        for message in self.messages:
            role = str(message.get("role") or "user").upper()
            content = message.get("content") or ""
            if isinstance(content, list):
                text_parts = [
                    str(item.get("text"))
                    for item in content
                    if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
                ]
                content = "\n".join(text_parts)
            if str(content).strip():
                parts.append(f"{role}:\n{content}")
        return "\n\n".join(parts)


@dataclass
class LLMProxyResponse:
    text: str
    structured_output: Any | None
    usage: dict[str, Any]
    session_id: str | None
    duration_ms: int
    raw_payload: dict[str, Any]
    cache_hit: bool = False
    proxy: dict[str, Any] = field(default_factory=dict)
    reasoning_content: str = ""

    def clone(self, *, cache_hit: bool | None = None) -> "LLMProxyResponse":
        return LLMProxyResponse(
            text=self.text,
            structured_output=copy.deepcopy(self.structured_output),
            usage=dict(self.usage),
            session_id=self.session_id,
            duration_ms=self.duration_ms,
            raw_payload=copy.deepcopy(self.raw_payload),
            cache_hit=self.cache_hit if cache_hit is None else cache_hit,
            proxy=copy.deepcopy(self.proxy),
            reasoning_content=self.reasoning_content,
        )


@dataclass(frozen=True)
class LLMRouteDecision:
    requested_model: str | None
    resolved_model: str
    provider_candidates: list[str]
    selected_provider: str | None
    route_reason: str
    fallback_allowed: bool = True


ClaudeProxyRequest = LLMProxyRequest
ClaudeProxyResponse = LLMProxyResponse
