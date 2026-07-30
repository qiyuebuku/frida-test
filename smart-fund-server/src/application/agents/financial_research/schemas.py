"""Typed inputs and outputs for financial Agent runs."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ResearchTaskMode(StrEnum):
    RESEARCH = "research"
    INVESTMENT_VIEW = "investment_view"
    MARKET_BRIEFING = "market_briefing"
    HOLDING_REVIEW = "holding_review"
    OPPORTUNITY_WATCHLIST = "opportunity_watchlist"
    REPLAY = "replay"


class ConfidenceLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    MEDIUM_HIGH = "medium_high"
    HIGH = "high"


class EvidenceCitation(BaseModel):
    kind: Literal["card", "edge", "community", "external", "market"]
    reference: str = Field(
        min_length=1,
        description=(
            "Complete Card, Edge, Community ID; opened external URL; or market "
            "instrument/dataset identifier."
        ),
    )
    claim: str = Field(
        min_length=1,
        description="The exact conclusion supported by this evidence.",
    )
    support: Literal["observed", "inferred", "source", "market"]


class FinancialResearchResult(BaseModel):
    task_mode: ResearchTaskMode
    conclusion: str = Field(
        min_length=1,
        description="Chinese Markdown containing the direct synthesized answer.",
    )
    confidence: ConfidenceLevel
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
