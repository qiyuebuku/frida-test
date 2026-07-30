from datetime import datetime, timedelta, timezone

from src.domain.collection.services.base import _interval_is_due


def test_interval_due_absorbs_short_schedule_jitter() -> None:
    now = datetime.now(timezone.utc)

    assert _interval_is_due(now, now - timedelta(seconds=53), 60)
    assert not _interval_is_due(now, now - timedelta(seconds=40), 60)


def test_interval_due_absorbs_source_runtime_for_three_minute_source() -> None:
    now = datetime.now(timezone.utc)

    assert _interval_is_due(now, now - timedelta(seconds=170), 180)
    assert not _interval_is_due(now, now - timedelta(seconds=140), 180)


def test_backfill_minimum_interval_has_no_grace() -> None:
    now = datetime.now(timezone.utc)

    assert _interval_is_due(now, now - timedelta(seconds=5), 5)
    assert not _interval_is_due(now, now - timedelta(seconds=4), 5)
