import asyncio

from src.infrastructure.llm_proxy.registry import ProviderRegistry
from src.infrastructure.llm_proxy.router import ModelRouter, ModelRouterConfig
from src.infrastructure.llm_proxy.cache import LLMPersistentFileCache
from src.infrastructure.llm_proxy.service import (
    LLMGatewayService,
    _json_schema_validation_issues,
    _llm_trace_input,
)
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMProxyResponse


class EchoProvider:
    def __init__(self, name, *, structured_output=None):
        self.name = name
        self.enabled = True
        self.calls = []
        self.structured_output = structured_output

    async def generate(self, request, route):
        self.calls.append((request, route))
        return LLMProxyResponse(
            text=f"{self.name}:{route.resolved_model}" if self.structured_output is None else '{"ok": true}',
            structured_output=self.structured_output,
            usage={"input_tokens": 1, "output_tokens": 1},
            session_id=None,
            duration_ms=1,
            raw_payload={},
            proxy={"provider": self.name},
        )

    def health(self):
        return {"enabled": self.enabled}

    def runtime_stats(self):
        return {}

    def supports(self, model):
        return True


class SequenceProvider(EchoProvider):
    def __init__(self, name, outputs):
        super().__init__(name)
        self.outputs = list(outputs)

    async def generate(self, request, route):
        self.calls.append((request, route))
        output = self.outputs.pop(0)
        return LLMProxyResponse(
            text=output.get("text", ""),
            structured_output=output.get("structured_output"),
            usage={"input_tokens": 1, "output_tokens": 1},
            session_id=None,
            duration_ms=1,
            raw_payload={},
            proxy={"provider": self.name},
        )


def _service():
    registry = ProviderRegistry()
    deepseek = EchoProvider("deepseek")
    claude = EchoProvider("claude_tmux")
    registry.register(deepseek)
    registry.register(claude)
    router = ModelRouter(
        ModelRouterConfig(
            default_model="glm-5.1",
            default_provider="claude_tmux",
            model_routes={
                "deepseek-v4-flash": ["deepseek"],
                "glm-5.1": ["claude_tmux"],
            },
            model_aliases={"glm5.1": "glm-5.1"},
        )
    )
    return LLMGatewayService(
        router=router,
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=16,
    ), deepseek, claude


def test_gateway_routes_deepseek_model_to_deepseek_provider():
    service, deepseek, claude = _service()

    response = asyncio.run(
        service.generate(LLMProxyRequest(prompt="hello", model="deepseek-v4-flash"))
    )

    assert response.text == "deepseek:deepseek-v4-flash"
    assert len(deepseek.calls) == 1
    assert len(claude.calls) == 0


def test_llm_trace_input_exposes_safe_reasoning_options_only():
    trace_input = _llm_trace_input(
        LLMProxyRequest(
            prompt="hello",
            provider_options={
                "reasoning_effort": "high",
                "thinking_type": "enabled",
                "authorization": "secret",
            },
        )
    )

    assert trace_input["provider_options"] == {
        "reasoning_effort": "high",
        "thinking_type": "enabled",
    }


def test_gateway_routes_glm_alias_to_claude_tmux_provider():
    service, _deepseek, claude = _service()

    response = asyncio.run(service.generate(LLMProxyRequest(prompt="hello", model="glm5.1")))

    assert response.text == "claude_tmux:glm-5.1"
    assert response.proxy["resolved_model"] == "glm-5.1"
    assert response.proxy["route_reason"] == "alias"
    assert len(claude.calls) == 1


def test_gateway_cache_key_includes_provider():
    service, deepseek, _claude = _service()
    request = LLMProxyRequest(prompt="hello", model="deepseek-v4-flash")

    first = asyncio.run(service.generate(request))
    second = asyncio.run(service.generate(request))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert len(deepseek.calls) == 1


def test_gateway_uses_persistent_file_cache(tmp_path):
    service, deepseek, _claude = _service()
    service._file_cache = LLMPersistentFileCache(tmp_path, enabled=True)
    request = LLMProxyRequest(prompt="hello", model="deepseek-v4-flash")

    first = asyncio.run(service.generate(request))

    restarted_service, restarted_deepseek, _restarted_claude = _service()
    restarted_service._file_cache = LLMPersistentFileCache(tmp_path, enabled=True)
    second = asyncio.run(restarted_service.generate(request))

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.text == "deepseek:deepseek-v4-flash"
    assert second.proxy["cache_store"] == "file"
    assert len(deepseek.calls) == 1
    assert len(restarted_deepseek.calls) == 0


def test_gateway_does_not_cache_unstructured_json_response():
    service, deepseek, _claude = _service()
    request = LLMProxyRequest(prompt="hello", model="deepseek-v4-flash", json_schema={"type": "object"})

    first = asyncio.run(service.generate(request))
    second = asyncio.run(service.generate(request))

    assert first.cache_hit is False
    assert second.cache_hit is False
    assert first.proxy["schema_repair_attempts"] == 3
    assert second.proxy["schema_repair_attempts"] == 3
    assert len(deepseek.calls) == 8


def test_gateway_ignores_bad_file_cache_for_json_request(tmp_path):
    service, deepseek, _claude = _service()
    service._file_cache = LLMPersistentFileCache(tmp_path, enabled=True)
    request = LLMProxyRequest(prompt="hello", model="deepseek-v4-flash", json_schema={"type": "object"})
    key = service._cache_key(request, "deepseek", "deepseek-v4-flash")
    service._file_cache.set(
        key,
        LLMProxyResponse(
            text="not json",
            structured_output=None,
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
            proxy={"provider": "deepseek"},
        ),
    )

    response = asyncio.run(service.generate(request))

    assert response.cache_hit is False
    assert response.text == "deepseek:deepseek-v4-flash"
    assert response.proxy["schema_repair_attempts"] == 3
    assert len(deepseek.calls) == 4


def test_gateway_no_cache_retry_overwrites_original_cache_key(tmp_path):
    registry = ProviderRegistry()
    deepseek = EchoProvider("deepseek", structured_output={"ok": True})
    registry.register(deepseek)
    router = ModelRouter(
        ModelRouterConfig(
            default_model="deepseek-v4-flash",
            default_provider="deepseek",
            model_routes={"deepseek-v4-flash": ["deepseek"]},
            model_aliases={},
        )
    )
    service = LLMGatewayService(
        router=router,
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=16,
        file_cache=LLMPersistentFileCache(tmp_path, enabled=True),
    )
    request = LLMProxyRequest(
        prompt="hello",
        model="deepseek-v4-flash",
        json_schema={"type": "object"},
    )
    retry_request = LLMProxyRequest(
        prompt="hello",
        model="deepseek-v4-flash",
        messages=[
            {"role": "system", "content": "system"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "bad"},
            {"role": "user", "content": "repair it"},
        ],
        json_schema={"type": "object"},
        metadata={
            "retry_reason": "schema_invalid_after_cache_hit",
            "_cache_key_prompt": "hello",
            "_cache_key_system_prompt": None,
            "_cache_key_messages": [],
        },
        use_cache=False,
    )

    normal_key = service._cache_key(request, "deepseek", "deepseek-v4-flash")
    retry_key = service._cache_key(retry_request, "deepseek", "deepseek-v4-flash")

    assert retry_key == normal_key

    retry_response = asyncio.run(service.generate(retry_request))
    cached_response = asyncio.run(service.generate(request))

    assert retry_response.cache_hit is False
    assert cached_response.cache_hit is True
    assert cached_response.structured_output == {"ok": True}
    assert len(deepseek.calls) == 1


def test_gateway_cache_key_metadata_override_ignores_trace_metadata():
    service, _deepseek, _claude = _service()
    request_a = LLMProxyRequest(
        prompt="same prompt",
        system_prompt="same system",
        model="deepseek-v4-flash",
        metadata={
            "task": "kg_cognitive_card",
            "source_id": "run-a:ft_news:1",
            "chunk_id": "run-a:chunk:1",
            "_cache_key_metadata": {"task": "kg_cognitive_card"},
        },
    )
    request_b = LLMProxyRequest(
        prompt="same prompt",
        system_prompt="same system",
        model="deepseek-v4-flash",
        metadata={
            "task": "kg_cognitive_card",
            "source_id": "run-b:ft_news:1",
            "chunk_id": "run-b:chunk:1",
            "_cache_key_metadata": {"task": "kg_cognitive_card"},
        },
    )

    assert service._cache_key(request_a, "deepseek", "deepseek-v4-flash") == service._cache_key(
        request_b,
        "deepseek",
        "deepseek-v4-flash",
    )


def test_gateway_repairs_json_schema_invalid_response_and_caches_repaired_result(tmp_path):
    registry = ProviderRegistry()
    deepseek = SequenceProvider(
        "deepseek",
        [
            {"structured_output": {"decision": "create_new_canonical_entity"}},
            {
                "structured_output": {
                    "decision": "create_new_canonical_entity",
                    "canonical_name": "广东",
                    "confidence": 0.85,
                }
            },
        ],
    )
    registry.register(deepseek)
    router = ModelRouter(
        ModelRouterConfig(
            default_model="deepseek-v4-flash",
            default_provider="deepseek",
            model_routes={"deepseek-v4-flash": ["deepseek"]},
            model_aliases={},
        )
    )
    service = LLMGatewayService(
        router=router,
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=16,
        file_cache=LLMPersistentFileCache(tmp_path, enabled=True),
    )
    request = LLMProxyRequest(
        prompt='{"entity":"广东"}',
        system_prompt="只输出 JSON",
        model="deepseek-v4-flash",
        json_schema={
            "type": "object",
            "properties": {
                "decision": {"type": "string"},
                "canonical_name": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["decision", "canonical_name", "confidence"],
            "additionalProperties": False,
        },
    )

    repaired = asyncio.run(service.generate(request))
    cached = asyncio.run(service.generate(request))

    assert repaired.cache_hit is False
    assert repaired.structured_output["canonical_name"] == "广东"
    assert repaired.proxy["schema_repair_attempted"] is True
    assert repaired.proxy["schema_repair_success"] is True
    assert cached.cache_hit is True
    assert cached.structured_output["canonical_name"] == "广东"
    assert len(deepseek.calls) == 2
    repair_request = deepseek.calls[1][0]
    assert repair_request.use_cache is False
    assert repair_request.response_format == {"type": "json_object"}
    assert repair_request.metadata["retry_reason"] == "json_schema_invalid"
    assert "validation_issues" in repair_request.messages[-1]["content"]


def test_gateway_repeats_json_schema_repair_until_valid(tmp_path):
    registry = ProviderRegistry()
    deepseek = SequenceProvider(
        "deepseek",
        [
            {"structured_output": ["not", "object"]},
            {"structured_output": {"decision": "create_new_canonical_entity"}},
            {
                "structured_output": {
                    "decision": "create_new_canonical_entity",
                    "canonical_name": "广东",
                    "confidence": 0.85,
                }
            },
        ],
    )
    registry.register(deepseek)
    router = ModelRouter(
        ModelRouterConfig(
            default_model="deepseek-v4-flash",
            default_provider="deepseek",
            model_routes={"deepseek-v4-flash": ["deepseek"]},
            model_aliases={},
        )
    )
    service = LLMGatewayService(
        router=router,
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=16,
        file_cache=LLMPersistentFileCache(tmp_path, enabled=True),
    )
    request = LLMProxyRequest(
        prompt='{"entity":"广东"}',
        system_prompt="只输出 JSON",
        model="deepseek-v4-flash",
        json_schema={
            "type": "object",
            "properties": {
                "decision": {"type": "string"},
                "canonical_name": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["decision", "canonical_name", "confidence"],
            "additionalProperties": False,
        },
    )

    repaired = asyncio.run(service.generate(request))
    cached = asyncio.run(service.generate(request))

    assert repaired.structured_output["canonical_name"] == "广东"
    assert repaired.proxy["schema_repair_attempted"] is True
    assert repaired.proxy["schema_repair_attempts"] == 2
    assert repaired.proxy["schema_repair_success"] is True
    assert cached.cache_hit is True
    assert cached.structured_output["canonical_name"] == "广东"
    assert len(deepseek.calls) == 3
    first_repair_request = deepseek.calls[1][0]
    second_repair_request = deepseek.calls[2][0]
    assert len(second_repair_request.messages) > len(first_repair_request.messages)
    assert second_repair_request.metadata["retry_reason"] == "json_schema_invalid"
    assert "validation_issues" in second_repair_request.messages[-1]["content"]


def test_gateway_repairs_with_caller_feedback_and_overwrites_original_cache(tmp_path):
    registry = ProviderRegistry()
    deepseek = SequenceProvider(
        "deepseek",
        [
            {"structured_output": {"title": "单公司项目"}},
            {"structured_output": {"title": "影视产业转型"}},
        ],
    )
    registry.register(deepseek)
    router = ModelRouter(
        ModelRouterConfig(
            default_model="deepseek-v4-flash",
            default_provider="deepseek",
            model_routes={"deepseek-v4-flash": ["deepseek"]},
            model_aliases={},
        )
    )
    service = LLMGatewayService(
        router=router,
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=16,
        file_cache=LLMPersistentFileCache(tmp_path, enabled=True),
    )
    request = LLMProxyRequest(
        prompt='{"topic":"横店影视"}',
        system_prompt="只输出 JSON",
        model="deepseek-v4-flash",
        provider_options={"reasoning_effort": "high"},
        json_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
    )

    first = asyncio.run(service.generate(request))
    repaired = asyncio.run(
        service.repair_with_feedback(
            request,
            first,
            ["new_community.title is not a valid broad L0 title"],
            instruction="请把单公司项目标题上提为可复用父级主题。",
            retry_reason="community_assignment_validation_invalid",
        )
    )
    cached = asyncio.run(service.generate(request))

    assert first.structured_output == {"title": "单公司项目"}
    assert repaired.structured_output == {"title": "影视产业转型"}
    assert repaired.proxy["retry_count"] == 0
    assert cached.cache_hit is True
    assert cached.structured_output == {"title": "影视产业转型"}
    assert len(deepseek.calls) == 2
    repair_request = deepseek.calls[1][0]
    assert repair_request.use_cache is False
    assert repair_request.metadata["retry_reason"] == "community_assignment_validation_invalid"
    assert repair_request.metadata["validation_issues"] == ["new_community.title is not a valid broad L0 title"]
    assert repair_request.provider_options == {"reasoning_effort": "high"}
    assert repair_request.messages[0]["role"] == "system"
    assert repair_request.messages[1]["role"] == "user"
    assert repair_request.messages[2]["role"] == "assistant"
    assert "validation_issues" in repair_request.messages[-1]["content"]


def test_gateway_repeats_caller_feedback_repair_until_schema_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_PROXY_SCHEMA_REPAIR_MAX_ATTEMPTS", "1")
    registry = ProviderRegistry()
    deepseek = SequenceProvider(
        "deepseek",
        [
            {"structured_output": None},
            {"structured_output": None},
            {"structured_output": None},
            {"structured_output": None},
            {"structured_output": {"title": "AI算力链"}},
        ],
    )
    registry.register(deepseek)
    router = ModelRouter(
        ModelRouterConfig(
            default_model="deepseek-v4-flash",
            default_provider="deepseek",
            model_routes={"deepseek-v4-flash": ["deepseek"]},
            model_aliases={},
        )
    )
    service = LLMGatewayService(
        router=router,
        registry=registry,
        cache_ttl_seconds=60,
        cache_max_size=16,
        file_cache=LLMPersistentFileCache(tmp_path, enabled=True),
    )
    request = LLMProxyRequest(
        prompt='{"topic":"AI芯片短缺"}',
        system_prompt="只输出 JSON",
        model="deepseek-v4-flash",
        json_schema={
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
            "additionalProperties": False,
        },
    )

    first = asyncio.run(service.generate(request))
    repaired = asyncio.run(
        service.repair_with_feedback(
            request,
            first,
            ["output must be JSON object"],
            instruction="请修复为合法 JSON object。",
            retry_reason="cognitive_card_validation_invalid",
        )
    )
    cached = asyncio.run(service.generate(request))

    assert repaired.structured_output == {"title": "AI算力链"}
    assert repaired.proxy["feedback_repair_attempted"] is True
    assert repaired.proxy["feedback_repair_attempts"] == 2
    assert repaired.proxy["feedback_repair_success"] is True
    assert cached.cache_hit is True
    assert cached.structured_output == {"title": "AI算力链"}
    assert len(deepseek.calls) == 5
    first_repair_request = deepseek.calls[2][0]
    second_repair_request = deepseek.calls[4][0]
    assert first_repair_request.metadata["retry_reason"] == "cognitive_card_validation_invalid"
    assert second_repair_request.metadata["retry_reason"] == "cognitive_card_validation_invalid"
    assert len(second_repair_request.messages) > len(first_repair_request.messages)
    assert "validation_issues" in second_repair_request.messages[-1]["content"]


def test_json_schema_validation_supports_conditional_assignment_schema():
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["attach_existing", "create_new_l0"]},
            "community_id": {"type": ["string", "null"]},
            "new_community": {
                "type": ["object", "null"],
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
        },
        "allOf": [
            {
                "if": {"properties": {"action": {"const": "attach_existing"}}, "required": ["action"]},
                "then": {"properties": {"community_id": {"type": "string"}, "new_community": {"type": "null"}}},
            },
            {
                "if": {"properties": {"action": {"const": "create_new_l0"}}, "required": ["action"]},
                "then": {"properties": {"community_id": {"type": "null"}, "new_community": {"type": "object"}}},
            },
        ],
        "required": ["action", "community_id", "new_community"],
        "additionalProperties": False,
    }

    assert not _json_schema_validation_issues(
        {"action": "attach_existing", "community_id": "c1", "new_community": None},
        schema,
    )
    assert not _json_schema_validation_issues(
        {"action": "create_new_l0", "community_id": None, "new_community": {"title": "服务贸易政策"}},
        schema,
    )
    assert _json_schema_validation_issues(
        {"action": "attach_existing", "community_id": "c1", "new_community": {"title": "placeholder"}},
        schema,
    )
    assert _json_schema_validation_issues(
        {"action": "create_new_l0", "community_id": "c1", "new_community": None},
        schema,
    )


def test_gateway_ignores_schema_invalid_file_cache(tmp_path):
    service, deepseek, _claude = _service()
    service._file_cache = LLMPersistentFileCache(tmp_path, enabled=True)
    request = LLMProxyRequest(
        prompt="hello",
        model="deepseek-v4-flash",
        json_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    )
    normalized_request = LLMProxyRequest(
        prompt="hello",
        model="deepseek-v4-flash",
        json_schema=request.json_schema,
        response_format={"type": "json_object"},
    )
    key = service._cache_key(normalized_request, "deepseek", "deepseek-v4-flash")
    service._file_cache.set(
        key,
        LLMProxyResponse(
            text='{"bad": true}',
            structured_output={"bad": True},
            usage={},
            session_id=None,
            duration_ms=1,
            raw_payload={},
            proxy={"provider": "deepseek"},
        ),
    )

    response = asyncio.run(service.generate(request))

    assert response.cache_hit is False
    assert response.proxy["schema_repair_attempts"] == 3
    assert len(deepseek.calls) == 4


def test_gateway_health_lists_routes_and_providers():
    service, _deepseek, _claude = _service()

    health = service.health()

    assert health["model_routes"]["deepseek-v4-flash"] == ["deepseek"]
    assert sorted(health["providers"]) == ["claude_tmux", "deepseek"]
    assert health["cache"]["memory_max_size"] == 16
