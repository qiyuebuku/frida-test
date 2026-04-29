"""Structured retrieval plan schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from src.domain.knowledge.retrieval_tools import RetrievalToolCall
from src.domain.knowledge.schemas import KnowledgeBaseModel

RetrievalIntent = Literal[
    "impact_events_for_entity",
    "affected_targets_for_topic",
    "asset_transmission",
    "macro_beneficiaries",
    "path_explanation",
    "research_summary",
    "general",
]

RelationDirection = Literal["incoming", "outgoing", "path", "undirected"]


class PlannedEntity(KnowledgeBaseModel):
    text: str
    type_hint: str | None = None


class PlannedTimeRange(KnowledgeBaseModel):
    preset: str | None = None
    days: int | None = None
    start: str | None = None
    end: str | None = None


class RetrievalPlan(KnowledgeBaseModel):
    mode: str = "deterministic_plan"
    intent: RetrievalIntent = "general"
    entities: list[PlannedEntity] = Field(default_factory=list)
    time_range: PlannedTimeRange = Field(default_factory=PlannedTimeRange)
    relation_filters: list[str] = Field(default_factory=list)
    direction: RelationDirection = "undirected"
    steps: list[RetrievalToolCall] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
