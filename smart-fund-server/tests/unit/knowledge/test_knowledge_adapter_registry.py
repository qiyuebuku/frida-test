"""Knowledge adapter registry tests."""

from __future__ import annotations

import pytest

from src.application.services.knowledge_adapter_registry import AdapterNotFoundError, get_adapter, list_adapters
from src.application.services.normalization_semantic_candidates import MilvusNormalizationCandidateProvider
from src.domain.knowledge_adapters.financial.normalization import NormalizationRules
from src.infrastructure.persistence.repositories import knowledge_normalization_rule_repository as rule_repo_module


def test_financial_adapter_is_registered(monkeypatch) -> None:
    _install_fake_rule_repository(monkeypatch)

    adapter = get_adapter("financial", target="test")

    assert adapter.spec.name == "financial"
    assert "financial" in list_adapters()
    assert isinstance(
        adapter.normalization_decision_strategy._semantic_candidate_provider,
        MilvusNormalizationCandidateProvider,
    )


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


def test_financial_adapter_backfills_active_type_registry(monkeypatch) -> None:
    _install_fake_rule_repository(
        monkeypatch,
        type_rules=[
            {
                "rule_type": "entity_type",
                "raw_value": "infrastructure_theme",
                "canonical_value": "基础设施型产业主题",
                "status": "active",
                "payload": {},
            },
            {
                "rule_type": "relation_type",
                "raw_value": "constrains",
                "canonical_value": "约束或限制",
                "status": "active",
                "payload": {},
            },
        ],
    )

    adapter = get_adapter("financial", target="test")

    assert "infrastructure_theme" in {item.name for item in adapter.spec.entities}
    assert "constrains" in {item.name for item in adapter.spec.relations}
    schema = adapter.news_extraction_strategy._json_schema
    entity_enum = schema["properties"]["entities"]["items"]["properties"]["type"]["enum"]
    relation_enum = schema["properties"]["relations"]["items"]["properties"]["relation_type"]["enum"]
    assert "infrastructure_theme" in entity_enum
    assert "constrains" in relation_enum


def _install_fake_rule_repository(
    monkeypatch: pytest.MonkeyPatch,
    *,
    calls: list[tuple[str, str, int] | tuple[str, str]] | None = None,
    type_rules: list[dict] | None = None,
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

        def list_rules(self, adapter_name: str, status: str | None = None) -> list[dict]:
            del adapter_name
            rules = list(type_rules or [])
            if status is None:
                return rules
            return [rule for rule in rules if rule.get("status") == status]

    monkeypatch.setattr(rule_repo_module, "KnowledgeNormalizationRuleRepository", FakeRuleRepository)
