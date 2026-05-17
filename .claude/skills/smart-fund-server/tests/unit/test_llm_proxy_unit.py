import importlib.util
import asyncio
import json
from pathlib import Path

import src.infrastructure.llm_proxy.service as service_module
import src.infrastructure.llm_proxy.tmux_backend as tmux_module
from src.infrastructure.llm_proxy.service import ClaudeProxyRequest, ClaudeProxyService
from src.infrastructure.llm_proxy.tmux_backend import PooledTmuxClaudeSession, TmuxClaudeResult


def _load_llm_proxy_module():
    route_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "interfaces"
        / "api"
        / "routes"
        / "llm_proxy.py"
    )
    spec = importlib.util.spec_from_file_location("llm_proxy_route_test", route_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_claude_proxy_service_parses_structured_output_and_caches(monkeypatch):
    calls = []

    class DummyCompleted:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "structured_output": {"answer": "ok"},
                "session_id": "sess-123",
                "usage": {"input_tokens": 11, "output_tokens": 7},
            },
            ensure_ascii=False,
        )

    def fake_run(cmd, capture_output, text, timeout, env, cwd):
        calls.append(cmd)
        return DummyCompleted()

    monkeypatch.setattr(service_module.subprocess, "run", fake_run)

    service = ClaudeProxyService(
        cli_bin="claude",
        default_model="sonnet",
        default_timeout=30,
        max_concurrency=1,
        cache_ttl_seconds=60,
        cache_max_size=16,
        sandbox_mode="light",
        sandbox_root="/tmp/test-claude-proxy",
    )

    request = ClaudeProxyRequest(
        prompt="提取结构化数据",
        system_prompt="你是 JSON 引擎",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )

    first = service._invoke_sync(request)
    cache_key = service._cache_key(request)
    service._cache.set(cache_key, first.clone())
    second = service._cache.get(cache_key).clone(cache_hit=True)

    assert first.structured_output == {"answer": "ok"}
    assert first.text == '{"answer": "ok"}'
    assert second.cache_hit is True
    assert len(calls) == 1
    assert "--json-schema" in calls[0]
    assert "--tools" in calls[0]


def test_claude_proxy_service_auto_mode_uses_hard_when_auth_token_present():
    service = ClaudeProxyService(
        cli_bin="claude",
        default_model="sonnet",
        default_timeout=30,
        max_concurrency=1,
        cache_ttl_seconds=60,
        cache_max_size=16,
        sandbox_mode="auto",
        sandbox_root="/tmp/test-claude-proxy",
        child_env_overrides={
            "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": "token",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "GLM-5.1",
        },
        child_settings={"skipDangerousModePermissionPrompt": True},
    )

    assert service._resolved_sandbox_mode() == "hard"
    settings_path = service._prepare_settings_file()
    assert settings_path
    assert Path(settings_path).exists()


def test_claude_proxy_service_hard_mode_uses_minimal_environment(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SSE_PORT", "12345")
    monkeypatch.setenv("CLAUDE_PROXY_MODEL", "sonnet")

    service = ClaudeProxyService(
        cli_bin="claude",
        default_model="sonnet",
        default_timeout=30,
        max_concurrency=1,
        cache_ttl_seconds=60,
        cache_max_size=16,
        sandbox_mode="hard",
        sandbox_root="/tmp/test-claude-proxy",
        child_env_overrides={"ANTHROPIC_AUTH_TOKEN": "token"},
    )

    env, cwd = service._prepare_exec_env()

    assert cwd.endswith("/workdir")
    assert env["HOME"].endswith("/home")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "token"
    assert "CLAUDE_CODE_SSE_PORT" not in env
    assert "CLAUDE_PROXY_MODEL" not in env


def test_claude_proxy_service_records_rate_limit_cooldown():
    service = ClaudeProxyService(
        cli_bin="claude",
        default_model="sonnet",
        default_timeout=30,
        max_concurrency=1,
        cache_ttl_seconds=60,
        cache_max_size=16,
        sandbox_mode="hard",
        sandbox_root="/tmp/test-claude-proxy",
        rate_limit_cooldown_seconds=30,
    )

    before = service._next_available_at
    assert service._looks_like_rate_limit("429 code=1302 您的账户已达到速率限制")

    service._record_rate_limit()

    assert service._next_available_at > before


def test_claude_proxy_service_tmux_backend_renders_prompt_and_parses_json(monkeypatch):
    calls = {}

    class FakeRunner:
        def __init__(self, **kwargs):
            calls["init"] = kwargs

        def run(self, prompt, timeout):
            calls["prompt"] = prompt
            calls["timeout"] = timeout
            return TmuxClaudeResult(
                text='{"answer":"ok"}',
                usage={"input_tokens": 5, "output_tokens": 3},
                session_id="tmux-session",
                duration_ms=1234,
                tool_calls=[],
            )

    monkeypatch.setattr(service_module, "TmuxClaudeRunner", FakeRunner)

    service = ClaudeProxyService(
        cli_bin="claude",
        default_model="sonnet",
        default_timeout=30,
        max_concurrency=1,
        cache_ttl_seconds=60,
        cache_max_size=16,
        sandbox_mode="hard",
        sandbox_root="/tmp/test-claude-proxy",
        backend="tmux",
        child_env_overrides={"ANTHROPIC_AUTH_TOKEN": "token"},
    )

    response = service._invoke_sync(
        ClaudeProxyRequest(
            prompt="USER:\n返回答案",
            system_prompt="你只返回 JSON",
            json_schema={
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
            },
        )
    )

    assert calls["init"]["model"] == "sonnet"
    assert "独立文本任务" in calls["prompt"]
    assert "USER SYSTEM INSTRUCTIONS:\n你只返回 JSON" in calls["prompt"]
    assert "JSON Schema" in calls["prompt"]
    assert calls["timeout"] == 30
    assert response.structured_output == {"answer": "ok"}
    assert response.session_id == "tmux-session"


def test_pooled_tmux_sessions_use_isolated_state_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(PooledTmuxClaudeSession, "_start", lambda self: None)
    monkeypatch.setattr(PooledTmuxClaudeSession, "_wait_until_ready", lambda self: None)
    monkeypatch.setattr(tmux_module, "_session_line_count", lambda home, session_id: 0)

    first = PooledTmuxClaudeSession(
        pool_id=0,
        cli_bin="claude",
        model="sonnet",
        env={},
        sandbox_root=tmp_path,
    )
    second = PooledTmuxClaudeSession(
        pool_id=1,
        cli_bin="claude",
        model="sonnet",
        env={},
        sandbox_root=tmp_path,
    )

    assert first.home_dir == tmp_path / "tmux-pool" / "session-0" / "home"
    assert second.home_dir == tmp_path / "tmux-pool" / "session-1" / "home"
    assert first.workdir != second.workdir
    assert first.tmux_buffer != second.tmux_buffer


def test_pooled_tmux_send_uses_named_buffer(monkeypatch, tmp_path):
    monkeypatch.setattr(PooledTmuxClaudeSession, "_start", lambda self: None)
    monkeypatch.setattr(PooledTmuxClaudeSession, "_wait_until_ready", lambda self: None)
    monkeypatch.setattr(tmux_module, "_session_line_count", lambda home, session_id: 0)
    monkeypatch.setattr(tmux_module.time, "sleep", lambda seconds: None)
    calls = []

    def fake_tmux(*args):
        calls.append(args)
        return ""

    monkeypatch.setattr(tmux_module, "_tmux", fake_tmux)

    session = PooledTmuxClaudeSession(
        pool_id=2,
        cli_bin="claude",
        model="sonnet",
        env={},
        sandbox_root=tmp_path,
    )

    session._send("hello")

    assert ("load-buffer", "-b", session.tmux_buffer) == calls[0][:3]
    assert ("paste-buffer", "-p", "-b", session.tmux_buffer, "-t", session.tmux_session) in calls
    assert ("delete-buffer", "-b", session.tmux_buffer) in calls


def test_chat_completion_helpers_map_messages_and_schema():
    llm_proxy = _load_llm_proxy_module()
    messages = [
        llm_proxy.ChatMessage(role="system", content="你只返回 JSON"),
        llm_proxy.ChatMessage(role="user", content="给我一个结果"),
    ]

    system_prompt, prompt = llm_proxy._split_messages(messages)
    schema = llm_proxy._resolve_json_schema(
        llm_proxy.ResponseFormatConfig(
            type="json_schema",
            json_schema={
                "name": "result_payload",
                "schema": {
                    "type": "object",
                    "properties": {"result": {"type": "string"}},
                    "required": ["result"],
                },
            },
        )
    )

    usage = llm_proxy._normalize_usage(
        {
            "input_tokens": 3,
            "output_tokens": 2,
            "prompt_cache_hit_tokens": 1,
            "prompt_cache_miss_tokens": 2,
            "reasoning_tokens": 0,
        }
    )

    assert system_prompt == "你只返回 JSON"
    assert prompt == "USER:\n给我一个结果"
    assert schema["type"] == "object"
    assert usage["total_tokens"] == 5
    assert usage["prompt_cache_hit_tokens"] == 1
    assert usage["prompt_cache_miss_tokens"] == 2
    assert usage["completion_tokens_details"] == {"reasoning_tokens": 0}


def test_embeddings_endpoint_maps_remote_embedding_service(monkeypatch):
    llm_proxy = _load_llm_proxy_module()
    calls = {}

    async def fake_embed_texts(texts, dim, normalize):
        calls["texts"] = texts
        calls["dim"] = dim
        calls["normalize"] = normalize
        return [[0.1, 0.2], [0.3, 0.4]]

    monkeypatch.setattr(llm_proxy, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(llm_proxy.settings, "EMBEDDING_DIM", 1024)
    monkeypatch.setattr(llm_proxy.settings, "EMBEDDING_MIN_DIM", 1)
    monkeypatch.setattr(llm_proxy.settings, "EMBEDDING_MAX_DIM", 2560)
    monkeypatch.setattr(llm_proxy.settings, "EMBEDDING_MODEL", "/models/Qwen3-Embedding-4B")
    monkeypatch.setattr(llm_proxy.settings, "EMBEDDING_URL", "http://embedding.local")
    monkeypatch.setattr(llm_proxy.settings, "EMBEDDING_BATCH_SIZE", 32)

    response = asyncio.run(
        llm_proxy.embeddings(
            llm_proxy.EmbeddingRequest(
                input=["AI算力", "液冷设备"],
                model="qwen3-embedding-4b",
                dimensions=2,
            )
        )
    )

    assert calls == {
        "texts": ["AI算力", "液冷设备"],
        "dim": 2,
        "normalize": True,
    }
    assert response["object"] == "list"
    assert response["data"][0]["embedding"] == [0.1, 0.2]
    assert response["_proxy"]["base_url"] == "http://embedding.local"
