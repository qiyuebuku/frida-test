from __future__ import annotations

from src.infrastructure.llm_proxy.service import (
    _llm_usage_details,
    _llm_usage_metadata,
)


def test_langfuse_usage_uses_only_canonical_dimensions() -> None:
    usage = {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    }

    assert _llm_usage_details(usage) == {
        "input": 100,
        "output": 20,
        "total": 120,
    }
    assert _llm_usage_metadata(usage) == {
        "logical_input_tokens": 100,
        "logical_output_tokens": 20,
        "prompt_cache_hit_tokens": 80,
        "prompt_cache_miss_tokens": 20,
    }


def test_langfuse_usage_exposes_reasoning_estimate_as_metadata_only() -> None:
    usage = {
        "input_tokens": 100,
        "output_tokens": 80,
        "reasoning_tokens": 0,
        "reasoning_tokens_estimated": 67,
    }

    assert _llm_usage_details(usage) == {"input": 100, "output": 80, "total": 180}
    assert _llm_usage_metadata(usage)["reasoning_tokens_reported"] == 0
    assert _llm_usage_metadata(usage)["reasoning_tokens_estimated"] == 67
