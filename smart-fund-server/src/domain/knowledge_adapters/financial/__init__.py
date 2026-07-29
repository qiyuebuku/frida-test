"""Financial domain adapter."""

from src.domain.knowledge_adapters.financial.adapter import FinancialKGAdapter
from src.domain.knowledge_adapters.financial.ontology import FINANCIAL_ADAPTER_SPEC

__all__ = ["FinancialKGAdapter", "FINANCIAL_ADAPTER_SPEC"]
