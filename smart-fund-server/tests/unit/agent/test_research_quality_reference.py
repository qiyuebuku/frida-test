from datetime import UTC, datetime

from src.application.services.agent_research_state_query_service import (
    AgentResearchStateQueryService,
)
from src.domain.trading.research_quality_reference import (
    quality_ref_from_run_id,
    run_id_from_quality_ref,
)
from src.interfaces.mcp.projection import project_tool_result


RUN_ID = "research-run-68d231b54bbd478aaab09413024cf4a2"


class _QualityRepository:
    def __init__(self) -> None:
        self.opened_run_id = ""

    def open_latest_quality_evaluation_for_run_at(
        self, *, run_id: str, cutoff_at: datetime
    ) -> dict[str, object]:
        self.opened_run_id = run_id
        return {"run_id": run_id, "overall_score": 86.88}


def test_quality_reference_is_short_and_reversible() -> None:
    quality_ref = quality_ref_from_run_id(RUN_ID)

    assert quality_ref == "Q_aNIxtUu9R4qqsJQTAkz0og"
    assert len(quality_ref) == 24
    assert run_id_from_quality_ref(quality_ref) == RUN_ID


def test_quality_list_hides_storage_identifiers_at_mcp_projection() -> None:
    result = project_tool_result(
        "research_quality_list",
        {
            "evaluations": [
                {
                    "evaluation_id": f"quality:{RUN_ID}:research-quality-v3",
                    "run_id": RUN_ID,
                    "overall_score": 86.88,
                    "grade": "good",
                    "passed": True,
                    "evaluated_at": datetime(2026, 8, 15, tzinfo=UTC),
                }
            ]
        },
    )

    assert result == {
        "evaluations": [
            {
                "quality_ref": "Q_aNIxtUu9R4qqsJQTAkz0og",
                "overall_score": 86.88,
                "grade": "good",
                "passed": True,
            }
        ]
    }
    assert "evaluation_id" not in str(result)
    assert "research-run-" not in str(result)


def test_quality_open_keeps_only_the_short_reference() -> None:
    result = project_tool_result(
        "research_quality_open",
        {
            "evaluation": {
                "evaluation_id": f"quality:{RUN_ID}:research-quality-v3",
                "run_id": RUN_ID,
                "overall_score": 86.88,
                "grade": "good",
                "passed": True,
            }
        },
    )

    assert result["evaluation"]["quality_ref"] == (
        "Q_aNIxtUu9R4qqsJQTAkz0og"
    )
    assert "evaluation_id" not in result["evaluation"]
    assert "run_id" not in result["evaluation"]


def test_quality_open_resolves_reference_inside_the_service() -> None:
    repository = _QualityRepository()
    service = AgentResearchStateQueryService(repository=repository)  # type: ignore[arg-type]

    result = service.open_quality(
        cutoff_at=datetime(2026, 8, 15, tzinfo=UTC),
        quality_ref="Q_aNIxtUu9R4qqsJQTAkz0og",
    )

    assert repository.opened_run_id == RUN_ID
    assert result["status"] == "available"
