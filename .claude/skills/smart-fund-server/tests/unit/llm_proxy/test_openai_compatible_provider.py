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
