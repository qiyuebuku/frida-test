from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.infrastructure.agent_runtime.run_authorization import (
    issue_run_authorization,
    verify_run_authorization,
)


NOW = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
SECRET = "test-secret"


def _token(**overrides) -> str:
    values = {
        "secret": SECRET,
        "run_id": "run-1",
        "role": "research",
        "task": "research_review",
        "cutoff_at": NOW - timedelta(minutes=1),
        "tools": ["market_frame_open"],
        "run_mode": "replay",
        "ttl_seconds": 300,
        "now": NOW,
    }
    values.update(overrides)
    return issue_run_authorization(**values)


def test_run_authorization_binds_role_tool_cutoff_and_expiry() -> None:
    claims = verify_run_authorization(
        _token(),
        secret=SECRET,
        tool_name="market_frame_open",
        expected_role="research",
        expected_task="research_review",
        now=NOW + timedelta(seconds=1),
    )

    assert claims.run_id == "run-1"
    assert claims.task == "research_review"
    assert claims.cutoff_at == NOW - timedelta(minutes=1)
    assert claims.tools == frozenset({"market_frame_open"})
    assert claims.run_mode == "replay"


def test_run_authorization_rejects_tampering_role_tool_and_expiry() -> None:
    token = _token()
    parts = token.split(".")
    tampered = f"{parts[0]}.{parts[1]}x.{parts[2]}"

    with pytest.raises(ValueError, match="signature"):
        verify_run_authorization(
            tampered,
            secret=SECRET,
            tool_name="market_frame_open",
            expected_role="research",
            now=NOW,
        )
    with pytest.raises(ValueError, match="role"):
        verify_run_authorization(
            token,
            secret=SECRET,
            tool_name="market_frame_open",
            expected_role="portfolio",
            now=NOW,
        )
    with pytest.raises(ValueError, match="not authorized"):
        verify_run_authorization(
            token,
            secret=SECRET,
            tool_name="market_domain_open",
            expected_role="research",
            now=NOW,
        )
    with pytest.raises(ValueError, match="expired"):
        verify_run_authorization(
            token,
            secret=SECRET,
            tool_name="market_frame_open",
            expected_role="research",
            now=NOW + timedelta(minutes=6),
        )


def test_run_authorization_rejects_future_cutoff() -> None:
    within_clock_skew = _token(cutoff_at=NOW + timedelta(seconds=1))
    claims = verify_run_authorization(
        within_clock_skew,
        secret=SECRET,
        tool_name="market_frame_open",
        expected_role="research",
        now=NOW,
    )
    assert claims.cutoff_at == NOW + timedelta(seconds=1)

    token = _token(cutoff_at=NOW + timedelta(seconds=6))

    with pytest.raises(ValueError, match="future"):
        verify_run_authorization(
            token,
            secret=SECRET,
            tool_name="market_frame_open",
            expected_role="research",
            now=NOW,
        )


def test_run_authorization_rejects_wrong_task_and_run_mode() -> None:
    with pytest.raises(ValueError, match="task"):
        verify_run_authorization(
            _token(task="portfolio_review"),
            secret=SECRET,
            tool_name="market_frame_open",
            expected_role="research",
            expected_task="research_review",
            now=NOW,
        )
    with pytest.raises(ValueError, match="run_mode"):
        verify_run_authorization(
            _token(run_mode="anything"),
            secret=SECRET,
            tool_name="market_frame_open",
            expected_role="research",
            expected_task="research_review",
            now=NOW,
        )
