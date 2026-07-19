from __future__ import annotations

from pathlib import Path

from src.infrastructure.config import settings


def test_milvus_relative_uri_resolves_against_project_root() -> None:
    expected = Path(settings.__file__).resolve().parents[3] / "data" / "milvus" / "kg_vectors.db"

    assert settings._resolve_local_path_setting("./data/milvus/kg_vectors.db") == str(expected.resolve())


def test_milvus_remote_uri_is_not_treated_as_local_path() -> None:
    assert settings._resolve_local_path_setting("http://localhost:19530") == "http://localhost:19530"
    assert settings._resolve_local_path_setting("unix:/tmp/milvus.sock") == "unix:/tmp/milvus.sock"


def test_aliyun_openai_compatible_provider_is_configured() -> None:
    aliyun = next(
        item
        for item in settings.LLM_PROXY_OPENAI_COMPATIBLE_PROVIDERS
        if item["name"] == "aliyun"
    )

    assert aliyun["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert aliyun["timeout"] == 1800
    assert "qwen3.7-plus" in aliyun["model_patterns"]
    assert aliyun["model_mappings"]["deepseek-v4-pro"] == "vanchin/deepseek-v4-pro"
    assert aliyun["reasoning_style"] == "aliyun"


def test_volcengine_openai_compatible_provider_is_configured() -> None:
    volcengine = next(
        item
        for item in settings.LLM_PROXY_OPENAI_COMPATIBLE_PROVIDERS
        if item["name"] == "volcengine"
    )

    assert volcengine["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert volcengine["timeout"] == 1800
    assert volcengine["model_mappings"]["deepseek-v4-pro"] == "deepseek-v4-pro-260425"
    assert (
        volcengine["model_mappings"]["doubao-seed-2.1-turbo"]
        == "doubao-seed-2-1-turbo-260628"
    )
    assert volcengine["reasoning_style"] == "volcengine"


def test_custom_openai_compatible_provider_reads_key_from_named_env(monkeypatch) -> None:
    monkeypatch.setenv("TEST_VENDOR_API_KEY", "sk-test")
    monkeypatch.setattr(
        settings,
        "LLM_PROXY_OPENAI_COMPATIBLE_PROVIDERS_JSON",
        '{"test_vendor":{"base_url":"https://example.test/v1",'
        '"api_key_env":"TEST_VENDOR_API_KEY","default_model":"test-chat",'
        '"timeout":30,"model_patterns":"test-*",'
        '"model_mappings":{"canonical-test":"vendor-test"}}}',
    )

    configs = settings._load_openai_compatible_provider_configs()
    custom = next(item for item in configs if item["name"] == "test_vendor")

    assert custom["api_key"] == "sk-test"
    assert custom["model_patterns"] == ("test-*",)
    assert custom["model_mappings"] == {"canonical-test": "vendor-test"}
