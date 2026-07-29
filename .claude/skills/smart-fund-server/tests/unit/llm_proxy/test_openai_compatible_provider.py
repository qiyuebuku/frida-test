import asyncio

from src.infrastructure.llm_proxy.providers.openai_compatible import (
    OpenAICompatibleProvider,
)
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMRouteDecision


def _route(
    model: str = "qwen3.7-plus",
    *,
    upstream_model: str | None = None,
) -> LLMRouteDecision:
    return LLMRouteDecision(
        requested_model=model,
        resolved_model=model,
        provider_candidates=["aliyun"],
        selected_provider="aliyun",
        route_reason="model_glob",
        upstream_model=upstream_model,
    )


def _provider(**kwargs) -> OpenAICompatibleProvider:
    options = {
        "name": "aliyun",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": "sk-test",
        "default_model": "qwen3.7-plus",
        "timeout": 1800,
        "model_patterns": ("qwen*", "kimi*", "glm-*"),
        "reasoning_style": "aliyun",
        "max_retries": 0,
    }
    options.update(kwargs)
    return OpenAICompatibleProvider(**options)


def test_aliyun_payload_uses_openai_compatible_chat_completion() -> None:
    provider = _provider()

    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="只回答 ok",
            system_prompt="你是助手",
            temperature=0,
            max_tokens=20,
        ),
        _route(),
    )

    assert payload == {
        "model": "qwen3.7-plus",
        "messages": [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "只回答 ok"},
        ],
        "stream": False,
        "temperature": 0,
        "max_tokens": 20,
    }


def test_aliyun_thinking_options_use_dashscope_parameter() -> None:
    provider = _provider(thinking_type="enabled")

    payload = provider._request_payload(
        LLMProxyRequest(prompt="推理", provider_options={"thinking_type": "disabled"}),
        _route(),
    )

    assert payload["enable_thinking"] is False
    assert "thinking" not in payload


def test_aliyun_usage_reads_nested_cached_tokens() -> None:
    usage = OpenAICompatibleProvider._normalized_usage(
        {
            "usage": {
                "prompt_tokens": 3019,
                "completion_tokens": 104,
                "total_tokens": 3123,
                "prompt_tokens_details": {"cached_tokens": 2048},
            }
        }
    )

    assert usage["prompt_cache_hit_tokens"] == 2048
    assert usage["prompt_cache_miss_tokens"] == 971
    assert usage["input_tokens"] == 3019
    assert usage["total_tokens"] == 3123


def test_aiclient2api_usage_combines_separate_fresh_and_cached_input() -> None:
    usage = OpenAICompatibleProvider._normalized_usage(
        {
            "usage": {
                "prompt_tokens": 554,
                "completion_tokens": 249,
                "total_tokens": 803,
                "prompt_cache_hit_tokens": 1664,
            }
        },
        cache_usage_style="separate",
    )

    assert usage["input_tokens"] == 2218
    assert usage["output_tokens"] == 249
    assert usage["total_tokens"] == 2467
    assert usage["prompt_cache_hit_tokens"] == 1664
    assert usage["prompt_cache_miss_tokens"] == 554


def test_aiclient2api_usage_counts_first_request_as_cache_miss() -> None:
    usage = OpenAICompatibleProvider._normalized_usage(
        {
            "usage": {
                "prompt_tokens": 2506,
                "completion_tokens": 554,
                "total_tokens": 3060,
                "prompt_cache_hit_tokens": 0,
            }
        },
        cache_usage_style="separate",
    )

    assert usage["input_tokens"] == 2506
    assert usage["total_tokens"] == 3060
    assert usage["prompt_cache_hit_tokens"] == 0
    assert usage["prompt_cache_miss_tokens"] == 2506


def test_aiclient2api_diagnostics_keep_raw_and_normalized_usage() -> None:
    provider = _provider(
        name="aiclient2api",
        base_url="http://127.0.0.1:3000/v1",
        reasoning_style="aiclient2api",
        cache_usage_style="separate",
    )
    provider_usage = {
        "prompt_tokens": 554,
        "completion_tokens": 249,
        "total_tokens": 803,
        "prompt_cache_hit_tokens": 1664,
    }

    diagnostics = provider._response_diagnostics(
        {
            "choices": [
                {
                    "message": {"content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": provider_usage,
        }
    )

    assert diagnostics["usage"] == {
        "input_tokens": 2218,
        "output_tokens": 249,
        "total_tokens": 2467,
        "prompt_cache_hit_tokens": 1664,
        "prompt_cache_miss_tokens": 554,
        "reasoning_tokens": 0,
    }
    assert diagnostics["provider_usage"] == provider_usage


def test_volcengine_thinking_options_use_ark_parameter() -> None:
    provider = _provider(
        name="volcengine",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        reasoning_style="volcengine",
        thinking_type="enabled",
    )

    payload = provider._request_payload(
        LLMProxyRequest(prompt="推理", provider_options={"thinking_type": "disabled"}),
        _route(),
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert "enable_thinking" not in payload


def test_aiclient2api_thinking_uses_anthropic_extra_body() -> None:
    provider = _provider(
        name="aiclient2api",
        base_url="http://127.0.0.1:3000/v1",
        reasoning_style="aiclient2api",
    )

    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="推理",
            provider_options={
                "thinking_type": "enabled",
                "reasoning_effort": "medium",
            },
        ),
        _route("glm-5.2"),
    )

    assert payload["extra_body"]["anthropic"]["thinking"] == {
        "type": "adaptive",
        "effort": "medium",
    }
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_aiclient2api_default_omits_thinking_options() -> None:
    provider = _provider(
        name="aiclient2api",
        base_url="http://127.0.0.1:3000/v1",
        reasoning_style="aiclient2api",
        thinking_type="",
        reasoning_effort="",
    )

    payload = provider._request_payload(
        LLMProxyRequest(prompt="使用模型默认思考模式"),
        _route("glm-5.2"),
    )

    assert "extra_body" not in payload
    assert "thinking" not in payload
    assert "enable_thinking" not in payload
    assert "reasoning_effort" not in payload


def test_aiclient2api_thinking_supports_explicit_budget() -> None:
    provider = _provider(
        name="aiclient2api",
        base_url="http://127.0.0.1:3000/v1",
        reasoning_style="aiclient2api",
    )

    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="推理",
            provider_options={
                "thinking_type": "enabled",
                "thinking_budget_tokens": 2048,
            },
        ),
        _route("glm-5.2"),
    )

    assert payload["extra_body"]["anthropic"]["thinking"] == {
        "type": "enabled",
        "budget_tokens": 2048,
    }


def test_aiclient2api_disabled_thinking_is_explicit() -> None:
    provider = _provider(
        name="aiclient2api",
        base_url="http://127.0.0.1:3000/v1",
        reasoning_style="aiclient2api",
    )

    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="推理",
            provider_options={"thinking_type": "disabled"},
        ),
        _route("glm-5.2"),
    )

    assert payload["extra_body"]["anthropic"]["thinking"] == {
        "type": "disabled",
    }


def test_provider_extra_body_cannot_override_routing_fields() -> None:
    provider = _provider()

    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="推理",
            provider_options={
                "extra_body": {
                    "clear_thinking": True,
                    "model": "attacker-model",
                    "messages": [],
                    "stream": True,
                }
            },
        ),
        _route(),
    )

    assert payload["clear_thinking"] is True
    assert payload["model"] == "qwen3.7-plus"
    assert payload["messages"]
    assert payload["stream"] is False


def test_generic_provider_does_not_use_deepseek_beta_prefix_endpoint(monkeypatch) -> None:
    provider = _provider()
    calls: list[dict] = []

    async def fake_post(payload, *, endpoint_url=None):
        calls.append({"payload": payload, "endpoint_url": endpoint_url})
        return {
            "model": payload["model"],
            "choices": [
                {
                    "message": {"content": '{"partial":'},
                    "finish_reason": "length",
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post", fake_post)
    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(prompt="返回 JSON", json_schema={"type": "object"}),
            _route(),
        )
    )

    assert len(calls) == 1
    assert calls[0]["endpoint_url"] is None
    assert response.proxy["provider"] == "aliyun"
    assert response.proxy["json_prefix_continuation_attempted"] is False


def test_provider_model_patterns_are_case_insensitive() -> None:
    provider = _provider()

    assert provider.supports("QWEN3.7-PLUS") is True
    assert provider.supports("kimi/kimi-k3") is True
    assert provider.supports("unknown-model") is False


def test_vendor_model_mapping_controls_upstream_model_id() -> None:
    provider = _provider(
        model_mappings={"deepseek-v4-pro": "vanchin/deepseek-v4-pro"}
    )
    route = _route(
        "deepseek-v4-pro",
        upstream_model=provider.resolve_model("deepseek-v4-pro"),
    )

    payload = provider._request_payload(LLMProxyRequest(prompt="hello"), route)

    assert provider.supports("deepseek-v4-pro") is True
    assert payload["model"] == "vanchin/deepseek-v4-pro"


def test_health_never_exposes_api_key() -> None:
    provider = _provider(api_key="sk-secret")

    health = provider.health()

    assert health["api_key_configured"] is True
    assert "sk-secret" not in str(health)


def test_string_configuration_is_normalized() -> None:
    provider = _provider(enabled="false", timeout="12.5", model_patterns="qwen*")

    assert provider.enabled is False
    assert provider.timeout == 12.5
    assert provider.model_patterns == ("qwen*",)
