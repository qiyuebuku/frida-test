"""DeepSeek OpenAI-compatible provider."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import httpx

from src.infrastructure.llm_proxy.types import (
    LLMProxyError,
    LLMProxyRequest,
    LLMProxyResponse,
    LLMRouteDecision,
)

JSON_SCHEMA_INSTRUCTION_MARKER = "__LLM_PROXY_JSON_SCHEMA_INSTRUCTION__"


class DeepSeekOpenAIProvider:
    name = "deepseek"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout: float,
        enabled: bool = True,
        max_concurrency: int = 5,
        rate_limit_cooldown_seconds: float = 60,
        thinking_type: str | None = None,
        reasoning_effort: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout = timeout
        self.enabled = enabled
        self.max_concurrency = max(1, int(max_concurrency or 1))
        self.rate_limit_cooldown_seconds = max(0.0, float(rate_limit_cooldown_seconds or 0))
        self._sem = asyncio.Semaphore(self.max_concurrency)
        self._cooldown_until = 0.0
        self.thinking_type = thinking_type
        self.reasoning_effort = reasoning_effort

    async def generate(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
    ) -> LLMProxyResponse:
        async with self._sem:
            await self._wait_for_cooldown()
            if not self.api_key:
                raise LLMProxyError("DeepSeek API key 未配置", error_type="auth_error")

            payload = self._request_payload(request, route)
            original_messages = list(payload.get("messages") or [])
            started_at = time.perf_counter()
            retry_count = 0
            json_mode_retry_data: dict[str, Any] | None = None
            json_mode_retry_error: str | None = None

            try:
                data = await self._post(payload)
            except LLMProxyError as exc:
                if exc.error_type == "rate_limited":
                    self._record_cooldown()
                if exc.error_type not in {"upstream_unavailable", "timeout"}:
                    raise
                retry_count = 1
                data = await self._post(payload)
            primary_data = data

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            tool_calls = message.get("tool_calls")
            reasoning_content = message.get("reasoning_content")
            structured_output = self._try_parse_json(text, request)
            repair_data: dict[str, Any] | None = None
            repair_error: str | None = None
            if (
                structured_output is None
                and self._expects_json(request)
                and not text.strip()
                and not tool_calls
            ):
                try:
                    json_mode_retry_data = await self._post(
                        self._json_empty_continuation_payload(
                            request,
                            route,
                            original_messages,
                        )
                    )
                    data = json_mode_retry_data
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    text = message.get("content") or ""
                    tool_calls = message.get("tool_calls")
                    reasoning_content = message.get("reasoning_content")
                    structured_output = self._try_parse_json(text, request)
                except LLMProxyError as exc:
                    json_mode_retry_error = f"{exc.error_type}: {str(exc)[:200]}"
            if structured_output is None and self._expects_json(request) and text.strip():
                try:
                    repair_data = await self._post(
                        self._json_repair_payload(request, route, text, original_messages)
                    )
                    repair_text = self._response_text(repair_data)
                    structured_output = self._try_parse_json(repair_text, request)
                    if structured_output is not None:
                        text = json.dumps(structured_output, ensure_ascii=False)
                    else:
                        repair_error = "repair response not parseable as JSON"
                except LLMProxyError as exc:
                    repair_error = f"{exc.error_type}: {str(exc)[:200]}"

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        choice = (data.get("choices") or [{}])[0]
        if structured_output is not None:
            text = json.dumps(structured_output, ensure_ascii=False)

        normalized_usage = self._normalized_usage(primary_data)
        if json_mode_retry_data is not None:
            normalized_usage = self._merge_usage(normalized_usage, self._normalized_usage(json_mode_retry_data))
        if repair_data is not None:
            normalized_usage = self._merge_usage(normalized_usage, self._normalized_usage(repair_data))

        return LLMProxyResponse(
            text=text,
            structured_output=structured_output,
            usage=normalized_usage,
            session_id=None,
            duration_ms=duration_ms,
            raw_payload={
                "id": data.get("id"),
                "object": data.get("object"),
                "model": data.get("model"),
                "finish_reason": choice.get("finish_reason"),
                "message": {
                    "content": text,
                    "reasoning_content": reasoning_content,
                    "tool_calls": tool_calls,
                },
                "tool_calls": tool_calls,
                "reasoning_content": reasoning_content,
                "provider": self.name,
                "json_repair": (
                    {
                        "id": repair_data.get("id"),
                        "model": repair_data.get("model"),
                        "finish_reason": ((repair_data.get("choices") or [{}])[0]).get("finish_reason"),
                    }
                    if repair_data is not None
                    else None
                ),
                "json_mode_retry": (
                    {
                        "id": json_mode_retry_data.get("id"),
                        "model": json_mode_retry_data.get("model"),
                        "finish_reason": ((json_mode_retry_data.get("choices") or [{}])[0]).get(
                            "finish_reason"
                        ),
                    }
                    if json_mode_retry_data is not None
                    else None
                ),
                "json_mode_initial": {
                    "id": primary_data.get("id"),
                    "model": primary_data.get("model"),
                    "finish_reason": ((primary_data.get("choices") or [{}])[0]).get("finish_reason"),
                },
            },
            cache_hit=False,
            proxy={
                "provider": self.name,
                "requested_model": route.requested_model,
                "resolved_model": route.resolved_model,
                "upstream_model": data.get("model") or route.resolved_model,
                "route_reason": route.route_reason,
                "retry_count": retry_count,
                "json_mode_retry_attempted": json_mode_retry_data is not None
                or json_mode_retry_error is not None,
                "json_mode_retry_success": json_mode_retry_data is not None and bool(text.strip()),
                "json_mode_retry_error": json_mode_retry_error,
                "json_repair_attempted": repair_data is not None or repair_error is not None,
                "json_repair_success": repair_data is not None and structured_output is not None,
                "json_repair_error": repair_error,
            },
        )

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMProxyError("DeepSeek 请求超时", error_type="timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMProxyError(
                f"DeepSeek 请求失败: {self._safe_exception_summary(exc)}",
                error_type="upstream_unavailable",
            ) from exc
        except Exception as exc:
            raise LLMProxyError(
                f"DeepSeek 请求异常: {self._safe_exception_summary(exc)}",
                error_type="upstream_unavailable",
            ) from exc

        if response.status_code >= 400:
            self._raise_for_status(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMProxyError("DeepSeek 响应不是合法 JSON", error_type="output_invalid") from exc
        if not isinstance(data, dict):
            raise LLMProxyError("DeepSeek 响应不是 JSON 对象", error_type="output_invalid")
        return data

    async def _wait_for_cooldown(self) -> None:
        delay = self._cooldown_until - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def _record_cooldown(self) -> None:
        if self.rate_limit_cooldown_seconds <= 0:
            return
        self._cooldown_until = max(
            self._cooldown_until,
            time.monotonic() + self.rate_limit_cooldown_seconds,
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        error_type = {
            400: "bad_request",
            401: "auth_error",
            402: "quota_error",
            422: "bad_request",
            429: "rate_limited",
            500: "upstream_unavailable",
            503: "upstream_unavailable",
        }.get(response.status_code, "upstream_error")
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("error") or payload.get("message") or "")[:300]
        except Exception:
            detail = response.text[:300]
        raise LLMProxyError(
            f"DeepSeek HTTP {response.status_code}: {detail}",
            error_type=error_type,
        )

    @staticmethod
    def _safe_exception_summary(exc: BaseException) -> str:
        detail = str(exc).strip()
        if not detail:
            detail = repr(exc)
        return f"{exc.__class__.__name__}: {detail}"[:300]

    def _request_payload(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
        *,
        force_response_format: bool = True,
        force_json_schema_instruction: bool = True,
    ) -> dict[str, Any]:
        messages = self._messages_for_request(
            request,
            force_json_schema_instruction=force_json_schema_instruction,
        )
        payload: dict[str, Any] = {
            "model": route.resolved_model or self.default_model,
            "messages": messages,
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if force_response_format and self._expects_json(request):
            payload["response_format"] = {"type": "json_object"}
        if request.tools:
            payload["tools"] = request.tools
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _messages_for_request(
        self,
        request: LLMProxyRequest,
        *,
        force_json_schema_instruction: bool = True,
    ) -> list[dict[str, Any]]:
        messages = list(request.messages) if request.messages else self._messages_from_prompt(request)
        if force_json_schema_instruction and self._expects_json(request):
            messages = self._ensure_json_mode_instruction(messages, request)
        return messages

    def _messages_from_prompt(self, request: LLMProxyRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt or request.prompt_text()})
        return messages

    @staticmethod
    def _ensure_json_mode_instruction(
        messages: list[dict[str, Any]],
        request: LLMProxyRequest,
    ) -> list[dict[str, Any]]:
        joined = json.dumps(messages, ensure_ascii=False).lower()
        needs_json_instruction = "json" not in joined
        needs_schema = bool(request.json_schema) and JSON_SCHEMA_INSTRUCTION_MARKER.lower() not in joined
        if not needs_json_instruction and not needs_schema:
            return messages

        instruction_parts: list[str] = []
        if needs_json_instruction:
            instruction_parts.append("请只输出合法 JSON 对象，不要输出 Markdown、解释文字或代码块。")
        if needs_schema and request.json_schema:
            schema = json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":"))
            instruction_parts.append(
                f"{JSON_SCHEMA_INSTRUCTION_MARKER}\n"
                f"必须严格符合下面的 JSON Schema，required 字段必须全部输出，"
                f"additionalProperties=false 时禁止输出额外字段：\n{schema}"
            )
        instruction = "\n".join(instruction_parts)
        if messages and str(messages[0].get("role") or "").lower() == "system":
            first = dict(messages[0])
            first["content"] = "\n\n".join([str(first.get("content") or ""), instruction]).strip()
            return [first, *messages[1:]]
        return [{"role": "system", "content": instruction}, *messages]

    def _json_empty_continuation_payload(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
        original_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        messages = [
            *original_messages,
            {
                "role": "user",
                "content": (
                    "你上一次返回了空内容，导致系统无法解析。请基于同一个输入重新输出合法 JSON 对象。"
                    "必须严格符合前面给出的 JSON Schema；不要输出 Markdown、解释文字或代码块。"
                ),
            },
        ]
        return self._payload_from_messages(
            request,
            route,
            messages,
            force_response_format=False,
        )

    def _json_repair_payload(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
        raw_text: str,
        original_messages: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if original_messages:
            messages = [
                *original_messages,
                {"role": "assistant", "content": raw_text[:12000]},
                {
                    "role": "user",
                    "content": (
                        "你上一次输出无法被解析为合法 JSON，失败原因是 JSON parser 无法解析该内容。"
                        "请说明失败原因只在内部判断，最终只重新输出一个合法 JSON 对象。"
                        "必须严格符合前面给出的 JSON Schema；不要输出 Markdown、解释文字或代码块。"
                    ),
                },
            ]
            return self._payload_from_messages(
                request,
                route,
                messages,
                force_response_format=True,
            )

        schema = json.dumps(request.json_schema or {"type": "object"}, ensure_ascii=False, indent=2)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是严格的 JSON 修复器。只允许把用户提供的模型输出整理成合法 JSON，"
                    "不得新增事实、不得解释、不得输出 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": "\n\n".join(
                    [
                        "下面这段模型输出本应是 JSON，但格式不合法。请尽最大可能转成合法 JSON。",
                        f"JSON Schema:\n{schema}",
                        f"原始输出:\n{raw_text[:12000]}",
                        "只输出 JSON。",
                    ]
                ),
            },
        ]
        return self._payload_from_messages(
            request,
            route,
            messages,
            force_response_format=True,
        )

    def _payload_from_messages(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
        messages: list[dict[str, Any]],
        *,
        force_response_format: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": route.resolved_model or self.default_model,
            "messages": messages,
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if force_response_format and self._expects_json(request):
            payload["response_format"] = {"type": "json_object"}
        if request.tools:
            payload["tools"] = request.tools
        if request.tool_choice is not None:
            payload["tool_choice"] = request.tool_choice
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return str(message.get("content") or "")

    @staticmethod
    def _normalized_usage(data: dict[str, Any]) -> dict[str, int]:
        usage = data.get("usage") or {}
        normalized_usage = {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
            "prompt_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens", 0) or 0),
            "prompt_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens", 0) or 0),
        }
        completion_details = usage.get("completion_tokens_details") or {}
        if isinstance(completion_details, dict):
            normalized_usage["reasoning_tokens"] = int(completion_details.get("reasoning_tokens", 0) or 0)
        if not normalized_usage["total_tokens"]:
            normalized_usage["total_tokens"] = (
                normalized_usage["input_tokens"] + normalized_usage["output_tokens"]
            )
        return normalized_usage

    @staticmethod
    def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        return {
            "input_tokens": int(left.get("input_tokens", 0)) + int(right.get("input_tokens", 0)),
            "output_tokens": int(left.get("output_tokens", 0)) + int(right.get("output_tokens", 0)),
            "total_tokens": int(left.get("total_tokens", 0)) + int(right.get("total_tokens", 0)),
            "prompt_cache_hit_tokens": int(left.get("prompt_cache_hit_tokens", 0))
            + int(right.get("prompt_cache_hit_tokens", 0)),
            "prompt_cache_miss_tokens": int(left.get("prompt_cache_miss_tokens", 0))
            + int(right.get("prompt_cache_miss_tokens", 0)),
            "reasoning_tokens": int(left.get("reasoning_tokens", 0)) + int(right.get("reasoning_tokens", 0)),
        }

    @staticmethod
    def _expects_json(request: LLMProxyRequest) -> bool:
        return bool(request.json_schema or (request.response_format or {}).get("type") == "json_object")

    @staticmethod
    def _try_parse_json(text: str, request: LLMProxyRequest) -> Any | None:
        if not DeepSeekOpenAIProvider._expects_json(request):
            return None
        candidate = text.strip()
        if not candidate:
            return None
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
            candidate = re.sub(r"\s*```\s*$", "", candidate)
        try:
            return json.loads(candidate)
        except Exception:
            pass
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except Exception:
                pass
        return None

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "default_model": self.default_model,
            "max_concurrency": self.max_concurrency,
        }

    def runtime_stats(self) -> dict:
        return {
            "base_url": self.base_url,
            "enabled": self.enabled,
            "cooldown_until": self._cooldown_until,
        }

    def supports(self, model: str) -> bool:
        return model.startswith("deepseek-")
