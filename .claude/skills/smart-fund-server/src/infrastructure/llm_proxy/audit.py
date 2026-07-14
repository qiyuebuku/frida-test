"""公共 LLM Gateway 调用审计材料构建与持久化。"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMProxyResponse


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


class LLMCallAuditRecorder(Protocol):
    def save_batch(self, rows: list[dict]) -> int:
        """保存一批逻辑模型调用。"""


@dataclass(frozen=True)
class LLMCallAuditContext:
    id: str
    started_at: datetime
    request_hash: str


def new_audit_context(request_hash: str) -> LLMCallAuditContext:
    return LLMCallAuditContext(
        id=str(uuid.uuid4()),
        started_at=datetime.now(timezone.utc),
        request_hash=request_hash,
    )


def build_llm_call_log(
    *,
    context: LLMCallAuditContext,
    request: LLMProxyRequest,
    provider: str | None,
    resolved_model: str | None,
    route_reason: str | None,
    response: LLMProxyResponse | None = None,
    error: Exception | None = None,
    cache_hit: bool = False,
    cache_store: str | None = None,
) -> dict[str, Any]:
    """把一次 Gateway 调用转换为可直接写入 ORM 的行。"""

    completed_at = datetime.now(timezone.utc)
    metadata = request.metadata or {}
    usage = _json_safe(response.usage if response is not None else {})
    proxy = response.proxy if response is not None else {}
    actual_cache_hit = bool(cache_hit or (response.cache_hit if response is not None else False))
    costs = _extract_costs(usage, proxy, response.raw_payload if response is not None else {})
    if actual_cache_hit:
        costs = {
            "input_cost": Decimal("0"),
            "output_cost": Decimal("0"),
            "cache_cost": Decimal("0"),
            "total_cost": Decimal("0"),
            "currency": costs.get("currency") or "USD",
            "cost_source": "local_cache",
            "cost_details": costs.get("cost_details") or {},
        }
    billed_usage = {} if actual_cache_hit else usage
    status = "failed" if error is not None else ("cache_hit" if actual_cache_hit else "succeeded")
    return {
        "id": context.id,
        "task": _optional_text(metadata.get("task") or metadata.get("operation"), 128),
        "source_type": _optional_text(metadata.get("source_type"), 64),
        "source_id": _optional_text(metadata.get("source_id"), 256),
        "provider": _optional_text(provider or proxy.get("provider"), 64),
        "requested_model": _optional_text(request.model or proxy.get("requested_model"), 128),
        "resolved_model": _optional_text(resolved_model or proxy.get("resolved_model"), 128),
        "upstream_model": _optional_text(proxy.get("upstream_model"), 128),
        "route_reason": _optional_text(route_reason or proxy.get("route_reason"), 64),
        "status": status,
        "cache_hit": actual_cache_hit,
        "cache_store": _optional_text(cache_store or proxy.get("cache_store"), 32),
        "request_hash": context.request_hash,
        "request_payload": _request_payload(request),
        "response_payload": _response_payload(response),
        "usage": usage,
        "input_tokens": _usage_int(billed_usage, "input_tokens"),
        "output_tokens": _usage_int(billed_usage, "output_tokens"),
        "total_tokens": _usage_int(billed_usage, "total_tokens"),
        "reasoning_tokens": _usage_int(billed_usage, "reasoning_tokens"),
        "prompt_cache_hit_tokens": _usage_int(billed_usage, "prompt_cache_hit_tokens"),
        "prompt_cache_miss_tokens": _usage_int(billed_usage, "prompt_cache_miss_tokens"),
        "cache_creation_tokens": _usage_int(billed_usage, "cache_creation_tokens"),
        "cache_read_tokens": _usage_int(billed_usage, "cache_read_tokens"),
        "input_cost": costs.get("input_cost"),
        "output_cost": costs.get("output_cost"),
        "cache_cost": costs.get("cache_cost"),
        "total_cost": costs.get("total_cost"),
        "currency": costs.get("currency"),
        "cost_source": costs.get("cost_source"),
        "cost_details": costs.get("cost_details") or {},
        "duration_ms": max(
            0,
            int(
                (completed_at - context.started_at).total_seconds() * 1000
                if response is None or actual_cache_hit
                else response.duration_ms
            ),
        ),
        "session_id": _optional_text(response.session_id if response is not None else None, 128),
        "error_type": _optional_text(error.__class__.__name__ if error is not None else None, 128),
        "error_message": str(error) if error is not None else None,
        "started_at": context.started_at,
        "completed_at": completed_at,
    }


def _request_payload(request: LLMProxyRequest) -> dict[str, Any]:
    return _sanitize(
        {
            "prompt": request.prompt,
            "system_prompt": request.system_prompt,
            "messages": request.messages,
            "model": request.model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "json_schema": request.json_schema,
            "response_format": request.response_format,
            "tools": request.tools,
            "tool_choice": request.tool_choice,
            "metadata": request.metadata,
            "provider_options": request.provider_options,
            "timeout": request.timeout,
            "use_cache": request.use_cache,
        }
    )


def _response_payload(response: LLMProxyResponse | None) -> dict[str, Any]:
    if response is None:
        return {}
    return _sanitize(
        {
            "text": response.text,
            "structured_output": response.structured_output,
            "session_id": response.session_id,
            "duration_ms": response.duration_ms,
            "raw_payload": response.raw_payload,
            "proxy": response.proxy,
            "cache_hit": response.cache_hit,
        }
    )


def _extract_costs(usage: dict[str, Any], proxy: dict[str, Any], raw_payload: dict[str, Any]) -> dict[str, Any]:
    cost_details = _collect_cost_values(
        {"usage": usage, "proxy": proxy, "raw_payload": raw_payload}
    )
    input_cost = _first_decimal(cost_details, "input_cost", "input_cost_usd", "prompt_cost", "prompt_cost_usd")
    output_cost = _first_decimal(
        cost_details,
        "output_cost",
        "output_cost_usd",
        "completion_cost",
        "completion_cost_usd",
    )
    cache_cost = _first_decimal(cost_details, "cache_cost", "cache_cost_usd")
    total_cost = _first_decimal(cost_details, "total_cost", "total_cost_usd", "cost", "cost_usd")
    components = [item for item in (input_cost, output_cost, cache_cost) if item is not None]
    if total_cost is None and components:
        total_cost = sum(components, Decimal("0"))
    has_cost = total_cost is not None or bool(components)
    currency = _find_text_value(
        {"usage": usage, "proxy": proxy, "raw_payload": raw_payload},
        {"currency", "cost_currency"},
    )
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "cache_cost": cache_cost,
        "total_cost": total_cost,
        "currency": currency or ("USD" if has_cost else None),
        "cost_source": "provider_reported" if has_cost else "unavailable",
        "cost_details": _json_safe(cost_details),
    }


def _collect_cost_values(value: Any, *, path: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if "cost" in str(key).lower() and isinstance(item, (int, float, Decimal, str)):
                result[child_path] = item
            result.update(_collect_cost_values(item, path=child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(_collect_cost_values(item, path=f"{path}[{index}]"))
    return result


def _first_decimal(values: dict[str, Any], *names: str) -> Decimal | None:
    expected = {name.lower() for name in names}
    for path, value in values.items():
        if path.rsplit(".", 1)[-1].lower() not in expected:
            continue
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return None


def _find_text_value(value: Any, names: set[str]) -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in names and item:
                return str(item)
            nested = _find_text_value(item, names)
            if nested:
                return nested
    elif isinstance(value, list):
        for item in value:
            nested = _find_text_value(item, names)
            if nested:
                return nested
    return None


def _sanitize(value: Any, *, key: str = "") -> Any:
    if key and any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): _sanitize(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    return _json_safe(value)


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


def _usage_int(usage: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(usage.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _optional_text(value: Any, max_length: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    return str(value)[:max_length]
