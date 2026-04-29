"""Tests for knowledge application DTOs."""

from datetime import datetime, timezone

from src.application.dto.knowledge_dto import (
    KnowledgeCompileCommand,
    KnowledgeResearchContextCommand,
    dto_to_dict,
)
from src.domain.knowledge.enums import NodeStatus


def test_compile_command_defaults_to_financial_prod_target() -> None:
    command = KnowledgeCompileCommand(records=[{"source_type": "demo"}])

    assert command.adapter_name == "financial"
    assert command.target == "prod"
    assert command.dry_run is False
    assert command.concurrency is None


def test_research_context_command_defaults_are_api_safe() -> None:
    command = KnowledgeResearchContextCommand(query="固态电池")

    assert command.adapter_name == "financial"
    assert command.graph_depth == 3
    assert command.max_chars == 5000


def test_dto_to_dict_serializes_enums_and_datetimes() -> None:
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)

    assert dto_to_dict({"status": NodeStatus.ACTIVE, "time": now}) == {
        "status": "active",
        "time": "2026-04-25T00:00:00+00:00",
    }
