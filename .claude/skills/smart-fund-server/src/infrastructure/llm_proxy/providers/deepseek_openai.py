"""DeepSeek OpenAI-compatible provider."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from src.infrastructure.llm_proxy.types import (
    LLMProxyError,
    LLMProxyRequest,
    LLMProxyResponse,
    LLMRouteDecision,
)


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
            started_at = time.perf_counter()
            retry_count = 0

            try:
                data = await self._post(payload)
            except LLMProxyError as exc:
                if exc.error_type == "rate_limited":
                    self._record_cooldown()
                if exc.error_type not in {"upstream_unavailable", "timeout"}:
                    raise
                retry_count = 1
                data = await self._post(payload)

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        structured_output = self._try_parse_json(text, request)
        if structured_output is not None:
            text = json.dumps(structured_output, ensure_ascii=False)

        usage = data.get("usage") or {}
        normalized_usage = {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        }
        if not normalized_usage["total_tokens"]:
            normalized_usage["total_tokens"] = (
                normalized_usage["input_tokens"] + normalized_usage["output_tokens"]
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
                "provider": self.name,
            },
            cache_hit=False,
            proxy={
                "provider": self.name,
                "requested_model": route.requested_model,
                "resolved_model": route.resolved_model,
                "upstream_model": data.get("model") or route.resolved_model,
                "route_reason": route.route_reason,
                "retry_count": retry_count,
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

    def _request_payload(self, request: LLMProxyRequest, route: LLMRouteDecision) -> dict[str, Any]:
        messages = request.messages or self._messages_from_prompt(request)
        payload: dict[str, Any] = {
            "model": route.resolved_model or self.default_model,
            "messages": messages,
            "stream": False,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.json_schema or (request.response_format or {}).get("type") == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if self.thinking_type:
            payload["thinking"] = {"type": self.thinking_type}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        return payload

    def _messages_from_prompt(self, request: LLMProxyRequest) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        prompt = request.prompt or request.prompt_text()
        if request.json_schema:
            schema = json.dumps(request.json_schema, ensure_ascii=False, indent=2)
            prompt = "\n\n".join(
                [
                    prompt,
                    "请只输出合法 JSON，不要输出 Markdown、解释文字或代码块。",
                    f"JSON Schema:\n{schema}",
                ]
            )
        messages.append({"role": "user", "content": prompt})
        return messages

    @staticmethod
    def _try_parse_json(text: str, request: LLMProxyRequest) -> Any | None:
        if not (request.json_schema or (request.response_format or {}).get("type") == "json_object"):
            return None
        candidate = text.strip()
        if not candidate:
            return None
        if candidate.startswith("```"):
            candidate = candidate.removeprefix("```json").removeprefix("```").strip()
            candidate = candidate.removesuffix("```").strip()
        try:
            return json.loads(candidate)
        except Exception:
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
