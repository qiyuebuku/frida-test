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


def test_unknown_model_rejected_when_no_route():
    with pytest.raises(LLMProxyError):
        _router().resolve("unknown-model")


def test_fallback_candidates_do_not_change_resolved_model():
    route = _router().resolve("glm-5.1")

    assert route.resolved_model == "glm-5.1"
    assert route.provider_candidates == ["claude_tmux", "glm_http"]
