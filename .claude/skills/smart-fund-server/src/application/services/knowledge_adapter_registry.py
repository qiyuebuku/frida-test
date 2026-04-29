"""Application-level registry for knowledge domain adapters."""

from __future__ import annotations

from src.domain.knowledge.adapter import DomainAdapter
from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.news_extraction import FinancialNewsExtractionStrategy


class AdapterNotFoundError(ValueError):
    """Raised when a requested knowledge adapter is not registered."""


def get_adapter(adapter_name: str) -> DomainAdapter:
    name = (adapter_name or "").strip()
    if name == "financial":
        return _build_financial_adapter()
    raise AdapterNotFoundError(f"adapter_name 不支持: {adapter_name}")


def _build_financial_adapter() -> FinancialKGAdapter:
    try:
        from src.infrastructure.llm_proxy.service import get_claude_proxy_service
        llm_service = get_claude_proxy_service()
    except Exception:
        llm_service = None
    return FinancialKGAdapter(
        news_extraction_strategy=FinancialNewsExtractionStrategy(llm_service=llm_service),
    )


def list_adapters() -> list[str]:
    return ["financial"]
