"""Reusable provider for OpenAI-compatible chat completion APIs."""

from __future__ import annotations

import asyncio
import fnmatch
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


class OpenAICompatibleProvider:

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout: float,
        enabled: bool = True,
        model_patterns: tuple[str, ...] = (),
        model_mappings: dict[str, str] | None = None,
        reasoning_style: str = "generic",
        cache_usage_style: str = "inclusive",
        json_prefix_completion_enabled: bool = False,
        max_concurrency: int = 5,
        rate_limit_cooldown_seconds: float = 60,
        thinking_type: str | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = 10,
        initial_retry_delay_seconds: float = 1.0,
        max_retry_delay_seconds: float = 60.0,
    ):
        self.name = str(name).strip()
        if not self.name:
            raise ValueError("OpenAI-compatible provider name 不能为空")
        self.base_url = str(base_url).strip().rstrip("/")
        if not self.base_url:
            raise ValueError(f"{self.name} base_url 不能为空")
        self.api_key = str(api_key or "")
        self.default_model = str(default_model).strip()
        if not self.default_model:
            raise ValueError(f"{self.name} default_model 不能为空")
        self.timeout = float(timeout)
        self.enabled = (
            str(enabled).strip().lower() not in {"0", "false", "no", "off"}
            if isinstance(enabled, str)
            else bool(enabled)
        )
        if isinstance(model_patterns, str):
            model_patterns = (model_patterns,)
        self.model_patterns = tuple(str(item) for item in model_patterns if str(item).strip())
        self.model_mappings = {
            str(canonical).strip().lower(): str(upstream).strip()
            for canonical, upstream in (model_mappings or {}).items()
            if str(canonical).strip() and str(upstream).strip()
        }
        self.reasoning_style = str(reasoning_style or "generic").strip().lower()
        if self.reasoning_style not in {
            "generic",
            "deepseek",
            "aliyun",
            "volcengine",
            "aiclient2api",
        }:
            raise ValueError(f"{self.name} reasoning_style 不支持: {self.reasoning_style}")
        self.cache_usage_style = str(cache_usage_style or "inclusive").strip().lower()
        if self.cache_usage_style not in {"inclusive", "separate"}:
            raise ValueError(
                f"{self.name} cache_usage_style 不支持: {self.cache_usage_style}"
            )
        self.json_prefix_completion_enabled = bool(json_prefix_completion_enabled)
        self.max_concurrency = max(1, int(max_concurrency or 1))
        self.rate_limit_cooldown_seconds = max(0.0, float(rate_limit_cooldown_seconds or 0))
        self.max_retries = max(0, int(max_retries or 0))
        self.initial_retry_delay_seconds = max(0.0, float(initial_retry_delay_seconds or 0.0))
        self.max_retry_delay_seconds = max(0.0, float(max_retry_delay_seconds or 0.0))
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
                raise LLMProxyError(f"{self.name} API key 未配置", error_type="auth_error")

            payload = self._request_payload(request, route)
            original_messages = list(payload.get("messages") or [])
            started_at = time.perf_counter()
            retry_stats: dict[str, Any] = {"count": 0, "events": []}
            prefix_continuation_data: dict[str, Any] | None = None
            prefix_continuation_error: str | None = None
            prefix_continuation_parsed = False

            data = await self._post_with_retries(
                payload,
                retry_stats=retry_stats,
                purpose="initial",
            )
            primary_data = data
            reasoning_stages = self._reasoning_stages(("initial", primary_data))

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            text = message.get("content") or ""
            tool_calls = message.get("tool_calls")
            structured_output = self._try_parse_json(text, request)
            repair_data: dict[str, Any] | None = None
            repair_error: str | None = None
            if (
                structured_output is None
                and self._expects_json(request)
                and not tool_calls
                and self.json_prefix_completion_enabled
                and (
                    str(choice.get("finish_reason") or "").strip().lower() == "length"
                    or not text.strip()
                )
            ):
                prefix_text = str(text)
                prefix_reasoning = str(message.get("reasoning_content") or "")
                try:
                    prefix_continuation_data = await self._post_with_retries(
                        self._json_prefix_continuation_payload(
                            request,
                            route,
                            original_messages,
                            reasoning_content=prefix_reasoning,
                            content=prefix_text,
                        ),
                        retry_stats=retry_stats,
                        purpose="json_prefix_continuation",
                        endpoint_url=self._prefix_completion_url(),
                    )
                    data = prefix_continuation_data
                    reasoning_stages.extend(
                        self._reasoning_stages(
                            ("json_prefix_continuation", prefix_continuation_data)
                        )
                    )
                    choice = (data.get("choices") or [{}])[0]
                    message = choice.get("message") or {}
                    text = self._merge_prefix_completion_content(
                        prefix_text,
                        str(message.get("content") or ""),
                    )
                    tool_calls = message.get("tool_calls")
                    structured_output = self._try_parse_json(text, request)
                    prefix_continuation_parsed = structured_output is not None
                except LLMProxyError as exc:
                    prefix_continuation_error = f"{exc.error_type}: {str(exc)[:200]}"

            final_finish_reason = str(choice.get("finish_reason") or "").strip().lower()
            if (
                structured_output is None
                and self._expects_json(request)
                and text.strip()
                and final_finish_reason != "length"
            ):
                try:
                    repair_data = await self._post_with_retries(
                        self._json_repair_payload(request, route, text),
                        retry_stats=retry_stats,
                        purpose="json_repair",
                    )
                    reasoning_stages.extend(
                        self._reasoning_stages(("json_repair", repair_data))
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
        reasoning_content = self._render_reasoning_stages(reasoning_stages)

        normalized_usage = self._normalized_usage(
            primary_data,
            cache_usage_style=self.cache_usage_style,
        )
        if prefix_continuation_data is not None:
            normalized_usage = self._merge_usage(
                normalized_usage,
                self._normalized_usage(
                    prefix_continuation_data,
                    cache_usage_style=self.cache_usage_style,
                ),
            )
        if repair_data is not None:
            normalized_usage = self._merge_usage(
                normalized_usage,
                self._normalized_usage(
                    repair_data,
                    cache_usage_style=self.cache_usage_style,
                ),
            )
        primary_diagnostics = self._response_diagnostics(primary_data)
        prefix_continuation_diagnostics = (
            self._response_diagnostics(prefix_continuation_data)
            if prefix_continuation_data is not None
            else None
        )
        if prefix_continuation_diagnostics is not None:
            prefix_continuation_diagnostics.update(
                {
                    "endpoint": self._prefix_completion_url(),
                    "prefix_content_chars": len(prefix_text),
                    "prefix_reasoning_chars": len(prefix_reasoning),
                }
            )
        json_repair_diagnostics = (
            self._response_diagnostics(repair_data)
            if repair_data is not None
            else None
        )

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
                "reasoning_stages": reasoning_stages,
                "provider": self.name,
                "json_repair": (
                    json_repair_diagnostics
                ),
                "json_prefix_continuation": prefix_continuation_diagnostics,
                "json_mode_initial": primary_diagnostics,
            },
            cache_hit=False,
            proxy={
                "provider": self.name,
                "requested_model": route.requested_model,
                "resolved_model": route.resolved_model,
                "upstream_model": data.get("model") or route.upstream_model or route.resolved_model,
                "requested_provider": route.requested_provider,
                "route_reason": route.route_reason,
                "retry_count": retry_stats["count"],
                "upstream_retry_events": retry_stats["events"],
                "upstream_max_retries": self.max_retries,
                "upstream_max_retry_delay_seconds": self.max_retry_delay_seconds,
                "json_prefix_continuation_attempted": prefix_continuation_data is not None
                or prefix_continuation_error is not None,
                "json_prefix_continuation_success": prefix_continuation_parsed,
                "json_prefix_continuation_error": prefix_continuation_error,
                "json_mode_initial_finish_reason": primary_diagnostics.get("finish_reason"),
                "json_prefix_continuation_finish_reason": (
                    prefix_continuation_diagnostics or {}
                ).get("finish_reason"),
                "json_mode_initial_usage": primary_diagnostics.get("usage"),
                "json_prefix_continuation_usage": (
                    prefix_continuation_diagnostics or {}
                ).get("usage"),
                "json_repair_attempted": repair_data is not None or repair_error is not None,
                "json_repair_success": repair_data is not None and structured_output is not None,
                "json_repair_error": repair_error,
                "json_repair_finish_reason": (
                    json_repair_diagnostics or {}
                ).get("finish_reason"),
                "json_repair_usage": (
                    json_repair_diagnostics or {}
                ).get("usage"),
            },
            reasoning_content=reasoning_content,
        )

    @staticmethod
    def _reasoning_stages(*items: tuple[str, dict[str, Any] | None]) -> list[dict[str, str]]:
        """保留一次逻辑调用内每个物理请求返回的完整思考文本。"""

        stages: list[dict[str, str]] = []
        for stage, data in items:
            choice = ((data or {}).get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = str(message.get("reasoning_content") or "").strip()
            if content:
                stages.append({"stage": stage, "content": content})
        return stages

    @staticmethod
    def _render_reasoning_stages(stages: list[dict[str, str]]) -> str:
        if not stages:
            return ""
        if len(stages) == 1:
            return stages[0]["content"]
        return "\n\n".join(
            f"[{item['stage']}]\n{item['content']}" for item in stages
        )

    async def _post_with_retries(
        self,
        payload: dict[str, Any],
        *,
        retry_stats: dict[str, Any],
        purpose: str,
        endpoint_url: str | None = None,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                if endpoint_url is None:
                    return await self._post(payload)
                return await self._post(payload, endpoint_url=endpoint_url)
            except LLMProxyError as exc:
                if not self._is_retriable_error(exc) or attempt >= self.max_retries:
                    if exc.error_type == "rate_limited":
                        self._record_cooldown()
                    raise

                if exc.error_type == "rate_limited":
                    self._record_cooldown()

                attempt += 1
                delay = self._retry_delay_seconds(attempt, exc)
                retry_stats["count"] = int(retry_stats.get("count", 0)) + 1
                events = retry_stats.setdefault("events", [])
                if isinstance(events, list):
                    events.append(
                        {
                            "purpose": purpose,
                            "retry_index": attempt,
                            "error_type": exc.error_type,
                            "wait_seconds": delay,
                            "error": str(exc)[:200],
                        }
                    )
                await asyncio.sleep(delay)

    @staticmethod
    def _is_retriable_error(exc: LLMProxyError) -> bool:
        return exc.error_type in {"upstream_unavailable", "timeout", "rate_limited"}

    def _retry_delay_seconds(self, retry_index: int, exc: LLMProxyError | None = None) -> float:
        if self.initial_retry_delay_seconds <= 0:
            delay = 0.0
        else:
            delay = self.initial_retry_delay_seconds * (2 ** max(0, retry_index - 1))
        if exc and exc.error_type == "rate_limited":
            delay = max(delay, self._cooldown_until - time.monotonic())
        if self.max_retry_delay_seconds > 0:
            delay = min(delay, self.max_retry_delay_seconds)
        return max(0.0, delay)

    async def _post(
        self,
        payload: dict[str, Any],
        *,
        endpoint_url: str | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout, trust_env=True) as client:
                response = await client.post(
                    endpoint_url or f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMProxyError(f"{self.name} 请求超时", error_type="timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMProxyError(
                f"{self.name} 请求失败: {self._safe_exception_summary(exc)}",
                error_type="upstream_unavailable",
            ) from exc
        except Exception as exc:
            raise LLMProxyError(
                f"{self.name} 请求异常: {self._safe_exception_summary(exc)}",
                error_type="upstream_unavailable",
            ) from exc

        if response.status_code >= 400:
            self._raise_for_status(response)

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMProxyError(f"{self.name} 响应不是合法 JSON", error_type="output_invalid") from exc
        if not isinstance(data, dict):
            raise LLMProxyError(f"{self.name} 响应不是 JSON 对象", error_type="output_invalid")
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
            403: "auth_error",
            404: "bad_request",
            408: "timeout",
            422: "bad_request",
            429: "rate_limited",
            500: "upstream_unavailable",
            502: "upstream_unavailable",
            503: "upstream_unavailable",
            504: "timeout",
        }.get(response.status_code, "upstream_error")
        detail = ""
        try:
            payload = response.json()
            detail = str(payload.get("error") or payload.get("message") or "")[:300]
        except Exception:
            detail = response.text[:300]
        raise LLMProxyError(
            f"{self.name} HTTP {response.status_code}: {detail}",
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
        inject_json_schema_instruction = bool(
            (request.provider_options or {}).get("inject_json_schema_instruction", True)
        )
        messages = self._messages_for_request(
            request,
            force_json_schema_instruction=(
                force_json_schema_instruction and inject_json_schema_instruction
            ),
        )
        payload: dict[str, Any] = {
            "model": route.upstream_model or route.resolved_model or self.default_model,
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
        self._apply_reasoning_options(payload, request)
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

    def _json_prefix_continuation_payload(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
        original_messages: list[dict[str, Any]],
        *,
        reasoning_content: str,
        content: str,
    ) -> dict[str, Any]:
        messages = [
            *original_messages,
            {
                "role": "assistant",
                "reasoning_content": reasoning_content,
                "content": content,
                "prefix": True,
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
    ) -> dict[str, Any]:
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
                        f"原始输出:\n{raw_text}",
                        "只输出 JSON。",
                    ]
                ),
            },
        ]
        payload = self._payload_from_messages(
            request,
            route,
            messages,
            force_response_format=True,
        )
        self._disable_reasoning(payload)
        return payload

    def _prefix_completion_url(self) -> str:
        base_url = self.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        if base_url.endswith("/beta"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/beta/chat/completions"

    @staticmethod
    def _merge_prefix_completion_content(prefix: str, completion: str) -> str:
        if not prefix:
            return completion
        if completion.startswith(prefix):
            return completion
        return f"{prefix}{completion}"

    def _payload_from_messages(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
        messages: list[dict[str, Any]],
        *,
        force_response_format: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": route.upstream_model or route.resolved_model or self.default_model,
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
        self._apply_reasoning_options(payload, request)
        return payload

    def _apply_reasoning_options(self, payload: dict[str, Any], request: LLMProxyRequest) -> None:
        options = request.provider_options or {}
        thinking_type = options.get("thinking_type", self.thinking_type)
        if self.reasoning_style == "aliyun" and thinking_type:
            payload["enable_thinking"] = str(thinking_type).strip().lower() not in {
                "disabled",
                "false",
                "0",
                "off",
            }
        elif self.reasoning_style == "aiclient2api" and thinking_type:
            self._apply_aiclient2api_thinking(
                payload,
                thinking_type=str(thinking_type),
                reasoning_effort=options.get(
                    "reasoning_effort",
                    self.reasoning_effort,
                ),
                budget_tokens=options.get("thinking_budget_tokens"),
            )
        elif thinking_type:
            payload["thinking"] = {"type": str(thinking_type)}
        if (
            self.reasoning_style != "aiclient2api"
            and str(thinking_type or "").strip().lower() != "disabled"
        ):
            reasoning_effort = options.get("reasoning_effort", self.reasoning_effort)
            if reasoning_effort:
                payload["reasoning_effort"] = str(reasoning_effort)
        extra_body = options.get("extra_body")
        if isinstance(extra_body, dict):
            protected = {"model", "messages", "stream"}
            payload.update(
                {str(key): value for key, value in extra_body.items() if key not in protected}
            )

    @staticmethod
    def _apply_aiclient2api_thinking(
        payload: dict[str, Any],
        *,
        thinking_type: str,
        reasoning_effort: Any,
        budget_tokens: Any,
    ) -> None:
        normalized_type = str(thinking_type or "").strip().lower()
        if normalized_type in {"false", "0", "off"}:
            normalized_type = "disabled"
        elif normalized_type in {"true", "1", "on"}:
            normalized_type = "enabled"

        thinking: dict[str, Any]
        if normalized_type == "disabled":
            thinking = {"type": "disabled"}
        elif normalized_type in {"enabled", "adaptive"}:
            if budget_tokens is not None:
                try:
                    normalized_budget = max(1, int(budget_tokens))
                except (TypeError, ValueError) as exc:
                    raise ValueError("thinking_budget_tokens 必须是正整数") from exc
                thinking = {
                    "type": "enabled",
                    "budget_tokens": normalized_budget,
                }
            else:
                normalized_effort = (
                    str(reasoning_effort or "medium").strip().lower()
                )
                if normalized_effort not in {"low", "medium", "high"}:
                    normalized_effort = "medium"
                thinking = {
                    "type": "adaptive",
                    "effort": normalized_effort,
                }
        else:
            raise ValueError(
                "AIClient2API thinking_type 只支持 enabled、adaptive 或 disabled"
            )

        extra_body = payload.setdefault("extra_body", {})
        if not isinstance(extra_body, dict):
            extra_body = {}
            payload["extra_body"] = extra_body
        anthropic = extra_body.setdefault("anthropic", {})
        if not isinstance(anthropic, dict):
            anthropic = {}
            extra_body["anthropic"] = anthropic
        anthropic["thinking"] = thinking

    def _disable_reasoning(self, payload: dict[str, Any]) -> None:
        if self.reasoning_style == "aliyun":
            payload["enable_thinking"] = False
            payload.pop("thinking", None)
        elif self.reasoning_style in {"deepseek", "volcengine"}:
            payload["thinking"] = {"type": "disabled"}
            payload.pop("enable_thinking", None)
        elif self.reasoning_style == "aiclient2api":
            self._apply_aiclient2api_thinking(
                payload,
                thinking_type="disabled",
                reasoning_effort=None,
                budget_tokens=None,
            )
            payload.pop("thinking", None)
            payload.pop("enable_thinking", None)
        else:
            payload.pop("thinking", None)
            payload.pop("enable_thinking", None)
        payload.pop("reasoning_effort", None)

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return str(message.get("content") or "")

    def _response_diagnostics(self, data: dict[str, Any]) -> dict[str, Any]:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = str(message.get("content") or "")
        reasoning_content = str(message.get("reasoning_content") or "")
        return {
            "id": data.get("id"),
            "model": data.get("model"),
            "finish_reason": choice.get("finish_reason"),
            "usage": self._normalized_usage(
                data,
                cache_usage_style=self.cache_usage_style,
            ),
            "provider_usage": dict(data.get("usage") or {}),
            "content_chars": len(content),
            "reasoning_chars": len(reasoning_content),
            "has_tool_calls": bool(message.get("tool_calls")),
        }

    @staticmethod
    def _normalized_usage(
        data: dict[str, Any],
        *,
        cache_usage_style: str = "inclusive",
    ) -> dict[str, Any]:
        usage = data.get("usage") or {}
        prompt_details = usage.get("prompt_tokens_details") or {}
        nested_cached_tokens = (
            int(prompt_details.get("cached_tokens", 0) or 0)
            if isinstance(prompt_details, dict)
            else 0
        )
        provider_input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        if not cache_hit_tokens:
            cache_hit_tokens = nested_cached_tokens
        if cache_usage_style == "separate":
            # Anthropic-style usage reports fresh input separately from cache reads.
            cache_miss_tokens = provider_input_tokens
            input_tokens = provider_input_tokens + cache_hit_tokens
        else:
            input_tokens = provider_input_tokens
            if "prompt_cache_miss_tokens" in usage:
                cache_miss_tokens = int(
                    usage.get("prompt_cache_miss_tokens", 0) or 0
                )
            else:
                cache_miss_tokens = max(0, input_tokens - cache_hit_tokens)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        normalized_usage = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "prompt_cache_hit_tokens": cache_hit_tokens,
            "prompt_cache_miss_tokens": cache_miss_tokens,
        }
        completion_details = usage.get("completion_tokens_details") or {}
        if isinstance(completion_details, dict):
            normalized_usage["reasoning_tokens"] = int(completion_details.get("reasoning_tokens", 0) or 0)
        for key, value in usage.items():
            normalized_key = str(key).lower()
            if "cost" in normalized_key or normalized_key in {"currency", "cost_currency"}:
                normalized_usage[str(key)] = value
        return normalized_usage

    @staticmethod
    def _merge_usage(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {
            "input_tokens": int(left.get("input_tokens", 0)) + int(right.get("input_tokens", 0)),
            "output_tokens": int(left.get("output_tokens", 0)) + int(right.get("output_tokens", 0)),
            "total_tokens": int(left.get("total_tokens", 0)) + int(right.get("total_tokens", 0)),
            "prompt_cache_hit_tokens": int(left.get("prompt_cache_hit_tokens", 0))
            + int(right.get("prompt_cache_hit_tokens", 0)),
            "prompt_cache_miss_tokens": int(left.get("prompt_cache_miss_tokens", 0))
            + int(right.get("prompt_cache_miss_tokens", 0)),
            "reasoning_tokens": int(left.get("reasoning_tokens", 0)) + int(right.get("reasoning_tokens", 0)),
        }
        for key in set(left) | set(right):
            normalized_key = str(key).lower()
            if "cost" not in normalized_key:
                if normalized_key in {"currency", "cost_currency"}:
                    merged[key] = right.get(key) or left.get(key)
                continue
            try:
                merged[key] = float(left.get(key, 0) or 0) + float(right.get(key, 0) or 0)
            except (TypeError, ValueError):
                merged[key] = right.get(key) if right.get(key) is not None else left.get(key)
        return merged

    @staticmethod
    def _expects_json(request: LLMProxyRequest) -> bool:
        return bool(request.json_schema or (request.response_format or {}).get("type") == "json_object")

    @staticmethod
    def _try_parse_json(text: str, request: LLMProxyRequest) -> Any | None:
        if not OpenAICompatibleProvider._expects_json(request):
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
            "model_patterns": list(self.model_patterns),
            "model_mappings": dict(self.model_mappings),
            "reasoning_style": self.reasoning_style,
        }

    def runtime_stats(self) -> dict:
        return {
            "base_url": self.base_url,
            "enabled": self.enabled,
            "cooldown_until": self._cooldown_until,
        }

    def supports(self, model: str) -> bool:
        return self.resolve_model(model) is not None

    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def resolve_model(self, model: str) -> str | None:
        normalized = str(model).strip()
        mapped = self.model_mappings.get(normalized.lower())
        if mapped:
            return mapped
        if any(
            fnmatch.fnmatchcase(normalized.lower(), pattern.lower())
            for pattern in self.model_patterns
        ):
            return normalized
        return None
