from src.application.agents.financial_research.replay_suite import (
    load_replay_suite,
    replay_case_input,
)


def test_fixed_replay_suite_has_30_labeled_future_safe_cases() -> None:
    suite = load_replay_suite()

    assert len(suite.cases) == 30
    assert len({case.case_id for case in suite.cases}) == 30
    assert all(case.forbidden_future_after == case.decision_at for case in suite.cases)
    assert len({case.category for case in suite.cases}) == 12


def test_model_replay_input_does_not_leak_human_labels() -> None:
    case = load_replay_suite().cases[0]
    payload = replay_case_input(case)

    assert set(payload) == {"case_id", "decision_at", "research_question"}
    assert "acceptable_conclusions" not in payload
    assert "required_counterevidence" not in payload
