"""公共 LLM Gateway 调用日志测试。"""

from __future__ import annotations

from decimal import Decimal

from src.infrastructure.llm_proxy.audit import build_llm_call_log, new_audit_context
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMProxyResponse


def _response(*, cache_hit: bool = False) -> LLMProxyResponse:
    return LLMProxyResponse(
        text='{"result":"ok"}',
        structured_output={"result": "ok"},
        usage={
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "reasoning_tokens": 5,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
            "input_cost_usd": "0.001",
            "output_cost_usd": "0.002",
        },
        session_id="session-1",
        duration_ms=123,
        raw_payload={"id": "upstream-1"},
        cache_hit=cache_hit,
        proxy={"provider": "deepseek", "upstream_model": "deepseek-chat"},
        reasoning_content="先核对证据，再生成结构化结果。",
    )


def test_build_llm_call_log_preserves_payload_usage_and_provider_cost() -> None:
    request = LLMProxyRequest(
        prompt="分析输入",
        system_prompt="系统约束",
        model="deepseek-pro",
        metadata={"task": "kg_test", "source_type": "news", "source_id": "ft_news:1"},
        provider_options={"reasoning_effort": "high", "authorization": "secret"},
    )

    row = build_llm_call_log(
        context=new_audit_context("hash-1"),
        request=request,
        provider="deepseek",
        resolved_model="deepseek-pro",
        route_reason="exact",
        response=_response(),
    )

    assert row["status"] == "succeeded"
    assert row["task"] == "kg_test"
    assert row["request_payload"]["prompt"] == "分析输入"
    assert row["request_payload"]["provider_options"]["authorization"] == "[REDACTED]"
    assert row["response_payload"]["structured_output"] == {"result": "ok"}
    assert row["response_payload"]["reasoning_content"] == "先核对证据，再生成结构化结果。"
    assert row["reasoning_content"] == "先核对证据，再生成结构化结果。"
    assert row["input_tokens"] == 100
    assert row["reasoning_tokens"] == 5
    assert row["total_cost"] == Decimal("0.003")
    assert row["cost_source"] == "provider_reported"
    assert row["currency"] == "USD"


def test_local_cache_hit_has_zero_billed_usage_and_cost() -> None:
    response = _response(cache_hit=True)
    row = build_llm_call_log(
        context=new_audit_context("hash-2"),
        request=LLMProxyRequest(prompt="same"),
        provider="deepseek",
        resolved_model="deepseek-pro",
        route_reason="exact",
        response=response,
        cache_hit=True,
        cache_store="memory",
    )

    assert row["status"] == "cache_hit"
    assert row["usage"]["input_tokens"] == 100
    assert row["input_tokens"] == 0
    assert row["total_tokens"] == 0
    assert row["total_cost"] == Decimal("0")
    assert row["cost_source"] == "local_cache"
    assert row["cache_store"] == "memory"


def test_failed_call_records_request_and_error_without_response() -> None:
    row = build_llm_call_log(
        context=new_audit_context("hash-3"),
        request=LLMProxyRequest(prompt="failed", metadata={"task": "failure_case"}),
        provider="deepseek",
        resolved_model="deepseek-pro",
        route_reason="exact",
        error=TimeoutError("upstream timeout"),
    )

    assert row["status"] == "failed"
    assert row["response_payload"] == {}
    assert row["error_type"] == "TimeoutError"
    assert row["error_message"] == "upstream timeout"
    assert row["cost_source"] == "unavailable"
