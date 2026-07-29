"""Application-level registry for knowledge domain adapters."""

from __future__ import annotations

from src.domain.knowledge.adapter import DomainAdapter
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.baseline_rules import financial_baseline_normalization_rules
from src.domain.knowledge_adapters.financial.news_extraction import FinancialNewsExtractionStrategy
from src.domain.knowledge_adapters.financial.ontology import extend_financial_adapter_spec
from src.application.services.financial_normalization_decision import FinancialNormalizationDecisionService
from src.application.services.knowledge_llm_extraction_service import KnowledgeLLMExtractionService
from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.application.services.normalization_semantic_candidates import MilvusNormalizationCandidateProvider


class AdapterNotFoundError(ValueError):
    """Raised when a requested knowledge adapter is not registered."""


def get_adapter(adapter_name: str, *, target: str | None = None) -> DomainAdapter:
    name = (adapter_name or "").strip()
    if name == "financial":
        return _build_financial_adapter(target=target)
    raise AdapterNotFoundError(f"adapter_name 不支持: {adapter_name}")


def _build_financial_adapter(*, target: str | None = None) -> FinancialKGAdapter:
    try:
        from src.infrastructure.llm_proxy.service import get_llm_gateway_service

        llm_service = get_llm_gateway_service()
        llm_extraction_port = KnowledgeLLMExtractionService(llm_service)
    except Exception:
        llm_service = None
        llm_extraction_port = None
    from src.infrastructure.persistence.repositories.knowledge_normalization_rule_repository import (
        KnowledgeNormalizationRuleRepository,
    )

    rule_repository = KnowledgeNormalizationRuleRepository(target=target)
    rule_repository.ensure_active_rules("financial", financial_baseline_normalization_rules())
    normalization_rules = rule_repository.load_active_rules("financial")
    type_registry = _active_type_registry(rule_repository)
    adapter_spec = extend_financial_adapter_spec(
        extra_entity_types={
            item["type_name"]
            for item in type_registry
            if item.get("type_kind") == "entity_type" and item.get("type_name")
        },
        extra_relation_types={
            item["type_name"]
            for item in type_registry
            if item.get("type_kind") == "relation_type" and item.get("type_name")
        },
    )
    return FinancialKGAdapter(
        news_extraction_strategy=FinancialNewsExtractionStrategy(
            llm_port=llm_extraction_port,
            llm_model=resolve_kg_llm_model("financial_news_extraction"),
            allowed_entity_types={item.name for item in adapter_spec.entities},
            allowed_relation_types={item.name for item in adapter_spec.relations},
            active_type_registry=type_registry,
        ),
        adapter_spec=adapter_spec,
        normalization_rules=normalization_rules,
        normalization_decision_strategy=FinancialNormalizationDecisionService(
            llm_service=llm_service,
            llm_model=resolve_kg_llm_model("financial_entity_normalization"),
            rule_repository=rule_repository,
            rules=normalization_rules,
            semantic_candidate_provider=MilvusNormalizationCandidateProvider(
                adapter_name="financial",
                target=target or "prod",
            ),
        ),
    )


def list_adapters() -> list[str]:
    return ["financial"]


def _active_type_registry(rule_repository) -> list[dict]:
    try:
        rules = rule_repository.list_rules("financial", status="active")
    except Exception:
        return []
    result: list[dict] = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.get("rule_type") not in {"entity_type", "relation_type"}:
            continue
        result.append(
            {
                "type_kind": rule.get("rule_type"),
                "type_name": rule.get("raw_value"),
                "definition": rule.get("canonical_value"),
                "payload": rule.get("payload") or {},
            }
        )
    return result
