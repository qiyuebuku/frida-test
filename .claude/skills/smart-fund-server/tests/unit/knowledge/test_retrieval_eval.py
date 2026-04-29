"""Unit tests for replay evaluation."""

from src.domain.knowledge.quality import replay_bad_case


def test_bad_case_replay_reports_missing_refs() -> None:
    result = replay_bad_case(
        case_id="case-1",
        query="Alpha",
        expected_refs=["a", "b"],
        actual_refs=["a"],
    )

    assert not result.passed
    assert result.details["missing"] == ["b"]


def test_bad_case_replay_passes_when_expected_refs_are_present() -> None:
    result = replay_bad_case(
        case_id="case-2",
        expected_refs=["a"],
        actual_refs=["a", "b"],
    )

    assert result.passed
