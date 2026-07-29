from __future__ import annotations

import langfuse
import pytest

from src.infrastructure.observability import langfuse_tracing


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
