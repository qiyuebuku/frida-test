"""Role-specific model settings for the financial Research runtime."""

from __future__ import annotations

from agents import ModelSettings


def research_model_settings(
    *,
    model: str,
    reasoning_effort: str,
    parallel_tool_calls: bool,
    tool_choice: str | None = None,
) -> ModelSettings:
    """Build settings without sending unsupported GLM-5.3 thinking options.

    GLM-5.3's Coding Plan endpoint accepts ``low``, ``high`` and ``max`` and
    no longer permits disabled thinking.  Older models retain their provider
    defaults because their accepted effort vocabulary is different.
    """

    extra_body = None
    if _is_glm_53(model):
        extra_body = {
            "thinking": {"type": "enabled"},
            "reasoning_effort": reasoning_effort,
        }
    return ModelSettings(
        parallel_tool_calls=parallel_tool_calls,
        include_usage=True,
        tool_choice=tool_choice,
        extra_body=extra_body,
    )


def _is_glm_53(model: str) -> bool:
    normalized = str(model or "").strip().casefold().replace("_", "-")
    return normalized == "glm-5.3" or normalized.startswith("glm-5.3-")
