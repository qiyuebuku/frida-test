"""Write-time normalization decision protocol for financial KG payloads."""

from __future__ import annotations

from typing import Any, Protocol


class FinancialPayloadNormalizationStrategy(Protocol):
    """Normalizes weak-ID entities before NodeDraft/EdgeDraft creation."""

    async def normalize_payload(
        self,
        payload: dict[str, Any],
        *,
        source_id: str,
        source_type: str,
    ) -> dict[str, Any]:
        """Return a payload whose entities are safe to enter the main graph."""
