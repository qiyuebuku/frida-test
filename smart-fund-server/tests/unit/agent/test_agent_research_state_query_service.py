from datetime import UTC, datetime

from src.application.services.agent_research_state_query_service import (
    AgentResearchStateQueryService,
)


class _MemoryRepository:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def search_memories(self, **_kwargs) -> list[dict]:
        return self.items


def test_empty_memory_search_does_not_offer_impossible_open_operations() -> None:
    service = AgentResearchStateQueryService(
        repository=_MemoryRepository([]),
    )

    result = service.search_memories(
        cutoff_at=datetime(2026, 8, 15, tzinfo=UTC),
        query="历史类比方向不一致",
    )

    assert result["status"] == "empty"
    assert result["memories"] == []
    assert result["next_operations"] == []


def test_available_memory_search_offers_open_operations() -> None:
    service = AgentResearchStateQueryService(
        repository=_MemoryRepository([{"memory_id": "memory-1"}]),
    )

    result = service.search_memories(
        cutoff_at=datetime(2026, 8, 15, tzinfo=UTC),
    )

    assert result["status"] == "available"
    assert result["next_operations"] == [
        "role_memory_open",
        "role_memory_case_open",
    ]
