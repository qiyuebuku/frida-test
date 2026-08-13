"""Versioned fixed replay-suite contracts and loader."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from src.application.agents.financial_research.schemas import ResearchContract


DEFAULT_REPLAY_SUITE = (
    Path(__file__).resolve().parents[4]
    / "tests"
    / "fixtures"
    / "research_replay"
    / "research_quality_v1.json"
)


class ResearchReplayCase(ResearchContract):
    case_id: str = Field(pattern=r"^RQ-[0-9]{3}$")
    category: Literal[
        "premarket_global_mapping", "intraday_move", "postmarket_leadership",
        "trend_continuation", "pullback_rebound", "local_high",
        "priced_in_news", "strong_news_weak_price", "strong_price_weak_mechanism",
        "candidate_competition", "data_quality", "view_evolution",
    ]
    decision_at: datetime
    research_question: str = Field(min_length=10, max_length=1000)
    must_consider: list[str] = Field(min_length=1, max_length=12)
    required_counterevidence: list[str] = Field(min_length=1, max_length=12)
    acceptable_conclusions: list[str] = Field(min_length=1, max_length=8)
    common_errors: list[str] = Field(min_length=1, max_length=12)
    forbidden_future_after: datetime

    @model_validator(mode="after")
    def validate_future_boundary(self) -> "ResearchReplayCase":
        if self.forbidden_future_after != self.decision_at:
            raise ValueError("forbidden_future_after must equal decision_at")
        return self


class ResearchReplaySuite(ResearchContract):
    suite_id: Literal["research-quality-v1"]
    cases: list[ResearchReplayCase] = Field(min_length=30)

    @model_validator(mode="after")
    def validate_unique_cases(self) -> "ResearchReplaySuite":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("replay case_id must be unique")
        return self


def load_replay_suite(path: Path = DEFAULT_REPLAY_SUITE) -> ResearchReplaySuite:
    return ResearchReplaySuite.model_validate_json(path.read_text(encoding="utf-8"))


def replay_case_input(case: ResearchReplayCase) -> dict[str, object]:
    """Return labels separately from model input to prevent answer leakage."""

    return {
        "case_id": case.case_id,
        "decision_at": case.decision_at,
        "research_question": case.research_question,
    }


def replay_labels(case: ResearchReplayCase) -> dict[str, object]:
    return json.loads(case.model_dump_json())
