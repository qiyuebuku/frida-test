"""Tests for application-level knowledge adapter registry."""

import pytest

from src.application.services.knowledge_adapter_registry import (
    AdapterNotFoundError,
    get_adapter,
    list_adapters,
)


def test_financial_adapter_is_registered() -> None:
    adapter = get_adapter("financial")

    assert adapter.spec.name == "financial"
    assert "financial" in list_adapters()


def test_unknown_adapter_raises_clear_error() -> None:
    with pytest.raises(AdapterNotFoundError, match="adapter_name 不支持"):
        get_adapter("missing")
