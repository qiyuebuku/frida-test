import asyncio

import httpx
import pytest

from src.infrastructure.llm_proxy.providers.deepseek_openai import DeepSeekOpenAIProvider
from src.infrastructure.llm_proxy.types import LLMProxyError, LLMProxyRequest, LLMRouteDecision


def _route(model="deepseek-v4-flash"):
    return LLMRouteDecision(
        requested_model=model,
        resolved_model=model,
        provider_candidates=["deepseek"],
        selected_provider="deepseek",
        route_reason="model_exact",
    )


def _provider(api_key="sk-test"):
    return DeepSeekOpenAIProvider(
        base_url="https://api.deepseek.com",
        api_key=api_key,
        default_model="deepseek-v4-flash",
        timeout=30,
        rate_limit_cooldown_seconds=1,
    )


def test_deepseek_request_body_chat_completion():
    provider = _provider()
    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="返回 JSON",
            system_prompt="你是助手",
            temperature=0,
            max_tokens=100,
        ),
        _route(),
    )

    assert payload["model"] == "deepseek-v4-flash"
    assert payload["messages"][0] == {"role": "system", "content": "你是助手"}
    assert payload["messages"][1]["content"] == "返回 JSON"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 100


def test_deepseek_json_object_response_format():
    provider = _provider()
    payload = provider._request_payload(
        LLMProxyRequest(prompt="返回 JSON", json_schema={"type": "object"}),
        _route(),
    )

    assert payload["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in payload["messages"][0]["content"]


def test_deepseek_usage_normalized(monkeypatch):
    provider = _provider()

    async def fake_post(payload):
        return {
            "id": "chat-1",
            "model": payload["model"],
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(prompt="返回 JSON", json_schema={"type": "object"}),
            _route(),
        )
    )

    assert response.usage == {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}
    assert response.structured_output == {"ok": True}
    assert response.proxy["provider"] == "deepseek"


def test_deepseek_missing_api_key_raises_without_leaking_key():
    provider = _provider(api_key="")

    with pytest.raises(LLMProxyError) as exc:
        asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    assert exc.value.error_type == "auth_error"
    assert "sk-" not in str(exc.value)


def test_deepseek_500_retries_once(monkeypatch):
    provider = _provider()
    calls = 0

    async def fake_post(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMProxyError("temporary", error_type="upstream_unavailable")
        return {
            "model": payload["model"],
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    assert calls == 2
    assert response.text == "ok"
    assert response.proxy["retry_count"] == 1


def test_deepseek_429_records_cooldown(monkeypatch):
    provider = _provider()

    async def fake_post(payload):
        raise LLMProxyError("rate", error_type="rate_limited")

    monkeypatch.setattr(provider, "_post", fake_post)

    with pytest.raises(LLMProxyError):
        asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    assert provider._cooldown_until > 0


def test_deepseek_http_error_message_contains_safe_exception_type(monkeypatch):
    provider = _provider(api_key="sk-secret")

    async def fake_post(self, url, **kwargs):
        raise httpx.ConnectError("")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(LLMProxyError) as exc:
        asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    message = str(exc.value)
    assert exc.value.error_type == "upstream_unavailable"
    assert "ConnectError" in message
    assert "sk-secret" not in message
