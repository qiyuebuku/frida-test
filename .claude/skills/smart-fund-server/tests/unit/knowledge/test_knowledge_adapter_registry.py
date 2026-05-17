"""Knowledge adapter registry tests."""

from __future__ import annotations

import pytest

from src.application.services.knowledge_adapter_registry import AdapterNotFoundError, get_adapter, list_adapters
from src.domain.knowledge_adapters.financial.normalization import NormalizationRules
from src.infrastructure.persistence.repositories import knowledge_normalization_rule_repository as rule_repo_module


def test_financial_adapter_is_registered(monkeypatch) -> None:
    _install_fake_rule_repository(monkeypatch)

    adapter = get_adapter("financial", target="test")

    assert adapter.spec.name == "financial"
    assert "financial" in list_adapters()


def test_unknown_adapter_raises_clear_error() -> None:
    with pytest.raises(AdapterNotFoundError, match="adapter_name 不支持"):
        get_adapter("missing")


def test_financial_adapter_bootstraps_baseline_rules_before_loading(monkeypatch) -> None:
    calls: list[tuple[str, str, int] | tuple[str, str]] = []

    _install_fake_rule_repository(monkeypatch, calls=calls)

    adapter = get_adapter("financial", target="test")

    assert calls[0][0:2] == ("ensure", "financial")
    assert calls[1] == ("load", "financial")
    assert adapter.normalization_rules.aliases["红利资产"] == "高股息"


def _install_fake_rule_repository(
    monkeypatch: pytest.MonkeyPatch,
    *,
    calls: list[tuple[str, str, int] | tuple[str, str]] | None = None,
) -> None:
    class FakeRuleRepository:
        def __init__(self, target: str | None = None):
            self.target = target

        def ensure_active_rules(self, adapter_name: str, baseline_rules: list[dict]) -> int:
            if calls is not None:
                calls.append(("ensure", adapter_name, len(baseline_rules)))
            return len(baseline_rules)

        def load_active_rules(self, adapter_name: str) -> NormalizationRules:
            if calls is not None:
                calls.append(("load", adapter_name))
            return NormalizationRules(
                aliases={"红利资产": "高股息"},
                weak_suffixes=("主题",),
                preserved_suffixes=("产业链",),
                generic_policy_suffixes=("政策",),
                concrete_policy_hints=("规则",),
                concept_taxonomy_default="business",
                concept_taxonomy_industry_chain="industry_chain",
                concept_taxonomy_policy_theme="policy_theme",
            )

    monkeypatch.setattr(rule_repo_module, "KnowledgeNormalizationRuleRepository", FakeRuleRepository)
