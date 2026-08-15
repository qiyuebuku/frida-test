"""Role-specific model settings for the financial Research runtime."""

from __future__ import annotations

from agents import ModelSettings
from openai.types.shared import Reasoning


def research_model_settings(
    *,
    model: str,
    reasoning_effort: str,
    parallel_tool_calls: bool,
    tool_choice: str | None = None,
    max_tokens: int | None = None,
) -> ModelSettings:
    """Build settings without sending unsupported GLM-5.3 thinking options.

    GLM-5.3's Coding Plan endpoint accepts ``low``, ``high`` and ``max`` and
    no longer permits disabled thinking.  Older models retain their provider
    defaults because their accepted effort vocabulary is different.
    """

    extra_body = None
    reasoning = None
    if _is_glm_53(model):
        extra_body = {
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
        }
        # Thinking does not guarantee a readable Responses reasoning item.
        # Request the provider-approved summary explicitly; encrypted reasoning
        # state remains excluded from observability.
        reasoning = Reasoning(summary="auto")
    elif _is_glm_52(model):
        # GLM-5.2 still supports non-thinking mode. Research uses it as the
        # explicit low-latency comparison baseline; do not leave this to a
        # provider default that may silently enable reasoning.
        extra_body = {"thinking": {"type": "disabled"}}
    return ModelSettings(
        parallel_tool_calls=parallel_tool_calls,
        include_usage=True,
        tool_choice=tool_choice,
        # OpenAI Responses interprets this as max_output_tokens. For reasoning
        # models the budget covers both hidden reasoning and visible output /
        # tool arguments, so every role must receive an explicit ceiling.
        max_tokens=max_tokens,
        reasoning=reasoning,
        extra_body=extra_body,
    )


def _is_glm_53(model: str) -> bool:
    normalized = str(model or "").strip().casefold().replace("_", "-")
    return normalized == "glm-5.3" or normalized.startswith("glm-5.3-")


def _is_glm_52(model: str) -> bool:
    normalized = str(model or "").strip().casefold().replace("_", "-")
    return normalized == "glm-5.2" or normalized.startswith("glm-5.2-")
