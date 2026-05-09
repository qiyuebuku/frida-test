import asyncio

import pytest

from src.infrastructure.llm_proxy.types import LLMProxyResponse
from src.interfaces.api.routes import llm_proxy


class FakeGateway:
    def __init__(self):
        self.requests = []

    def health(self):
        return {
            "status": "ok",
            "default_provider": "claude_tmux",
            "default_model": "glm-5.1",
            "model_routes": {"deepseek-v4-flash": ["deepseek"]},
            "providers": {"deepseek": {"api_key_configured": True}},
        }

    async def generate(self, request):
        self.requests.append(request)
        return LLMProxyResponse(
            text="ok",
            structured_output=None,
            usage={"input_tokens": 2, "output_tokens": 3},
            session_id="sess",
            duration_ms=10,
            raw_payload={},
            proxy={
                "provider": "deepseek",
                "requested_model": request.model,
                "resolved_model": request.model,
                "upstream_model": request.model,
                "route_reason": "model_exact",
                "retry_count": 0,
            },
        )


def test_llm_proxy_health_lists_routes_and_providers(monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr(llm_proxy, "get_llm_gateway_service", lambda: gateway)

    response = asyncio.run(llm_proxy.llm_proxy_health())

    assert response["model_routes"]["deepseek-v4-flash"] == ["deepseek"]
    assert response["providers"]["deepseek"]["api_key_configured"] is True


def test_chat_completion_response_is_openai_compatible(monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr(llm_proxy, "get_llm_gateway_service", lambda: gateway)

    response = asyncio.run(
        llm_proxy.chat_completions(
            llm_proxy.ChatCompletionRequest(
                model="deepseek-v4-flash",
                messages=[llm_proxy.ChatMessage(role="user", content="hello")],
            )
        )
    )

    assert response["object"] == "chat.completion"
    assert response["model"] == "deepseek-v4-flash"
    assert response["choices"][0]["message"]["content"] == "ok"
    assert response["usage"] == {
        "prompt_tokens": 2,
        "completion_tokens": 3,
        "total_tokens": 5,
    }
    assert response["_proxy"]["provider"] == "deepseek"
    assert gateway.requests[0].model == "deepseek-v4-flash"


def test_chat_completion_error_maps_to_502(monkeypatch):
    from src.infrastructure.llm_proxy.types import LLMProxyError

    class ErrorGateway(FakeGateway):
        async def generate(self, request):
            raise LLMProxyError("upstream failed")

    monkeypatch.setattr(llm_proxy, "get_llm_gateway_service", lambda: ErrorGateway())

    with pytest.raises(llm_proxy.HTTPException) as exc:
        asyncio.run(
            llm_proxy.chat_completions(
                llm_proxy.ChatCompletionRequest(
                    model="deepseek-v4-flash",
                    messages=[llm_proxy.ChatMessage(role="user", content="hello")],
                )
            )
        )

    assert exc.value.status_code == 502
