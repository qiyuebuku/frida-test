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


def test_deepseek_injects_schema_even_when_prompt_mentions_json_schema():
    provider = _provider()
    schema = {
        "type": "object",
        "properties": {
            "assignments": {"type": "array"},
            "new_communities": {"type": "array"},
        },
        "required": ["assignments", "new_communities"],
        "additionalProperties": False,
    }
    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="返回归档裁决",
            system_prompt="输出必须符合 JSON Schema，不要 Markdown。",
            json_schema=schema,
        ),
        _route(),
    )

    system_prompt = payload["messages"][0]["content"]
    assert "__LLM_PROXY_JSON_SCHEMA_INSTRUCTION__" in system_prompt
    assert '"assignments"' in system_prompt
    assert '"new_communities"' in system_prompt
    assert '"required"' in system_prompt


def test_deepseek_json_object_mode_injects_json_instruction_for_messages():
    provider = _provider()
    payload = provider._request_payload(
        LLMProxyRequest(
            messages=[{"role": "user", "content": "返回一个对象"}],
            response_format={"type": "json_object"},
        ),
        _route(),
    )

    assert payload["response_format"] == {"type": "json_object"}
    assert payload["messages"][0]["role"] == "system"
    assert "JSON" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "返回一个对象"}


def test_deepseek_request_body_supports_tool_calls():
    provider = _provider()
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_date",
                "description": "Return current date",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    payload = provider._request_payload(
        LLMProxyRequest(prompt="今天几号", tools=tools, tool_choice="auto"),
        _route(),
    )

    assert payload["tools"] == tools
    assert payload["tool_choice"] == "auto"


def test_deepseek_usage_normalized(monkeypatch):
    provider = _provider()

    async def fake_post(payload):
        return {
            "id": "chat-1",
            "model": payload["model"],
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 3,
                "completion_tokens": 4,
                "total_tokens": 7,
                "prompt_cache_hit_tokens": 2,
                "prompt_cache_miss_tokens": 1,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(prompt="返回 JSON", json_schema={"type": "object"}),
            _route(),
        )
    )

    assert response.usage == {
        "input_tokens": 3,
        "output_tokens": 4,
        "total_tokens": 7,
        "prompt_cache_hit_tokens": 2,
        "prompt_cache_miss_tokens": 1,
        "reasoning_tokens": 0,
    }
    assert response.structured_output == {"ok": True}
    assert response.proxy["provider"] == "deepseek"


def test_deepseek_response_preserves_tool_calls(monkeypatch):
    provider = _provider()
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_date", "arguments": "{}"},
        }
    ]

    async def fake_post(payload):
        return {
            "id": "chat-1",
            "model": payload["model"],
            "choices": [
                {
                    "message": {"content": "", "tool_calls": tool_calls},
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = asyncio.run(
        provider.generate(LLMProxyRequest(prompt="今天几号", tools=[]), _route())
    )

    assert response.raw_payload["finish_reason"] == "tool_calls"
    assert response.raw_payload["tool_calls"] == tool_calls
    assert response.raw_payload["message"]["tool_calls"] == tool_calls


def test_deepseek_extracts_json_from_prose_and_markdown(monkeypatch):
    provider = _provider()

    async def fake_post(payload):
        return {
            "id": "chat-1",
            "model": payload["model"],
            "choices": [
                {
                    "message": {"content": '结果如下：\n```json\n{"ok": true}\n```'},
                    "finish_reason": "stop",
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

    assert response.structured_output == {"ok": True}
    assert response.text == '{"ok": true}'


def test_deepseek_repairs_unparseable_json_with_second_llm_call(monkeypatch):
    provider = _provider()
    calls = []

    async def fake_post(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {
                "id": "chat-1",
                "model": payload["model"],
                "choices": [{"message": {"content": "ok=true, reason=done"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            }
        return {
            "id": "chat-2",
            "model": payload["model"],
            "choices": [{"message": {"content": '{"ok": true, "reason": "done"}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11},
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(prompt="返回 JSON", json_schema={"type": "object"}),
            _route(),
        )
    )

    assert len(calls) == 2
    assert calls[1]["response_format"] == {"type": "json_object"}
    assert calls[1]["messages"][0] == calls[0]["messages"][0]
    assert calls[1]["messages"][1] == calls[0]["messages"][1]
    assert calls[1]["messages"][2] == {"role": "assistant", "content": "ok=true, reason=done"}
    assert response.structured_output == {"ok": True, "reason": "done"}
    assert response.usage == {
        "input_tokens": 8,
        "output_tokens": 10,
        "total_tokens": 18,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
        "reasoning_tokens": 0,
    }
    assert response.proxy["json_repair_attempted"] is True
    assert response.proxy["json_repair_success"] is True
    assert response.raw_payload["json_repair"]["id"] == "chat-2"


def test_deepseek_retries_empty_json_mode_without_forced_json_then_repairs(monkeypatch):
    provider = _provider()
    calls = []

    async def fake_post(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {
                "id": "chat-1",
                "model": payload["model"],
                "choices": [{"message": {"content": ""}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 0,
                    "total_tokens": 2,
                    "prompt_cache_hit_tokens": 1,
                    "prompt_cache_miss_tokens": 1,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            }
        if len(calls) == 2:
            return {
                "id": "chat-2",
                "model": payload["model"],
                "choices": [
                    {
                        "message": {
                            "content": "ok=true, reason=done",
                            "reasoning_content": "reasoned",
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 4,
                    "total_tokens": 7,
                    "prompt_cache_hit_tokens": 2,
                    "prompt_cache_miss_tokens": 1,
                    "completion_tokens_details": {"reasoning_tokens": 2},
                },
            }
        return {
            "id": "chat-3",
            "model": payload["model"],
            "choices": [{"message": {"content": '{"ok": true, "reason": "done"}'}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 6,
                "total_tokens": 11,
                "prompt_cache_hit_tokens": 3,
                "prompt_cache_miss_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": 1},
            },
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(prompt="返回 JSON", json_schema={"type": "object"}),
            _route(),
        )
    )

    assert len(calls) == 3
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]
    assert calls[1]["messages"][0] == calls[0]["messages"][0]
    assert calls[1]["messages"][1] == calls[0]["messages"][1]
    assert "JSON Schema" in str(calls[1]["messages"])
    assert calls[2]["response_format"] == {"type": "json_object"}
    assert calls[2]["messages"][0] == calls[0]["messages"][0]
    assert calls[2]["messages"][1] == calls[0]["messages"][1]
    assert calls[2]["messages"][2] == {"role": "assistant", "content": "ok=true, reason=done"}
    assert response.structured_output == {"ok": True, "reason": "done"}
    assert response.usage == {
        "input_tokens": 10,
        "output_tokens": 10,
        "total_tokens": 20,
        "prompt_cache_hit_tokens": 6,
        "prompt_cache_miss_tokens": 4,
        "reasoning_tokens": 3,
    }
    assert response.proxy["json_mode_retry_attempted"] is True
    assert response.proxy["json_mode_retry_success"] is True
    assert response.proxy["json_repair_attempted"] is True
    assert response.proxy["json_repair_success"] is True
    assert response.proxy["json_mode_initial_finish_reason"] == "stop"
    assert response.proxy["json_mode_retry_finish_reason"] == "length"
    assert response.proxy["json_mode_initial_usage"] == {
        "input_tokens": 2,
        "output_tokens": 0,
        "total_tokens": 2,
        "prompt_cache_hit_tokens": 1,
        "prompt_cache_miss_tokens": 1,
        "reasoning_tokens": 0,
    }
    assert response.proxy["json_mode_retry_usage"] == {
        "input_tokens": 3,
        "output_tokens": 4,
        "total_tokens": 7,
        "prompt_cache_hit_tokens": 2,
        "prompt_cache_miss_tokens": 1,
        "reasoning_tokens": 2,
    }
    assert response.raw_payload["json_mode_initial"]["id"] == "chat-1"
    assert response.raw_payload["json_mode_initial"]["usage"] == response.proxy["json_mode_initial_usage"]
    assert response.raw_payload["json_mode_initial"]["content_chars"] == 0
    assert response.raw_payload["json_mode_retry"]["id"] == "chat-2"
    assert response.raw_payload["json_mode_retry"]["finish_reason"] == "length"
    assert response.raw_payload["json_mode_retry"]["usage"] == response.proxy["json_mode_retry_usage"]
    assert response.raw_payload["json_mode_retry"]["content_chars"] == len("ok=true, reason=done")
    assert response.raw_payload["json_mode_retry"]["reasoning_chars"] == len("reasoned")
    assert response.raw_payload["json_repair"]["id"] == "chat-3"


def test_deepseek_keeps_original_text_when_json_repair_fails(monkeypatch):
    provider = _provider()

    async def fake_post(payload):
        return {
            "id": "chat-1",
            "model": payload["model"],
            "choices": [{"message": {"content": "still not json"}, "finish_reason": "stop"}],
            "usage": {},
        }

    monkeypatch.setattr(provider, "_post", fake_post)

    response = asyncio.run(
        provider.generate(
            LLMProxyRequest(prompt="返回 JSON", json_schema={"type": "object"}),
            _route(),
        )
    )

    assert response.structured_output is None
    assert response.text == "still not json"
    assert response.proxy["json_repair_attempted"] is True
    assert response.proxy["json_repair_success"] is False
    assert response.proxy["json_repair_error"] == "repair response not parseable as JSON"


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


def test_deepseek_unexpected_transport_error_is_wrapped(monkeypatch):
    provider = _provider(api_key="sk-secret")

    async def fake_post(self, url, **kwargs):
        raise ValueError("second argument (exceptions) must be a non-empty sequence")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(LLMProxyError) as exc:
        asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    message = str(exc.value)
    assert exc.value.error_type == "upstream_unavailable"
    assert "ValueError" in message
    assert "sk-secret" not in message
