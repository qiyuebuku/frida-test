"""Application-level registry for knowledge domain adapters."""

from __future__ import annotations

from src.domain.knowledge.adapter import DomainAdapter
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.baseline_rules import financial_baseline_normalization_rules
from src.domain.knowledge_adapters.financial.news_extraction import FinancialNewsExtractionStrategy
from src.application.services.financial_normalization_decision import FinancialNormalizationDecisionService
from src.application.services.knowledge_llm_extraction_service import KnowledgeLLMExtractionService
from src.application.services.knowledge_llm_config import resolve_kg_llm_model


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
    return FinancialKGAdapter(
        news_extraction_strategy=FinancialNewsExtractionStrategy(
            llm_port=llm_extraction_port,
            llm_model=resolve_kg_llm_model("financial_news_extraction"),
        ),
        normalization_rules=normalization_rules,
        normalization_decision_strategy=FinancialNormalizationDecisionService(
            llm_service=llm_service,
            llm_model=resolve_kg_llm_model("financial_entity_normalization"),
            rule_repository=rule_repository,
            rules=normalization_rules,
        ),
    )


def list_adapters() -> list[str]:
    return ["financial"]
