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


def _provider(api_key="sk-test", rate_limit_cooldown_seconds=1, **kwargs):
    options = {
        "max_retries": 0,
        "initial_retry_delay_seconds": 0,
        "max_retry_delay_seconds": 0,
    }
    options.update(kwargs)
    return DeepSeekOpenAIProvider(
        base_url="https://api.deepseek.com",
        api_key=api_key,
        default_model="deepseek-v4-flash",
        timeout=30,
        rate_limit_cooldown_seconds=rate_limit_cooldown_seconds,
        **options,
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


def test_deepseek_request_provider_options_override_reasoning_effort():
    provider = _provider(reasoning_effort="medium")
    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="快速分类",
            provider_options={"reasoning_effort": "low"},
        ),
        _route(),
    )

    assert payload["reasoning_effort"] == "low"


def test_deepseek_request_provider_options_disable_thinking_suppresses_reasoning_effort():
    provider = _provider(reasoning_effort="high")
    payload = provider._request_payload(
        LLMProxyRequest(
            prompt="快速分类",
            provider_options={"thinking_type": "disabled"},
        ),
        _route(),
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload


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
            "choices": [
                {
                    "message": {
                        "content": '{"ok": true}',
                        "reasoning_content": "先检查 JSON 约束。",
                    },
                    "finish_reason": "stop",
                }
            ],
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
    assert response.reasoning_content == "先检查 JSON 约束。"
    assert response.raw_payload["reasoning_stages"] == [
        {"stage": "initial", "content": "先检查 JSON 约束。"}
    ]
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
    assert calls[1]["messages"][0]["role"] == "system"
    assert "严格的 JSON 修复器" in calls[1]["messages"][0]["content"]
    assert "ok=true, reason=done" in calls[1]["messages"][1]["content"]
    assert calls[1]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in calls[1]
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


def test_deepseek_continues_empty_json_with_previous_reasoning_and_assistant_prefix(monkeypatch):
    provider = _provider()
    calls = []

    async def fake_post(payload, *, endpoint_url=None):
        calls.append((payload, endpoint_url))
        if len(calls) == 1:
            return {
                "id": "chat-1",
                "model": payload["model"],
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "已完成事实边界判断，准备输出 JSON。",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 3,
                    "total_tokens": 5,
                    "prompt_cache_hit_tokens": 1,
                    "prompt_cache_miss_tokens": 1,
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            }
        return {
            "id": "chat-2",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": '{"ok": true, "reason": "done"}',
                        "reasoning_content": "沿用上一轮结论，直接完成输出。",
                    },
                    "finish_reason": "stop",
                }
            ],
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

    assert len(calls) == 2
    initial_payload, initial_endpoint = calls[0]
    continuation_payload, continuation_endpoint = calls[1]
    assert initial_endpoint is None
    assert initial_payload["response_format"] == {"type": "json_object"}
    assert continuation_endpoint == "https://api.deepseek.com/beta/chat/completions"
    assert "response_format" not in continuation_payload
    assert continuation_payload["messages"][:-1] == initial_payload["messages"]
    assert continuation_payload["messages"][-1] == {
        "role": "assistant",
        "reasoning_content": "已完成事实边界判断，准备输出 JSON。",
        "content": "",
        "prefix": True,
    }
    assert response.structured_output == {"ok": True, "reason": "done"}
    assert response.usage == {
        "input_tokens": 7,
        "output_tokens": 9,
        "total_tokens": 16,
        "prompt_cache_hit_tokens": 4,
        "prompt_cache_miss_tokens": 3,
        "reasoning_tokens": 4,
    }
    assert response.proxy["json_prefix_continuation_attempted"] is True
    assert response.proxy["json_prefix_continuation_success"] is True
    assert response.proxy["json_repair_attempted"] is False
    assert response.proxy["json_mode_initial_finish_reason"] == "stop"
    assert response.proxy["json_prefix_continuation_finish_reason"] == "stop"
    assert response.proxy["json_mode_initial_usage"] == {
        "input_tokens": 2,
        "output_tokens": 3,
        "total_tokens": 5,
        "prompt_cache_hit_tokens": 1,
        "prompt_cache_miss_tokens": 1,
        "reasoning_tokens": 3,
    }
    assert response.raw_payload["json_mode_initial"]["id"] == "chat-1"
    assert response.raw_payload["json_mode_initial"]["usage"] == response.proxy["json_mode_initial_usage"]
    assert response.raw_payload["json_mode_initial"]["content_chars"] == 0
    continuation = response.raw_payload["json_prefix_continuation"]
    assert continuation["id"] == "chat-2"
    assert continuation["prefix_content_chars"] == 0
    assert continuation["prefix_reasoning_chars"] == len("已完成事实边界判断，准备输出 JSON。")
    assert "[initial]" in response.reasoning_content
    assert "[json_prefix_continuation]" in response.reasoning_content


def test_deepseek_continues_partial_json_instead_of_restarting(monkeypatch):
    provider = _provider()
    calls = []

    async def fake_post(payload, *, endpoint_url=None):
        calls.append((payload, endpoint_url))
        if len(calls) == 1:
            return {
                "id": "chat-1",
                "model": payload["model"],
                "choices": [
                    {
                        "message": {
                            "content": '{"cards":[{"id":1}',
                            "reasoning_content": "已经确定一张 Card。",
                        },
                        "finish_reason": "length",
                    }
                ],
                "usage": {},
            }
        return {
            "id": "chat-2",
            "model": payload["model"],
            "choices": [
                {
                    "message": {"content": '],"relations":[]}'},
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

    assert len(calls) == 2
    continuation_payload, continuation_endpoint = calls[1]
    assert continuation_endpoint == "https://api.deepseek.com/beta/chat/completions"
    assert continuation_payload["messages"][-1] == {
        "role": "assistant",
        "reasoning_content": "已经确定一张 Card。",
        "content": '{"cards":[{"id":1}',
        "prefix": True,
    }
    assert response.structured_output == {"cards": [{"id": 1}], "relations": []}
    assert response.proxy["json_prefix_continuation_success"] is True
    assert response.proxy["json_repair_attempted"] is False


def test_deepseek_does_not_repair_when_prefix_continuation_is_still_truncated(monkeypatch):
    provider = _provider()
    calls = []

    async def fake_post(payload, *, endpoint_url=None):
        calls.append((payload, endpoint_url))
        content = '{"cards":[' if len(calls) == 1 else '{"id":1}'
        return {
            "id": f"chat-{len(calls)}",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "content": content,
                        "reasoning_content": "尚未完成",
                    },
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

    assert len(calls) == 2
    assert response.structured_output is None
    assert response.text == '{"cards":[{"id":1}'
    assert response.proxy["json_prefix_continuation_success"] is False
    assert response.proxy["json_repair_attempted"] is False


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


def test_deepseek_500_retries_with_exponential_backoff(monkeypatch):
    provider = _provider(max_retries=10, initial_retry_delay_seconds=1, max_retry_delay_seconds=60)
    calls = 0
    sleeps = []

    async def fake_post(payload):
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise LLMProxyError("temporary", error_type="upstream_unavailable")
        return {
            "model": payload["model"],
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(provider, "_post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    response = asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    assert calls == 4
    assert response.text == "ok"
    assert response.proxy["retry_count"] == 3
    assert sleeps == [1, 2, 4]
    assert response.proxy["upstream_retry_events"][0]["purpose"] == "initial"
    assert response.proxy["upstream_max_retries"] == 10
    assert response.proxy["upstream_max_retry_delay_seconds"] == 60


def test_deepseek_retries_are_capped_at_ten(monkeypatch):
    provider = _provider(max_retries=10, initial_retry_delay_seconds=1, max_retry_delay_seconds=60)
    calls = 0
    sleeps = []

    async def fake_post(payload):
        nonlocal calls
        calls += 1
        raise LLMProxyError("busy", error_type="upstream_unavailable")

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(provider, "_post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(LLMProxyError):
        asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    assert calls == 11
    assert sleeps == [1, 2, 4, 8, 16, 32, 60, 60, 60, 60]


def test_deepseek_429_records_cooldown(monkeypatch):
    provider = _provider()

    async def fake_post(payload):
        raise LLMProxyError("rate", error_type="rate_limited")

    monkeypatch.setattr(provider, "_post", fake_post)

    with pytest.raises(LLMProxyError):
        asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    assert provider._cooldown_until > 0


def test_deepseek_429_retry_wait_respects_cooldown_cap(monkeypatch):
    provider = _provider(
        rate_limit_cooldown_seconds=120,
        max_retries=1,
        initial_retry_delay_seconds=1,
        max_retry_delay_seconds=60,
    )
    calls = 0
    sleeps = []

    async def fake_post(payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMProxyError("rate", error_type="rate_limited")
        return {
            "model": payload["model"],
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {},
        }

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(provider, "_post", fake_post)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    response = asyncio.run(provider.generate(LLMProxyRequest(prompt="hello"), _route()))

    assert response.text == "ok"
    assert len(sleeps) == 1
    assert 59 <= sleeps[0] <= 60


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


def test_normalized_usage_preserves_provider_cost_fields() -> None:
    usage = DeepSeekOpenAIProvider._normalized_usage(
        {
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "total_cost_usd": "0.0015",
                "currency": "USD",
            }
        }
    )

    assert usage["total_cost_usd"] == "0.0015"
    assert usage["currency"] == "USD"
