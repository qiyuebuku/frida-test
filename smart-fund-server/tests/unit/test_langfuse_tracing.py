from __future__ import annotations

import langfuse
import pytest

from src.infrastructure.observability import langfuse_tracing


@pytest.fixture(autouse=True)
def clear_langfuse_client_cache() -> None:
    langfuse_tracing._build_langfuse_client.cache_clear()
    yield
    langfuse_tracing._build_langfuse_client.cache_clear()


def test_client_is_pinned_to_self_hosted_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}
    client = object()

    def fake_build(base_url: str, public_key: str, secret_key: str):
        captured.update(
            base_url=base_url,
            public_key=public_key,
            secret_key=secret_key,
        )
        return client

    monkeypatch.setenv("KG_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_BASE_URL", "http://127.0.0.1:3001/")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_PUBLIC_KEY", "pk-self-hosted")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_SECRET_KEY", "sk-self-hosted")
    monkeypatch.setattr(langfuse_tracing, "_build_langfuse_client", fake_build)

    assert langfuse_tracing.langfuse_client_or_none() is client
    assert captured == {
        "base_url": "http://127.0.0.1:3001",
        "public_key": "pk-self-hosted",
        "secret_key": "sk-self-hosted",
    }


def test_builder_rejects_sdk_singleton_bound_to_another_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResources:
        base_url = "https://cloud.langfuse.com"

    class FakeClient:
        _resources = FakeResources()

    monkeypatch.setattr(langfuse, "Langfuse", lambda **_kwargs: FakeClient())

    with pytest.raises(RuntimeError, match="already bound to a different endpoint"):
        langfuse_tracing._build_langfuse_client(
            "http://127.0.0.1:3001",
            "pk-self-hosted",
            "sk-self-hosted",
        )


@pytest.mark.parametrize(
    "base_url",
    [
        "https://cloud.langfuse.com",
        "https://us.cloud.langfuse.com",
        "https://eu.cloud.langfuse.com",
        "not-a-url",
    ],
)
def test_client_refuses_official_or_invalid_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    monkeypatch.setenv("KG_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_BASE_URL", base_url)
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr(
        langfuse_tracing,
        "_build_langfuse_client",
        lambda *_args: pytest.fail("official endpoint must not initialize a client"),
    )

    assert langfuse_tracing.langfuse_client_or_none() is None


def test_server_project_ignores_legacy_generic_langfuse_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_build(base_url: str, _public_key: str, _secret_key: str):
        captured["base_url"] = base_url
        return object()

    monkeypatch.setenv("KG_LANGFUSE_ENABLED", "true")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_BASE_URL", "http://langfuse.internal:3000")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_PUBLIC_KEY", "pk-server")
    monkeypatch.setenv("SMART_FUND_SERVER_LANGFUSE_SECRET_KEY", "sk-server")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-legacy")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-legacy")
    monkeypatch.setattr(langfuse_tracing, "_build_langfuse_client", fake_build)

    assert langfuse_tracing.langfuse_client_or_none() is not None
    assert captured["base_url"] == "http://langfuse.internal:3000"


@pytest.mark.parametrize(
    ("has_active_span", "expected_trace_name"),
    [(False, "kg.news_ingest"), (True, None)],
)
def test_propagation_context_only_sets_trace_name_at_root(
    monkeypatch: pytest.MonkeyPatch,
    has_active_span: bool,
    expected_trace_name: str | None,
) -> None:
    captured: dict[str, object] = {}

    def fake_propagate_attributes(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setenv("KG_LANGFUSE_ENABLED", "true")
    monkeypatch.setattr(langfuse_tracing, "_has_active_span", lambda: has_active_span)
    monkeypatch.setattr(langfuse, "propagate_attributes", fake_propagate_attributes)

    context = langfuse_tracing.langfuse_propagation_context(
        trace_name="kg.news_ingest",
        session_id="workflow:1",
    )

    assert context == captured
    assert captured.get("trace_name") == expected_trace_name
