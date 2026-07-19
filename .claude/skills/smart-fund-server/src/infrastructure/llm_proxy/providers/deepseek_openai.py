"""DeepSeek provider specialization for the OpenAI-compatible transport."""

from __future__ import annotations

from src.infrastructure.llm_proxy.providers.openai_compatible import (
    OpenAICompatibleProvider,
)


class DeepSeekOpenAIProvider(OpenAICompatibleProvider):
    """Keep DeepSeek-specific reasoning and JSON prefix continuation behavior."""

    def __init__(self, **kwargs):
        super().__init__(
            name="deepseek",
            model_patterns=("deepseek-*",),
            reasoning_style="deepseek",
            json_prefix_completion_enabled=True,
            **kwargs,
        )
