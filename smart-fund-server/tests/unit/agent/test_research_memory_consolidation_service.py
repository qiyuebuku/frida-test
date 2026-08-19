from datetime import UTC, datetime, timedelta

from src.application.services.research_memory_consolidation_service import (
    ResearchMemoryConsolidationService,
)
from src.interfaces.cli.main import COLLECTION_WORKER_GROUPS
from src.interfaces.cli.schedules import SCHEDULES
from src.interfaces.mcp.projection import project_tool_result
from src.infrastructure.persistence.repositories.agent_research_read_repository import (
    _text_terms,
)


NOW = datetime(2026, 8, 15, 8, 0, tzinfo=UTC)


class _Repository:
    def __init__(self, *, quality=None, outcomes=None) -> None:
        self.quality = quality or []
        self.outcomes = outcomes or []
        self.saved = []

    def list_quality_memory_evidence(self, **_):
        return self.quality

    def list_outcome_memory_evidence(self, **_):
        return self.outcomes

    def upsert_role_memory_with_cases(self, *, memory, cases) -> None:
        self.saved.append((memory, cases))


def _quality(index: int, *, hard: bool = False) -> dict:
    code = "market_citation_subject_mismatch"
    return {
        "evaluation_id": f"evaluation-{index}",
        "run_id": f"research-run-{index:032x}",
        "grade": "good",
        "overall_score": 82.0,
        "hard_failures": [code] if hard else [],
        "advisory_findings": [] if hard else [code],
        "improvement_actions": ["核对主张对象和证据对象"],
        "evaluated_at": NOW - timedelta(days=index),
    }


def test_repeated_quality_failure_is_promoted_with_auditable_cases() -> None:
    repository = _Repository(quality=[_quality(1, hard=True), _quality(2, hard=True), _quality(3)])

    result = ResearchMemoryConsolidationService(
        repository=repository  # type: ignore[arg-type]
    ).consolidate(now=NOW)

    assert result["quality_memories"] == {"promoted": 1, "candidates": 0}
    memory, cases = repository.saved[0]
    assert memory["status"] == "promoted"
    assert memory["scope"]["sample_count"] == 3
    assert memory["scope"]["hard_failure_count"] == 2
    assert len(cases) == 3
    assert all(item["decision_ref"].startswith("Q_") for item in cases)


def test_single_quality_failure_remains_invisible_candidate() -> None:
    repository = _Repository(quality=[_quality(1, hard=True)])

    result = ResearchMemoryConsolidationService(
        repository=repository  # type: ignore[arg-type]
    ).consolidate(now=NOW)

    assert result["quality_memories"] == {"promoted": 0, "candidates": 1}
    assert repository.saved[0][0]["status"] == "candidate"


def test_predictive_memory_requires_support_and_counterexample() -> None:
    statuses = ["confirmed", "partially_confirmed", "not_confirmed"]
    outcomes = [
        {
            "evaluation_id": f"outcome-{index}",
            "forecast_id": f"forecast-{index}",
            "run_id": f"research-run-{index:032x}",
            "subject_id": f"cn:concept:{886030 + index}",
            "metric": "相对上证指数累计超额收益",
            "expected_direction": "up",
            "status": status,
            "summary": "确定性结果评估",
            "evaluated_at": NOW - timedelta(days=index),
        }
        for index, status in enumerate(statuses, start=1)
    ]
    repository = _Repository(outcomes=outcomes)

    result = ResearchMemoryConsolidationService(
        repository=repository  # type: ignore[arg-type]
    ).consolidate(now=NOW)

    assert result["predictive_memories"] == {"promoted": 1, "candidates": 0}
    memory, cases = repository.saved[0]
    assert memory["status"] == "promoted"
    assert memory["scope"]["support_count"] == 2
    assert memory["scope"]["counterexample_count"] == 1
    assert len(cases) == 3


def test_memory_consolidation_has_an_independent_hourly_schedule() -> None:
    schedule = next(
        item for item in SCHEDULES
        if item.name == "research_memory_consolidation_hourly"
    )

    assert schedule.queue == "consolidate_research_memory"
    assert schedule.cron_expression == "25 * * * *"
    assert "consolidate_research_memory" in COLLECTION_WORKER_GROUPS["general"]


def test_memory_search_projection_hides_audit_references_until_open() -> None:
    result = project_tool_result(
        "role_memory_search",
        {
            "operation": "role_memory_search",
            "status": "available",
            "cutoff_at": NOW.isoformat(),
            "memories": [{
                "memory_id": "RM_1234",
                "summary": "对象证据必须对齐",
                "applicability": "行情主张",
                "counterexample": "显式跨对象比较",
                "evidence_references": ["Q_long-audit-list"],
                "confidence": "high",
            }],
        },
    )

    assert result == {
        "status": "available",
        "application_policy": (
            "这里只是导航摘要，不能据此声称已应用经验。最多选择两条最相关记忆，"
            "先用 role_memory_open 核对适用条件；需要判断是否适合当前情境时，"
            "再用 role_memory_case_open 查看原始案例和反例。"
        ),
        "memories": [{
            "memory_id": "RM_1234",
            "summary": "对象证据必须对齐",
            "applicability": "行情主张",
            "counterexample": "显式跨对象比较",
            "confidence": "high",
        }],
    }


def test_exact_market_evidence_failure_can_form_quality_memory() -> None:
    rows = [_quality(1, hard=True), _quality(2, hard=True), _quality(3)]
    for row in rows:
        row["hard_failures"] = (
            ["missing_exact_market_evidence"]
            if row["hard_failures"] else []
        )
        row["advisory_findings"] = (
            [] if row["hard_failures"] else ["missing_exact_market_evidence"]
        )
    repository = _Repository(quality=rows)

    result = ResearchMemoryConsolidationService(
        repository=repository  # type: ignore[arg-type]
    ).consolidate(now=NOW)

    assert result["quality_memories"] == {"promoted": 1, "candidates": 0}
    memory, _ = repository.saved[0]
    assert memory["scope"]["finding_code"] == "missing_exact_market_evidence"
    assert "记录级精确证据" in memory["summary"]


def test_chinese_memory_search_uses_partial_terms_not_whole_sentence() -> None:
    query_terms = _text_terms("研究市场主线时如何避免证据对象错配")
    memory_terms = _text_terms("市场主张必须引用同一对象的证据")

    assert query_terms & memory_terms
