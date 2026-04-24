"""Claude CLI LLM 代理运行时。

职责：
- 统一构造 Claude CLI / tmux 会话池调用
- 处理代理环境变量恢复
- 提供结构化 JSON 输出解析
- 提供基础并发控制和进程内缓存
"""

from __future__ import annotations

import asyncio
import copy
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.infrastructure.clients.base import ORIGINAL_PROXY_ENV
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy.tmux_backend import (
    TmuxClaudeError,
    TmuxClaudePool,
    TmuxClaudeRunner,
)

logger = logging.getLogger(__name__)


class LLMProxyError(RuntimeError):
    """LLM 代理层异常。"""


@dataclass(frozen=True)
class ClaudeProxyRequest:
    prompt: str
    system_prompt: str | None = None
    model: str | None = None
    temperature: float | None = 0.0
    max_tokens: int | None = None
    json_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timeout: float | None = None
    use_cache: bool = True


@dataclass
class ClaudeProxyResponse:
    text: str
    structured_output: Any | None
    usage: dict[str, Any]
    session_id: str | None
    duration_ms: int
    raw_payload: dict[str, Any]
    cache_hit: bool = False

    def clone(self, *, cache_hit: bool | None = None) -> "ClaudeProxyResponse":
        copied = ClaudeProxyResponse(
            text=self.text,
            structured_output=copy.deepcopy(self.structured_output),
            usage=dict(self.usage),
            session_id=self.session_id,
            duration_ms=self.duration_ms,
            raw_payload=dict(self.raw_payload),
            cache_hit=self.cache_hit if cache_hit is None else cache_hit,
        )
        return copied


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
        if request.use_cache:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached:
                return cached.clone(cache_hit=True)

        response = await asyncio.to_thread(self._invoke_with_limit, request)

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
