import pytest

from src.infrastructure.llm_proxy.router import ModelRouter, ModelRouterConfig
from src.infrastructure.llm_proxy.types import LLMProxyError


def _router():
    return ModelRouter(
        ModelRouterConfig(
            default_model="glm-5.1",
            default_provider="claude_tmux",
            model_routes={
                "glm-5.1": ["claude_tmux", "glm_http"],
                "deepseek-v4-flash": ["deepseek"],
                "deepseek-v4-pro": ["deepseek"],
            },
            model_aliases={
                "glm5.1": "glm-5.1",
                "deepseek-flash": "deepseek-v4-flash",
            },
        )
    )


def test_exact_model_route_deepseek_flash():
    route = _router().resolve("deepseek-v4-flash")

    assert route.resolved_model == "deepseek-v4-flash"
    assert route.provider_candidates == ["deepseek"]
    assert route.selected_provider == "deepseek"


def test_alias_route_glm51_keeps_provider_priority():
    route = _router().resolve("glm5.1")

    assert route.resolved_model == "glm-5.1"
    assert route.provider_candidates == ["claude_tmux", "glm_http"]
    assert route.route_reason == "alias"


def test_empty_model_uses_default_model():
    route = _router().resolve(None)

    assert route.resolved_model == "glm-5.1"
    assert route.route_reason == "default"


def test_unknown_model_is_deferred_to_provider_catalog():
    route = _router().resolve("unknown-model")

    assert route.provider_candidates == []
    assert route.route_reason == "provider_catalog"


def test_fallback_candidates_do_not_change_resolved_model():
    route = _router().resolve("glm-5.1")

    assert route.resolved_model == "glm-5.1"
    assert route.provider_candidates == ["claude_tmux", "glm_http"]


def test_glob_model_route_supports_aliyun_model_families():
    router = ModelRouter(
        ModelRouterConfig(
            default_model="glm-5.1",
            default_provider="claude_tmux",
            model_routes={
                "glm-5.1": ["claude_tmux"],
                "glm-*": ["aliyun"],
                "kimi*": ["aliyun"],
                "qwen3.7-plus": ["aliyun"],
            },
            model_aliases={},
        )
    )

    qwen_route = router.resolve("qwen3.7-plus")
    assert qwen_route.provider_candidates == ["aliyun"]
    assert qwen_route.route_reason == "model_exact"
    kimi_route = router.resolve("kimi/kimi-k3")
    assert kimi_route.provider_candidates == ["aliyun"]
    assert kimi_route.route_reason == "model_pattern"
    assert router.resolve("glm-5.2").provider_candidates == ["aliyun"]

    embedding_route = router.resolve("qwen3.7-text-embedding")
    assert embedding_route.provider_candidates == []
    assert embedding_route.route_reason == "provider_catalog"


def test_exact_model_route_wins_over_glob_route():
    router = ModelRouter(
        ModelRouterConfig(
            default_model="glm-5.1",
            default_provider="claude_tmux",
            model_routes={
                "glm-5.1": ["claude_tmux"],
                "glm-*": ["aliyun"],
            },
            model_aliases={},
        )
    )

    assert router.resolve("glm-5.1").provider_candidates == ["claude_tmux"]


def test_explicit_provider_overrides_model_provider_preference():
    route = _router().resolve("deepseek-v4-pro", requested_provider="aliyun")

    assert route.provider_candidates == ["aliyun"]
    assert route.selected_provider == "aliyun"
    assert route.requested_provider == "aliyun"
    assert route.route_reason == "provider_explicit"
