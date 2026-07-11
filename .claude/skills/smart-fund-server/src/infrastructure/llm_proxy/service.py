"""Claude CLI LLM 代理运行时。

职责：
- 统一构造 Claude CLI / tmux 会话池调用
- 处理代理环境变量恢复
- 提供结构化 JSON 输出解析
- 提供基础并发控制和进程内缓存
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from typing import Any

from src.infrastructure.clients.base import ORIGINAL_PROXY_ENV
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy.cache import LLMPersistentFileCache
from src.infrastructure.llm_proxy.providers.claude_tmux import ClaudeTmuxProvider
from src.infrastructure.llm_proxy.providers.deepseek_openai import DeepSeekOpenAIProvider
from src.infrastructure.llm_proxy.registry import ProviderRegistry
from src.infrastructure.llm_proxy.router import ModelRouter, ModelRouterConfig
from src.infrastructure.llm_proxy.tmux_backend import (
    TmuxClaudeError,
    TmuxClaudePool,
    TmuxClaudeRunner,
)
from src.infrastructure.llm_proxy.types import (
    ClaudeProxyRequest,
    ClaudeProxyResponse,
    LLMProxyError,
    LLMProxyRequest,
    LLMProxyResponse,
)
from src.infrastructure.observability.langfuse_tracing import (
    clip_trace_text,
    langfuse_observation,
    langfuse_update_generation,
)

logger = logging.getLogger(__name__)


def _llm_call_log_fields(
    request: LLMProxyRequest,
    *,
    provider: str,
    model: str,
    backend: str | None = None,
) -> dict[str, Any]:
    metadata = request.metadata or {}
    return {
        "task": metadata.get("task") or metadata.get("operation") or "-",
        "source_id": metadata.get("source_id") or "-",
        "source_type": metadata.get("source_type") or "-",
        "provider": metadata.get("_llm_provider") or provider,
        "model": model,
        "backend": backend or "-",
        "use_cache": request.use_cache,
        "has_json_schema": bool(request.json_schema),
        "prompt_chars": len(request.prompt_text()),
    }


def _log_llm_call_start(fields: dict[str, Any]) -> None:
    logger.info(
        "[llm_call] START task=%s provider=%s model=%s backend=%s source_type=%s source_id=%s "
        "use_cache=%s json_schema=%s prompt_chars=%s",
        fields["task"],
        fields["provider"],
        fields["model"],
        fields["backend"],
        fields["source_type"],
        fields["source_id"],
        fields["use_cache"],
        fields["has_json_schema"],
        fields["prompt_chars"],
    )


def _append_llm_full_trace(
    *,
    event: str,
    fields: dict[str, Any],
    request: LLMProxyRequest,
    response: LLMProxyResponse | None = None,
    error: Exception | None = None,
) -> None:
    path = os.getenv("LLM_PROXY_FULL_TRACE_FILE")
    if not path:
        return
    payload: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event,
        "fields": fields,
        "request": {
            "model": request.model,
            "system_prompt": request.system_prompt,
            "prompt": request.prompt,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "json_schema": request.json_schema,
            "response_format": request.response_format,
            "tools": request.tools,
            "tool_choice": request.tool_choice,
            "metadata": request.metadata,
            "timeout": request.timeout,
            "use_cache": request.use_cache,
        },
    }
    if response is not None:
        payload["response"] = {
            "text": response.text,
            "structured_output": response.structured_output,
            "usage": response.usage,
            "duration_ms": response.duration_ms,
            "raw_payload": response.raw_payload,
            "proxy": response.proxy,
            "cache_hit": response.cache_hit,
        }
    if error is not None:
        payload["error"] = {"type": error.__class__.__name__, "message": str(error)}
    try:
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n\n===== llm_proxy {event} =====\n")
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
            handle.write("\n")
    except Exception:
        logger.debug("[llm_call] failed to append full trace", exc_info=True)


def _log_llm_call_done(
    fields: dict[str, Any],
    *,
    duration_ms: int,
    usage: dict[str, Any] | None,
) -> None:
    usage = usage or {}
    logger.info(
        "[llm_call] DONE task=%s provider=%s model=%s backend=%s source_type=%s source_id=%s "
        "duration_ms=%s input_tokens=%s output_tokens=%s total_tokens=%s "
        "prompt_cache_hit_tokens=%s prompt_cache_miss_tokens=%s cache_hit=False",
        fields["task"],
        fields["provider"],
        fields["model"],
        fields["backend"],
        fields["source_type"],
        fields["source_id"],
        duration_ms,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        usage.get("total_tokens", 0),
        usage.get("prompt_cache_hit_tokens", 0),
        usage.get("prompt_cache_miss_tokens", 0),
    )


def _log_llm_call_failed(
    fields: dict[str, Any],
    *,
    duration_ms: int,
    error: Exception,
) -> None:
    logger.warning(
        "[llm_call] FAILED task=%s provider=%s model=%s backend=%s source_type=%s source_id=%s "
        "duration_ms=%s error=%r",
        fields["task"],
        fields["provider"],
        fields["model"],
        fields["backend"],
        fields["source_type"],
        fields["source_id"],
        duration_ms,
        error,
    )


def _llm_generation_observation(fields: dict[str, Any], request: LLMProxyRequest):
    return langfuse_observation(
        name=f"llm:{fields['task']}",
        as_type="generation",
        input=_llm_trace_input(request),
        metadata={
            **fields,
            "timeout": request.timeout,
            "tools_count": len(request.tools or []),
            "has_tool_choice": request.tool_choice is not None,
            "cache_store": "miss",
        },
        model=str(fields.get("model") or request.model or "-"),
        model_parameters={
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": request.response_format,
            "json_schema": bool(request.json_schema),
        },
    )


def _trace_llm_cache_hit(
    fields: dict[str, Any],
    request: LLMProxyRequest,
    response: LLMProxyResponse,
) -> None:
    with langfuse_observation(
        name=f"llm:{fields['task']}:cache_hit",
        as_type="generation",
        input=_llm_trace_input(request),
        output=_llm_trace_output(response),
        metadata={
            **fields,
            "cache_hit": True,
            "cache_store": response.proxy.get("cache_store") or "memory",
            "duration_ms": response.duration_ms,
        },
        model=str(fields.get("model") or request.model or "-"),
        model_parameters={
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": request.response_format,
            "json_schema": bool(request.json_schema),
        },
        usage_details=_llm_usage_details(response.usage),
    ):
        pass


def _trace_llm_response(response: LLMProxyResponse) -> None:
    langfuse_update_generation(
        output=_llm_trace_output(response),
        metadata={
            "cache_hit": response.cache_hit,
            "duration_ms": response.duration_ms,
            "proxy": response.proxy,
            "session_id": response.session_id,
        },
        usage_details=_llm_usage_details(response.usage),
        level="DEFAULT",
        status_message="completed",
    )


def _trace_llm_error(error: Exception) -> None:
    langfuse_update_generation(
        metadata={"error_type": error.__class__.__name__},
        level="ERROR",
        status_message=str(error),
    )


def _llm_trace_input(request: LLMProxyRequest) -> dict[str, Any]:
    metadata = {
        key: value
        for key, value in (request.metadata or {}).items()
        if not key.startswith("_cache_key_")
    }
    return {
        "prompt": clip_trace_text(request.prompt),
        "system_prompt": clip_trace_text(request.system_prompt),
        "messages": _clip_json_trace(request.messages),
        "prompt_text_preview": clip_trace_text(request.prompt_text(), limit=8000),
        "metadata": metadata,
        "json_schema": _clip_json_trace(request.json_schema),
        "response_format": request.response_format,
        "provider_options": {
            key: value
            for key, value in (request.provider_options or {}).items()
            if key in {"reasoning_effort", "thinking_type"}
        },
        "use_cache": request.use_cache,
    }


def _llm_trace_output(response: LLMProxyResponse) -> dict[str, Any]:
    return {
        "text": clip_trace_text(response.text),
        "structured_output": _clip_json_trace(response.structured_output),
        "usage": response.usage,
        "duration_ms": response.duration_ms,
        "cache_hit": response.cache_hit,
        "proxy": response.proxy,
        "provider_diagnostics": _llm_provider_diagnostics(response.raw_payload),
    }


def _llm_provider_diagnostics(raw_payload: dict[str, Any] | None) -> dict[str, Any]:
    raw_payload = raw_payload or {}
    diagnostics = {
        "finish_reason": raw_payload.get("finish_reason"),
        "json_mode_initial": raw_payload.get("json_mode_initial"),
        "json_mode_retry": raw_payload.get("json_mode_retry"),
        "json_repair": raw_payload.get("json_repair"),
    }
    return {key: value for key, value in diagnostics.items() if value is not None}


def _clip_json_trace(value: Any, *, limit: int = 1_000_000) -> Any:
    if value is None:
        return None
    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        rendered = str(value)
    return clip_trace_text(rendered, limit=limit)


def _llm_usage_details(usage: dict[str, Any] | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for key in (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "prompt_cache_hit_tokens",
        "prompt_cache_miss_tokens",
    ):
        value = (usage or {}).get(key)
        if isinstance(value, int):
            result[key] = value
        elif isinstance(value, float):
            result[key] = int(value)
    return result


class _TTLCache:
    def __init__(self, ttl_seconds: int, max_size: int):
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._store: OrderedDict[str, tuple[float, ClaudeProxyResponse]] = OrderedDict()

    def get(self, key: str) -> ClaudeProxyResponse | None:
        now = time.time()
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if expires_at < now:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: ClaudeProxyResponse) -> None:
        if self.max_size <= 0:
            return
        self._store[key] = (time.time() + self.ttl_seconds, value)
        self._store.move_to_end(key)
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)


class ClaudeProxyService:
    """受控 Claude CLI 代理，支持 `claude -p` 与交互式 tmux 后端。"""

    def __init__(
        self,
        *,
        cli_bin: str,
        default_model: str,
        default_timeout: float,
        max_concurrency: int,
        cache_ttl_seconds: int,
        cache_max_size: int,
        sandbox_mode: str,
        sandbox_root: str,
        min_interval_seconds: float = 0.0,
        rate_limit_cooldown_seconds: float = 0.0,
        model_aliases: dict[str, str] | None = None,
        child_env_overrides: dict[str, str] | None = None,
        child_settings: dict[str, Any] | None = None,
        backend: str = "claude_p",
        tmux_ready_timeout: int = 45,
        tmux_pool_size: int = 1,
        tmux_clear_timeout: int = 20,
        tmux_max_requests_per_session: int = 200,
        tmux_max_session_age_seconds: int = 7200,
        file_context_threshold_chars: int = 8000,
    ):
        self.cli_bin = cli_bin
        self.default_model = default_model
        self.backend = (backend or "claude_p").strip().lower()
        self.model_aliases = {str(k): str(v) for k, v in (model_aliases or {}).items()}
        self.default_timeout = default_timeout
        self.sandbox_mode = sandbox_mode
        self.sandbox_root = Path(sandbox_root)
        self.sandbox_root.mkdir(parents=True, exist_ok=True)
        self.min_interval_seconds = max(0.0, float(min_interval_seconds or 0.0))
        self.rate_limit_cooldown_seconds = max(
            0.0, float(rate_limit_cooldown_seconds or 0.0)
        )
        self.child_env_overrides = {
            str(k): str(v) for k, v in (child_env_overrides or {}).items()
        }
        self.child_settings = dict(child_settings or {})
        self.tmux_ready_timeout = max(5, int(tmux_ready_timeout or 45))
        self.tmux_pool_size = max(1, int(tmux_pool_size or 1))
        self.tmux_clear_timeout = max(5, int(tmux_clear_timeout or 20))
        self.tmux_max_requests_per_session = max(
            1,
            int(tmux_max_requests_per_session or 200),
        )
        self.tmux_max_session_age_seconds = max(
            60,
            int(tmux_max_session_age_seconds or 7200),
        )
        self.file_context_threshold_chars = max(
            0,
            int(file_context_threshold_chars or 0),
        )
        self._sem = threading.Semaphore(max(1, max_concurrency))
        self._cache = _TTLCache(cache_ttl_seconds, cache_max_size)
        self._cache_lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._next_available_at = 0.0
        self._tmux_pool: TmuxClaudePool | None = None
        self._tmux_pool_model: str | None = None
        self._tmux_pool_lock = threading.Lock()

    async def generate(self, request: ClaudeProxyRequest) -> ClaudeProxyResponse:
        cache_key = self._cache_key(request)
        log_fields = _llm_call_log_fields(
            request,
            provider="claude_proxy",
            model=self._resolve_model(request.model),
            backend=self.backend,
        )
        if request.use_cache:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached:
                cached_response = cached.clone(cache_hit=True)
                _trace_llm_cache_hit(log_fields, request, cached_response)
                return cached_response

        call_started_at = time.perf_counter()
        _log_llm_call_start(log_fields)
        _append_llm_full_trace(event="request", fields=log_fields, request=request)

        # 失败重试一次：tmux session 偶发返回 usage 为空、API 抖动等。
        # 限流类失败不重试（已经在 _record_rate_limit 里冷却）。
        with _llm_generation_observation(log_fields, request):
            try:
                response = await asyncio.to_thread(self._invoke_with_limit, request)
            except LLMProxyError as exc:
                msg = str(exc)
                if self._looks_like_rate_limit(msg):
                    _log_llm_call_failed(
                        log_fields,
                        duration_ms=int((time.perf_counter() - call_started_at) * 1000),
                        error=exc,
                    )
                    _append_llm_full_trace(
                        event="failed",
                        fields=log_fields,
                        request=request,
                        error=exc,
                    )
                    _trace_llm_error(exc)
                    raise
                logger.warning("[llm_proxy] first attempt failed, retrying once: %s", msg)
                try:
                    response = await asyncio.to_thread(self._invoke_with_limit, request)
                except Exception as retry_exc:
                    _log_llm_call_failed(
                        log_fields,
                        duration_ms=int((time.perf_counter() - call_started_at) * 1000),
                        error=retry_exc,
                    )
                    _append_llm_full_trace(
                        event="failed",
                        fields=log_fields,
                        request=request,
                        error=retry_exc,
                    )
                    _trace_llm_error(retry_exc)
                    raise
            except Exception as exc:
                _log_llm_call_failed(
                    log_fields,
                    duration_ms=int((time.perf_counter() - call_started_at) * 1000),
                    error=exc,
                )
                _append_llm_full_trace(
                    event="failed",
                    fields=log_fields,
                    request=request,
                    error=exc,
                )
                _trace_llm_error(exc)
                raise

            _log_llm_call_done(
                log_fields,
                duration_ms=int((time.perf_counter() - call_started_at) * 1000),
                usage=response.usage,
            )
            _append_llm_full_trace(
                event="response",
                fields=log_fields,
                request=request,
                response=response,
            )
            _trace_llm_response(response)

            if request.use_cache:
                with self._cache_lock:
                    self._cache.set(cache_key, response.clone())
            return response

    def _invoke_with_limit(self, request: ClaudeProxyRequest) -> ClaudeProxyResponse:
        with self._sem:
            self._wait_for_rate_limit()
            return self._invoke_sync(request)

    def _wait_for_rate_limit(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            if self._next_available_at > now:
                time.sleep(self._next_available_at - now)
            if self.min_interval_seconds > 0:
                self._next_available_at = time.monotonic() + self.min_interval_seconds

    def _record_rate_limit(self) -> None:
        if self.rate_limit_cooldown_seconds <= 0:
            return
        with self._rate_lock:
            self._next_available_at = max(
                self._next_available_at,
                time.monotonic() + self.rate_limit_cooldown_seconds,
            )

    @staticmethod
    def _looks_like_rate_limit(text: str) -> bool:
        lowered = text.lower()
        return any(
            marker in lowered
            for marker in (
                "429",
                "rate limit",
                "rate_limit",
                "too many requests",
                "1302",
                "速率限制",
                "请求频率",
            )
        )

    def _cache_key(self, request: ClaudeProxyRequest) -> str:
        resolved_model = self._resolve_model(request.model)
        payload = {
            "prompt": request.prompt,
            "system_prompt": request.system_prompt,
            "model": resolved_model,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "json_schema": request.json_schema,
            "response_format": request.response_format,
            "tools": request.tools,
            "tool_choice": request.tool_choice,
            "metadata": request.metadata,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _build_command(self, request: ClaudeProxyRequest) -> list[str]:
        mode = self._resolved_sandbox_mode()
        cmd = [self.cli_bin]
        if mode == "hard":
            cmd.append("--bare")

        cmd.extend(
            [
                "-p",
                request.prompt,
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
                "--no-session-persistence",
                "--disable-slash-commands",
                "--tools",
                "",
            ]
        )

        model = self._resolve_model(request.model)
        if model:
            cmd.extend(["--model", model])

        if request.system_prompt:
            cmd.extend(["--append-system-prompt", request.system_prompt])

        if request.json_schema:
            cmd.extend(
                [
                    "--json-schema",
                    json.dumps(request.json_schema, ensure_ascii=False, separators=(",", ":")),
                ]
            )

        settings_path = self._prepare_settings_file()
        if settings_path:
            cmd.extend(["--settings", settings_path])

        return cmd

    def _resolve_model(self, requested_model: str | None) -> str:
        if not requested_model:
            return self.default_model
        requested_model = requested_model.strip()
        if not requested_model:
            return self.default_model
        return self.model_aliases.get(requested_model, requested_model)

    def _resolved_sandbox_mode(self) -> str:
        mode = (self.sandbox_mode or "auto").lower()
        if mode != "auto":
            return mode
        has_auth = bool(
            os.getenv("ANTHROPIC_API_KEY")
            or os.getenv("ANTHROPIC_AUTH_TOKEN")
            or self.child_env_overrides.get("ANTHROPIC_API_KEY")
            or self.child_env_overrides.get("ANTHROPIC_AUTH_TOKEN")
        )
        return "hard" if has_auth else "light"

    def _prepare_settings_file(self) -> str | None:
        if not self.child_settings:
            return None
        settings_path = self.sandbox_root / "claude-settings.json"
        settings_path.write_text(
            json.dumps(self.child_settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return str(settings_path)

    def _prepare_exec_env(self) -> tuple[dict[str, str], str]:
        mode = self._resolved_sandbox_mode()

        workdir = self.sandbox_root / "workdir"
        workdir.mkdir(parents=True, exist_ok=True)

        if mode == "hard":
            env = {
                "PATH": os.environ.get("PATH", ""),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "LC_ALL": os.environ.get("LC_ALL", os.environ.get("LANG", "C.UTF-8")),
            }
            home_dir = self.sandbox_root / "home"
            cache_dir = self.sandbox_root / "xdg-cache"
            config_dir = self.sandbox_root / "xdg-config"
            state_dir = self.sandbox_root / "xdg-state"
            for path in (home_dir, cache_dir, config_dir, state_dir):
                path.mkdir(parents=True, exist_ok=True)

            env.update(
                {
                    "HOME": str(home_dir),
                    "XDG_CACHE_HOME": str(cache_dir),
                    "XDG_CONFIG_HOME": str(config_dir),
                    "XDG_STATE_HOME": str(state_dir),
                }
            )
        else:
            env = {**os.environ, **ORIGINAL_PROXY_ENV}

        env.update(self.child_env_overrides)

        return env, str(workdir)

    def _invoke_sync(self, request: ClaudeProxyRequest) -> ClaudeProxyResponse:
        if self.backend in {"tmux_pool", "pool"}:
            return self._invoke_tmux_pool_sync(request)
        if self.backend in {"tmux", "tmux_one_shot", "interactive", "claude_tui"}:
            return self._invoke_tmux_sync(request)
        return self._invoke_claude_p_sync(request)

    def _invoke_claude_p_sync(self, request: ClaudeProxyRequest) -> ClaudeProxyResponse:
        cmd = self._build_command(request)
        timeout = request.timeout or self.default_timeout
        env, cwd = self._prepare_exec_env()
        started_at = time.perf_counter()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMProxyError(f"claude -p 超时（{timeout}s）") from exc
        except Exception as exc:
            raise LLMProxyError(f"claude -p 调用失败: {exc}") from exc

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()

        if result.returncode != 0:
            err_short = stderr[:500]
            out_short = stdout[:300]
            if self._looks_like_rate_limit(f"{stderr}\n{stdout}"):
                self._record_rate_limit()
            raise LLMProxyError(
                f"claude -p 退出码 {result.returncode}, stderr={err_short!r}, stdout={out_short!r}"
            )

        payload = self._parse_payload(stdout)
        usage = payload.get("usage") or {}
        text = payload.get("result") or ""
        structured_output = payload.get("structured_output")
        if structured_output is not None and not text:
            text = json.dumps(structured_output, ensure_ascii=False)

        logger.info(
            "[llm_proxy] model=%s sandbox=%s duration_ms=%s cache=%s input=%s output=%s",
            self._resolve_model(request.model),
            self._resolved_sandbox_mode(),
            duration_ms,
            False,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )

        return ClaudeProxyResponse(
            text=text,
            structured_output=structured_output,
            usage=usage,
            session_id=payload.get("session_id"),
            duration_ms=duration_ms,
            raw_payload=payload,
            cache_hit=False,
        )

    def _invoke_tmux_pool_sync(self, request: ClaudeProxyRequest) -> ClaudeProxyResponse:
        timeout = request.timeout or self.default_timeout
        env, _cwd = self._prepare_exec_env()
        prompt = self._render_interactive_prompt(request)
        model = self._resolve_model(request.model)
        started_at = time.perf_counter()

        pool = self._get_tmux_pool(env=env, model=model)
        try:
            result = pool.run(prompt, timeout=timeout)
        except TmuxClaudeError as exc:
            if self._looks_like_rate_limit(str(exc)):
                self._record_rate_limit()
            raise LLMProxyError(f"交互式 Claude tmux pool 调用失败: {exc}") from exc

        text = result.text
        structured_output = None
        if request.json_schema:
            structured_output = self._try_parse_structured_output(text)
            if structured_output is not None:
                text = json.dumps(structured_output, ensure_ascii=False)

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "[llm_proxy] backend=tmux_pool model=%s duration_ms=%s cache=%s input=%s output=%s pool=%s",
            model,
            duration_ms,
            False,
            result.usage.get("input_tokens", 0),
            result.usage.get("output_tokens", 0),
            pool.stats(),
        )

        return ClaudeProxyResponse(
            text=text,
            structured_output=structured_output,
            usage=result.usage,
            session_id=result.session_id,
            duration_ms=duration_ms,
            raw_payload={
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "backend": "tmux_pool",
                "session_id": result.session_id,
                "result": result.text,
                "structured_output": structured_output,
                "usage": result.usage,
                "tool_calls": result.tool_calls,
                "pool": pool.stats(),
            },
            cache_hit=False,
        )

    def _get_tmux_pool(self, *, env: dict[str, str], model: str) -> TmuxClaudePool:
        with self._tmux_pool_lock:
            if self._tmux_pool and self._tmux_pool_model == model:
                return self._tmux_pool
            if self._tmux_pool:
                self._tmux_pool.close()
            self._tmux_pool = TmuxClaudePool(
                cli_bin=self.cli_bin,
                model=model,
                env=env,
                sandbox_root=self.sandbox_root,
                pool_size=self.tmux_pool_size,
                ready_timeout=self.tmux_ready_timeout,
                clear_timeout=self.tmux_clear_timeout,
                max_requests_per_session=self.tmux_max_requests_per_session,
                max_session_age_seconds=self.tmux_max_session_age_seconds,
            )
            self._tmux_pool_model = model
            return self._tmux_pool

    def runtime_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {"backend": self.backend}
        with self._tmux_pool_lock:
            if self._tmux_pool:
                stats["tmux_pool"] = self._tmux_pool.stats()
        return stats

    def _invoke_tmux_sync(self, request: ClaudeProxyRequest) -> ClaudeProxyResponse:
        timeout = request.timeout or self.default_timeout
        env, _cwd = self._prepare_exec_env()
        prompt = self._render_interactive_prompt(request)
        started_at = time.perf_counter()

        runner = TmuxClaudeRunner(
            cli_bin=self.cli_bin,
            model=self._resolve_model(request.model),
            env=env,
            sandbox_root=self.sandbox_root,
            ready_timeout=self.tmux_ready_timeout,
        )
        try:
            result = runner.run(prompt, timeout=timeout)
        except TmuxClaudeError as exc:
            if self._looks_like_rate_limit(str(exc)):
                self._record_rate_limit()
            raise LLMProxyError(f"交互式 Claude tmux 调用失败: {exc}") from exc

        text = result.text
        structured_output = None
        if request.json_schema:
            structured_output = self._try_parse_structured_output(text)
            if structured_output is not None:
                text = json.dumps(structured_output, ensure_ascii=False)

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        logger.info(
            "[llm_proxy] backend=tmux model=%s duration_ms=%s cache=%s input=%s output=%s",
            self._resolve_model(request.model),
            duration_ms,
            False,
            result.usage.get("input_tokens", 0),
            result.usage.get("output_tokens", 0),
        )

        return ClaudeProxyResponse(
            text=text,
            structured_output=structured_output,
            usage=result.usage,
            session_id=result.session_id,
            duration_ms=duration_ms,
            raw_payload={
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "backend": "tmux",
                "session_id": result.session_id,
                "result": result.text,
                "structured_output": structured_output,
                "usage": result.usage,
                "tool_calls": result.tool_calls,
            },
            cache_hit=False,
        )

    def _render_interactive_prompt(self, request: ClaudeProxyRequest) -> str:
        direct_prompt = self._render_direct_task_prompt(request)
        if (
            self.file_context_threshold_chars > 0
            and len(direct_prompt) > self.file_context_threshold_chars
        ):
            context_path = self._write_context_file(direct_prompt)
            return "\n\n".join(
                [
                    "你正在处理一个独立任务。不要联网。",
                    "完整任务内容已经写入本地文件。请使用 Read 工具读取该文件，"
                    "然后严格按照文件中的任务要求给出最终答案。",
                    f"任务文件：{context_path}",
                    "只读取这个任务文件；除非任务文件明确要求，不要读取或搜索其他本机文件。",
                    "最终答案不要解释读取过程，不要提及代理、API 或实现细节。",
                ]
            )
        return direct_prompt

    def _render_direct_task_prompt(self, request: ClaudeProxyRequest) -> str:
        parts: list[str] = [
            (
                "SYSTEM:\n"
                "你正在处理一个独立文本任务。不要联网；除非当前提示明确给出"
                "任务文件路径，否则不要读取或搜索本机文件。严格按照用户任务要求"
                "输出最终答案，不要提及运行环境或实现细节。"
            )
        ]
        if request.system_prompt:
            parts.append(f"USER SYSTEM INSTRUCTIONS:\n{request.system_prompt.strip()}")
        parts.append(request.prompt.strip())
        if request.json_schema:
            schema = json.dumps(request.json_schema, ensure_ascii=False, indent=2)
            parts.append(
                "请只输出一个符合下面 JSON Schema 的 JSON 对象，不要输出 Markdown、解释文字或代码块。\n"
                f"JSON Schema:\n{schema}"
            )
        if request.max_tokens:
            parts.append(f"回答长度上限约 {request.max_tokens} tokens。")
        return "\n\n".join(part for part in parts if part).strip()

    def _write_context_file(self, content: str) -> Path:
        context_dir = self.sandbox_root / "request-contexts"
        context_dir.mkdir(parents=True, exist_ok=True)
        path = context_dir / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.md"
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def _try_parse_structured_output(text: str) -> Any | None:
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
            candidate = re.sub(r"\s*```$", "", candidate)
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
                return None
        return None

    def _parse_payload(self, stdout: str) -> dict[str, Any]:
        if not stdout:
            raise LLMProxyError("claude -p 无输出")

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise LLMProxyError(f"claude -p JSON 解析失败: {stdout[:300]!r}") from exc

        if not isinstance(data, dict):
            raise LLMProxyError("claude -p 输出不是 JSON 对象")

        if data.get("is_error"):
            result = data.get("result") or "claude -p 返回错误结果"
            if self._looks_like_rate_limit(str(result)):
                self._record_rate_limit()
            raise LLMProxyError(result)

        return data


_proxy_service: ClaudeProxyService | None = None
_gateway_service: "LLMGatewayService | None" = None


def get_claude_proxy_service() -> ClaudeProxyService:
    global _proxy_service
    if _proxy_service is None:
        _proxy_service = ClaudeProxyService(
            cli_bin=settings.CLAUDE_PROXY_CLI_BIN,
            default_model=settings.CLAUDE_PROXY_MODEL,
            default_timeout=settings.CLAUDE_PROXY_TIMEOUT,
            max_concurrency=settings.CLAUDE_PROXY_MAX_CONCURRENCY,
            cache_ttl_seconds=settings.CLAUDE_PROXY_CACHE_TTL_SECONDS,
            cache_max_size=settings.CLAUDE_PROXY_CACHE_MAX_SIZE,
            sandbox_mode=settings.CLAUDE_PROXY_SANDBOX_MODE,
            sandbox_root=settings.CLAUDE_PROXY_SANDBOX_ROOT,
            min_interval_seconds=settings.CLAUDE_PROXY_MIN_INTERVAL_SECONDS,
            rate_limit_cooldown_seconds=settings.CLAUDE_PROXY_RATE_LIMIT_COOLDOWN_SECONDS,
            model_aliases=settings.CLAUDE_PROXY_MODEL_ALIASES,
            child_env_overrides=settings.CLAUDE_PROXY_CHILD_ENV,
            child_settings=settings.CLAUDE_PROXY_CHILD_SETTINGS,
            backend=settings.CLAUDE_PROXY_BACKEND,
            tmux_ready_timeout=settings.CLAUDE_PROXY_TMUX_READY_TIMEOUT,
            tmux_pool_size=settings.CLAUDE_PROXY_TMUX_POOL_SIZE,
            tmux_clear_timeout=settings.CLAUDE_PROXY_TMUX_CLEAR_TIMEOUT,
            tmux_max_requests_per_session=settings.CLAUDE_PROXY_TMUX_MAX_REQUESTS_PER_SESSION,
            tmux_max_session_age_seconds=settings.CLAUDE_PROXY_TMUX_MAX_SESSION_AGE_SECONDS,
            file_context_threshold_chars=settings.CLAUDE_PROXY_FILE_CONTEXT_THRESHOLD_CHARS,
        )
    return _proxy_service


class LLMGatewayService:
    """Unified model gateway routing model names to providers."""

    def __init__(
        self,
        *,
        router: ModelRouter,
        registry: ProviderRegistry,
        cache_ttl_seconds: int,
        cache_max_size: int,
        file_cache: LLMPersistentFileCache | None = None,
    ):
        self.router = router
        self.registry = registry
        self._cache = _TTLCache(cache_ttl_seconds, cache_max_size)
        self._cache_lock = threading.Lock()
        self._file_cache = file_cache

    async def generate(self, request: LLMProxyRequest) -> LLMProxyResponse:
        if request.json_schema and not request.response_format:
            request = replace(request, response_format={"type": "json_object"})
        route = self.router.resolve(request.model)
        provider = self.registry.select_first_available(route.provider_candidates)
        route = type(route)(
            requested_model=route.requested_model,
            resolved_model=route.resolved_model,
            provider_candidates=route.provider_candidates,
            selected_provider=provider.name,
            route_reason=route.route_reason if provider.name == route.selected_provider else "fallback",
            fallback_allowed=route.fallback_allowed,
        )
        log_fields = _llm_call_log_fields(
            request,
            provider=provider.name,
            model=route.resolved_model,
            backend=provider.name,
        )
        cache_key = self._cache_key(request, route.selected_provider or "", route.resolved_model)
        if request.use_cache:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached and _is_usable_cached_response(request, cached):
                cached_response = cached.clone(cache_hit=True)
                _trace_llm_cache_hit(log_fields, request, cached_response)
                return cached_response
            if self._file_cache is not None:
                cached = self._file_cache.get(cache_key)
                if cached and _is_usable_cached_response(request, cached):
                    cached.proxy.setdefault("provider", provider.name)
                    cached.proxy.setdefault("requested_model", route.requested_model)
                    cached.proxy.setdefault("resolved_model", route.resolved_model)
                    cached.proxy.setdefault("route_reason", route.route_reason)
                    cached.proxy["cache_store"] = "file"
                    with self._cache_lock:
                        self._cache.set(cache_key, cached.clone(cache_hit=False))
                    cached_response = cached.clone(cache_hit=True)
                    _trace_llm_cache_hit(log_fields, request, cached_response)
                    return cached_response

        gateway_logs_call = provider.name != "claude_tmux"
        call_started_at = time.perf_counter()
        if gateway_logs_call:
            _log_llm_call_start(log_fields)
            _append_llm_full_trace(event="request", fields=log_fields, request=request)
        generation_context = _llm_generation_observation(log_fields, request) if gateway_logs_call else nullcontext()
        with generation_context:
            try:
                response = await provider.generate(request, route)
                response = await self._repair_schema_invalid_response(
                    request,
                    route,
                    provider,
                    response,
                )
            except Exception as exc:
                if gateway_logs_call:
                    _log_llm_call_failed(
                        log_fields,
                        duration_ms=int((time.perf_counter() - call_started_at) * 1000),
                        error=exc,
                    )
                    _append_llm_full_trace(
                        event="failed",
                        fields=log_fields,
                        request=request,
                        error=exc,
                    )
                    _trace_llm_error(exc)
                raise
            response.proxy.setdefault("provider", provider.name)
            response.proxy.setdefault("requested_model", route.requested_model)
            response.proxy.setdefault("resolved_model", route.resolved_model)
            response.proxy.setdefault("route_reason", route.route_reason)
            response.proxy.setdefault("retry_count", 0)
            if gateway_logs_call:
                _log_llm_call_done(
                    log_fields,
                    duration_ms=int((time.perf_counter() - call_started_at) * 1000),
                    usage=response.usage,
                )
                _append_llm_full_trace(
                    event="response",
                    fields=log_fields,
                    request=request,
                    response=response,
                )
                _trace_llm_response(response)

            if _should_write_cache(request) and _is_cacheable_response(request, response):
                with self._cache_lock:
                    self._cache.set(cache_key, response.clone())
                if self._file_cache is not None:
                    self._file_cache.set(cache_key, response.clone())
            return response

    async def repair_with_feedback(
        self,
        request: LLMProxyRequest,
        response: LLMProxyResponse,
        validation_issues: list[str],
        *,
        instruction: str | None = None,
        retry_reason: str = "business_validation_invalid",
    ) -> LLMProxyResponse:
        """Continue the original request with caller-provided validation feedback.

        This is for business/domain validation failures that JSON parsing and
        JSON Schema cannot express. The repair call keeps the original request
        prefix, appends the previous assistant output, then asks the model to
        repair only the invalid parts. The repaired response is cached under the
        original request cache key.
        """

        max_attempts = _feedback_repair_max_attempts()
        original_issues = list(validation_issues)
        current_response = response
        current_issues = list(validation_issues)
        repair_messages: list[dict[str, Any]] | None = None
        for attempt in range(1, max_attempts + 1):
            repair_request = _feedback_repair_request(
                request,
                current_response,
                current_issues,
                instruction=instruction,
                retry_reason=retry_reason,
                previous_messages=repair_messages,
            )
            repaired = await self.generate(repair_request)
            repaired_issues = _feedback_repair_response_issues(repaired, request)
            repaired.proxy.setdefault("feedback_repair_attempted", True)
            repaired.proxy.setdefault("feedback_repair_attempts", attempt)
            repaired.proxy.setdefault("feedback_repair_issues", original_issues)
            repaired.proxy.setdefault("feedback_repair_last_issues", repaired_issues)
            repaired.proxy.setdefault("feedback_repair_success", not repaired_issues)
            if not repaired_issues:
                return repaired
            repair_messages = list(repair_request.messages)
            current_response = repaired
            current_issues = repaired_issues
        current_response.proxy.setdefault("feedback_repair_attempted", True)
        current_response.proxy.setdefault("feedback_repair_attempts", max_attempts)
        current_response.proxy.setdefault("feedback_repair_issues", original_issues)
        current_response.proxy.setdefault("feedback_repair_success", False)
        current_response.proxy.setdefault("feedback_repair_retry_issues", current_issues)
        return current_response

    async def _repair_schema_invalid_response(
        self,
        request: LLMProxyRequest,
        route: LLMRouteDecision,
        provider: Any,
        response: LLMProxyResponse,
    ) -> LLMProxyResponse:
        if not request.json_schema:
            return response
        issues = _json_schema_validation_issues(response.structured_output, request.json_schema)
        if not issues:
            return response
        max_attempts = _schema_repair_max_attempts()
        original_issues = list(issues)
        current_response = response
        current_issues = list(issues)
        repair_messages: list[dict[str, Any]] | None = None
        for attempt in range(1, max_attempts + 1):
            repair_request = _schema_repair_request(
                request,
                current_response,
                current_issues,
                previous_messages=repair_messages,
            )
            repaired = await provider.generate(repair_request, route)
            repaired_issues = _json_schema_validation_issues(repaired.structured_output, request.json_schema)
            repaired.proxy.setdefault("schema_repair_attempted", True)
            repaired.proxy.setdefault("schema_repair_attempts", attempt)
            repaired.proxy.setdefault("schema_repair_issues", original_issues)
            repaired.proxy.setdefault("schema_repair_last_issues", repaired_issues)
            repaired.proxy.setdefault("schema_repair_success", not repaired_issues)
            if not repaired_issues:
                return repaired
            repair_messages = list(repair_request.messages)
            current_response = repaired
            current_issues = repaired_issues
        response.proxy.setdefault("schema_repair_attempted", True)
        response.proxy.setdefault("schema_repair_attempts", max_attempts)
        response.proxy.setdefault("schema_repair_issues", original_issues)
        response.proxy.setdefault("schema_repair_success", False)
        response.proxy.setdefault("schema_repair_retry_issues", current_issues)
        return response

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "default_provider": self.router.config.default_provider,
            "default_model": self.router.config.default_model,
            "model_routes": self.router.config.model_routes,
            "model_aliases": self.router.config.model_aliases,
            "providers": self.registry.health(),
            "cache": {
                "memory_ttl_seconds": self._cache.ttl_seconds,
                "memory_max_size": self._cache.max_size,
                "file": self._file_cache.stats() if self._file_cache else {"enabled": False},
            },
        }

    def runtime_stats(self) -> dict[str, Any]:
        return {
            "router": {
                "default_provider": self.router.config.default_provider,
                "default_model": self.router.config.default_model,
            },
            "providers": self.registry.runtime_stats(),
            "cache": {
                "file": self._file_cache.stats() if self._file_cache else {"enabled": False},
            },
        }

    @staticmethod
    def _cache_key(request: LLMProxyRequest, provider: str, resolved_model: str) -> str:
        metadata = request.metadata or {}
        cache_prompt = metadata.get("_cache_key_prompt", request.prompt)
        cache_system_prompt = metadata.get("_cache_key_system_prompt", request.system_prompt)
        cache_messages = metadata.get("_cache_key_messages", request.messages)
        cache_metadata = metadata.get("_cache_key_metadata")
        if not isinstance(cache_metadata, dict):
            cache_metadata = _cache_key_metadata(request.metadata)
        payload = {
            "provider": provider,
            "resolved_model": resolved_model,
            "prompt": cache_prompt,
            "system_prompt": cache_system_prompt,
            "messages": cache_messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "json_schema": request.json_schema,
            "response_format": request.response_format,
            "tools": request.tools,
            "tool_choice": request.tool_choice,
            "provider_options": request.provider_options,
            "metadata": cache_metadata,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_json_request(request: LLMProxyRequest) -> bool:
    return bool(request.json_schema or (request.response_format or {}).get("type") == "json_object")


def _is_usable_cached_response(request: LLMProxyRequest, response: LLMProxyResponse) -> bool:
    if _is_json_request(request) and response.structured_output is None:
        return False
    if request.json_schema and _json_schema_validation_issues(response.structured_output, request.json_schema):
        return False
    return bool(response.text or response.structured_output is not None)


def _is_cacheable_response(request: LLMProxyRequest, response: LLMProxyResponse) -> bool:
    if _is_json_request(request) and response.structured_output is None:
        return False
    if request.json_schema and _json_schema_validation_issues(response.structured_output, request.json_schema):
        return False
    return bool(response.text or response.structured_output is not None)


def _should_write_cache(request: LLMProxyRequest) -> bool:
    if request.use_cache:
        return True
    return bool((request.metadata or {}).get("retry_reason"))


def _cache_key_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in (metadata or {}).items()
        if key not in {"retry_reason", "validation_issues"} and not key.startswith("_cache_key_")
    }


def _schema_repair_request(
    request: LLMProxyRequest,
    response: LLMProxyResponse,
    issues: list[str],
    *,
    previous_messages: list[dict[str, Any]] | None = None,
) -> LLMProxyRequest:
    original_messages = previous_messages or _messages_for_schema_repair(request)
    previous = response.text or json.dumps(response.structured_output, ensure_ascii=False, default=str)
    schema = json.dumps(request.json_schema or {}, ensure_ascii=False, indent=2)
    metadata = {
        **(request.metadata or {}),
        "retry_reason": "json_schema_invalid",
        "validation_issues": issues,
        "_cache_key_prompt": request.prompt,
        "_cache_key_system_prompt": request.system_prompt,
        "_cache_key_messages": request.messages,
    }
    return LLMProxyRequest(
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        model=request.model,
        messages=[
            *original_messages,
            {"role": "assistant", "content": previous[:12000]},
            {
                "role": "user",
                "content": (
                    "你上一次输出是合法 JSON，但不符合系统要求的 JSON Schema。"
                    f"validation_issues={issues}。\n"
                    f"JSON Schema:\n{schema}\n"
                    "请只重新输出一个合法 JSON 对象。必须补齐 required 字段，遵守 enum/type/range，"
                    "不要输出 Markdown、解释文字或代码块。"
                ),
            },
        ],
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        json_schema=request.json_schema,
        response_format=request.response_format or {"type": "json_object"},
        tools=request.tools,
        tool_choice=request.tool_choice,
        provider_options=request.provider_options,
        metadata=metadata,
        timeout=request.timeout,
        use_cache=False,
    )


def _schema_repair_max_attempts() -> int:
    raw = os.getenv("LLM_PROXY_SCHEMA_REPAIR_MAX_ATTEMPTS", "3")
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 3


def _feedback_repair_max_attempts() -> int:
    raw = os.getenv("LLM_PROXY_FEEDBACK_REPAIR_MAX_ATTEMPTS", "3")
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 3


def _feedback_repair_response_issues(
    response: LLMProxyResponse,
    request: LLMProxyRequest,
) -> list[str]:
    if request.json_schema:
        return _json_schema_validation_issues(response.structured_output, request.json_schema)
    if _is_json_request(request) and response.structured_output is None:
        return ["structured_output:none"]
    return []


def _feedback_repair_request(
    request: LLMProxyRequest,
    response: LLMProxyResponse,
    issues: list[str],
    *,
    instruction: str | None,
    retry_reason: str,
    previous_messages: list[dict[str, Any]] | None = None,
) -> LLMProxyRequest:
    original_messages = previous_messages or _messages_for_schema_repair(request)
    previous = response.text or json.dumps(response.structured_output, ensure_ascii=False, default=str)
    default_instruction = (
        "你上一次输出没有通过调用方的业务校验。请基于同一个任务上下文继续修复输出。"
        "只修复 validation_issues 指出的字段或判断，不要新增外部事实，"
        "不要输出 Markdown、解释文字或代码块。"
    )
    metadata = {
        **(request.metadata or {}),
        "retry_reason": retry_reason,
        "validation_issues": issues,
        "_cache_key_prompt": request.prompt,
        "_cache_key_system_prompt": request.system_prompt,
        "_cache_key_messages": request.messages,
    }
    return LLMProxyRequest(
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        model=request.model,
        messages=[
            *original_messages,
            {"role": "assistant", "content": previous[:12000]},
            {
                "role": "user",
                "content": (
                    f"{instruction or default_instruction}\n"
                    f"validation_issues={issues}\n"
                    "请只重新输出一个符合原任务要求的 JSON 对象。"
                ),
            },
        ],
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        json_schema=request.json_schema,
        response_format=request.response_format or ({"type": "json_object"} if request.json_schema else None),
        tools=request.tools,
        tool_choice=request.tool_choice,
        provider_options=request.provider_options,
        metadata=metadata,
        timeout=request.timeout,
        use_cache=False,
    )


def _messages_for_schema_repair(request: LLMProxyRequest) -> list[dict[str, Any]]:
    if request.messages:
        return list(request.messages)
    messages: list[dict[str, Any]] = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    messages.append({"role": "user", "content": request.prompt or request.prompt_text()})
    return messages


def _json_schema_validation_issues(value: Any, schema: dict[str, Any] | None) -> list[str]:
    if not schema:
        return []
    return _validate_json_schema_value(value, schema, path="$")


def _validate_json_schema_value(value: Any, schema: dict[str, Any], *, path: str) -> list[str]:
    issues: list[str] = []
    if "const" in schema and value != schema.get("const"):
        issues.append(f"{path}:const_invalid")
        return issues
    if "enum" in schema and value not in (schema.get("enum") or []):
        issues.append(f"{path}:enum_invalid")
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for index, sub_schema in enumerate(all_of):
            if isinstance(sub_schema, dict):
                issues.extend(_validate_json_schema_value(value, sub_schema, path=path))
    if_schema = schema.get("if")
    if isinstance(if_schema, dict):
        if not _validate_json_schema_value(value, if_schema, path=path):
            then_schema = schema.get("then")
            if isinstance(then_schema, dict):
                issues.extend(_validate_json_schema_value(value, then_schema, path=path))
        else_schema = schema.get("else")
        if isinstance(else_schema, dict) and _validate_json_schema_value(value, if_schema, path=path):
            issues.extend(_validate_json_schema_value(value, else_schema, path=path))
    schema_type = schema.get("type")
    if schema_type is not None and not _json_schema_type_matches(value, schema_type):
        issues.append(f"{path}:type_invalid:{schema_type}")
        return issues
    if isinstance(value, dict):
        required = schema.get("required") or []
        for field in required:
            if field not in value:
                issues.append(f"{path}.{field}:required_missing")
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for field, field_schema in properties.items():
                if field in value and isinstance(field_schema, dict):
                    issues.extend(_validate_json_schema_value(value[field], field_schema, path=f"{path}.{field}"))
        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            allowed = set(properties)
            for field in value:
                if field not in allowed:
                    issues.append(f"{path}.{field}:additional_property")
    elif isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                issues.extend(_validate_json_schema_value(item, item_schema, path=f"{path}[{index}]"))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            issues.append(f"{path}:below_minimum")
        if maximum is not None and value > maximum:
            issues.append(f"{path}:above_maximum")
    return issues


def _json_schema_type_matches(value: Any, schema_type: Any) -> bool:
    if isinstance(schema_type, list):
        return any(_json_schema_type_matches(value, item) for item in schema_type)
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True


def get_llm_gateway_service() -> LLMGatewayService:
    global _gateway_service
    if _gateway_service is None:
        legacy_service = get_claude_proxy_service()
        registry = ProviderRegistry()
        registry.register(ClaudeTmuxProvider(legacy_service))
        registry.register(
            DeepSeekOpenAIProvider(
                base_url=settings.DEEPSEEK_BASE_URL,
                api_key=settings.DEEPSEEK_API_KEY,
                default_model=settings.DEEPSEEK_DEFAULT_MODEL,
                timeout=settings.DEEPSEEK_TIMEOUT,
                max_concurrency=settings.DEEPSEEK_MAX_CONCURRENCY,
                rate_limit_cooldown_seconds=settings.DEEPSEEK_RATE_LIMIT_COOLDOWN_SECONDS,
                thinking_type=settings.DEEPSEEK_THINKING_TYPE or None,
                reasoning_effort=settings.DEEPSEEK_REASONING_EFFORT or None,
            )
        )
        router = ModelRouter(
            ModelRouterConfig(
                default_model=settings.LLM_PROXY_DEFAULT_MODEL,
                default_provider=settings.LLM_PROXY_DEFAULT_PROVIDER,
                model_routes=settings.LLM_PROXY_MODEL_ROUTES,
                model_aliases=settings.LLM_PROXY_MODEL_ALIASES,
            )
        )
        _gateway_service = LLMGatewayService(
            router=router,
            registry=registry,
            cache_ttl_seconds=settings.LLM_PROXY_CACHE_TTL_SECONDS,
            cache_max_size=settings.LLM_PROXY_CACHE_MAX_SIZE,
            file_cache=LLMPersistentFileCache(
                settings.LLM_PROXY_FILE_CACHE_DIR,
                enabled=settings.LLM_PROXY_FILE_CACHE_ENABLED,
            ),
        )
    return _gateway_service
