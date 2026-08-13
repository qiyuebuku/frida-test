from datetime import datetime, timezone

from src.application.services.ths_realtime_stream_service import (
    _us_ranking_session,
    _us_ranking_sort_id,
)


def _utc(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 5, hour, minute, tzinfo=timezone.utc)


def test_us_ranking_session_switches_ths_sort_field() -> None:
    assert _us_ranking_session(_utc(8, 28)) == "pre_market"
    assert _us_ranking_sort_id(_utc(8, 28)) == 36065
    assert _us_ranking_session(_utc(14, 0)) == "regular"
    assert _us_ranking_sort_id(_utc(14, 0)) == 34818
    assert _us_ranking_session(_utc(21, 0)) == "after_hours"
    assert _us_ranking_sort_id(_utc(21, 0)) == 34868
