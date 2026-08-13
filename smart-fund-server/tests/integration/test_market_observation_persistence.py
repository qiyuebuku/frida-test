from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from src.infrastructure.connections import get_engine, get_session, set_target
from src.infrastructure.persistence.models.base import Base
from src.infrastructure.persistence.models.collection import (
    CollectionRun,
    EtfDailyShare,
    MarketSnapshot,
)
from src.infrastructure.persistence.repositories import (
    CollectionRunRepository,
    EtfDailyShareRepository,
    MarketSnapshotRepository,
)


SUBJECT_ID = "test:market_observation"


@pytest.fixture(autouse=True)
def prepare_test_tables():
    set_target("test")
    Base.metadata.create_all(
        get_engine("test"),
        tables=[
            MarketSnapshot.__table__,
            EtfDailyShare.__table__,
            CollectionRun.__table__,
        ],
    )
    _cleanup()
    yield
    _cleanup()
    set_target("prod")


def _cleanup() -> None:
    with get_session() as session:
        session.execute(
            delete(MarketSnapshot).where(
                MarketSnapshot.subject_id == SUBJECT_ID
            )
        )
        session.execute(
            delete(EtfDailyShare).where(EtfDailyShare.code == "999999")
        )
        session.execute(
            delete(CollectionRun).where(
                CollectionRun.source_name == SUBJECT_ID
            )
        )


@pytest.mark.integration
def test_market_snapshot_bucket_is_idempotent_and_keeps_history() -> None:
    repository = MarketSnapshotRepository()
    bucket = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
    base = {
        "data_type": "market_breadth",
        "subject_type": "market",
        "subject_id": SUBJECT_ID,
        "market": "cn",
        "provider": "test",
        "trade_date": date(2026, 7, 31),
        "observed_at": bucket,
        "fetched_at": bucket,
        "freshness_status": "realtime",
        "source_latency_seconds": 0,
    }

    repository.upsert_batch(
        [
            {
                **base,
                "bucket_at": bucket,
                "payload_hash": "first",
                "data": {"up_count": 1},
            }
        ]
    )
    repository.upsert_batch(
        [
            {
                **base,
                "bucket_at": bucket,
                "payload_hash": "retry",
                "data": {"up_count": 2},
            },
            {
                **base,
                "bucket_at": bucket + timedelta(seconds=30),
                "payload_hash": "next",
                "data": {"up_count": 3},
            },
        ]
    )

    rows = repository.query_history(
        subject_id=SUBJECT_ID,
        data_type="market_breadth",
    )
    assert len(rows) == 2
    assert rows[-1]["data"]["up_count"] == 2
    latest = repository.list_latest(
        data_types=["market_breadth"],
        limit=5000,
    )
    test_latest = [
        row for row in latest if row["subject_id"] == SUBJECT_ID
    ]
    assert len(test_latest) == 1
    assert test_latest[0]["data"]["up_count"] == 3


@pytest.mark.integration
def test_market_snapshot_rejects_older_frame_in_same_bucket() -> None:
    repository = MarketSnapshotRepository()
    bucket = datetime(2026, 7, 31, 1, 0, tzinfo=timezone.utc)
    base = {
        "data_type": "ths_cn_index_quote",
        "subject_type": "index",
        "subject_id": SUBJECT_ID,
        "market": "cn",
        "provider": "ths_native_stream",
        "trade_date": date(2026, 7, 31),
        "bucket_at": bucket,
        "freshness_status": "realtime",
        "source_latency_seconds": 0,
    }
    repository.upsert_batch([{
        **base,
        "observed_at": bucket + timedelta(milliseconds=900),
        "fetched_at": bucket + timedelta(seconds=1),
        "payload_hash": "new",
        "data": {"close": 2},
    }])
    saved = repository.upsert_batch([{
        **base,
        "observed_at": bucket + timedelta(milliseconds=100),
        "fetched_at": bucket + timedelta(seconds=2),
        "payload_hash": "old-late",
        "data": {"close": 1},
    }])

    assert saved == 0
    rows = repository.query_history(
        subject_id=SUBJECT_ID,
        data_type="ths_cn_index_quote",
    )
    assert rows[0]["data"]["close"] == 2
    summary = repository.summarize_since(bucket - timedelta(seconds=1))
    assert summary["total"] >= 2
    assert summary["latest_bucket_at"] >= bucket + timedelta(seconds=30)


@pytest.mark.integration
def test_market_snapshot_deduplicates_conflicting_rows_in_one_batch() -> None:
    repository = MarketSnapshotRepository()
    bucket = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    base = {
        "data_type": "sector_flow",
        "subject_type": "sector",
        "subject_id": SUBJECT_ID,
        "market": "cn",
        "provider": "test",
        "trade_date": date(2026, 7, 31),
        "observed_at": bucket,
        "fetched_at": bucket,
        "bucket_at": bucket,
        "freshness_status": "realtime",
        "source_latency_seconds": 0,
    }

    saved = repository.upsert_batch(
        [
            {**base, "payload_hash": "first", "data": {"rank": 1}},
            {**base, "payload_hash": "last", "data": {"rank": 2}},
        ]
    )

    assert saved == 1
    rows = repository.query_history(
        subject_id=SUBJECT_ID,
        data_type="sector_flow",
    )
    assert len(rows) == 1
    assert rows[0]["payload_hash"] == "last"
    assert rows[0]["data"]["rank"] == 2


@pytest.mark.integration
def test_market_snapshot_reads_only_previous_trade_date_history() -> None:
    repository = MarketSnapshotRepository()
    current_bucket = datetime(2026, 7, 31, 2, 0, tzinfo=timezone.utc)
    rows = []
    for days_ago, value in ((2, 100), (1, 200), (0, 300)):
        bucket = current_bucket - timedelta(days=days_ago)
        rows.append(
            {
                "data_type": "market_breadth",
                "subject_type": "market",
                "subject_id": SUBJECT_ID,
                "market": "cn",
                "provider": "test",
                "trade_date": bucket.date(),
                "observed_at": bucket,
                "fetched_at": bucket,
                "bucket_at": bucket,
                "freshness_status": "realtime",
                "source_latency_seconds": 0,
                "payload_hash": str(value),
                "data": {"turnover": value},
            }
        )
    repository.upsert_batch(rows)

    previous = repository.query_previous_trade_date_history(
        subject_id=SUBJECT_ID,
        data_type="market_breadth",
        before_date=date(2026, 7, 31),
    )

    assert len(previous) == 1
    assert previous[0]["trade_date"] == date(2026, 7, 30)
    assert previous[0]["data"]["turnover"] == 200


@pytest.mark.integration
def test_etf_completeness_and_collection_run_are_persisted() -> None:
    trade_date = date(2026, 7, 31)
    etf_repository = EtfDailyShareRepository()
    etf_repository.upsert_batch(
        [
            {
                "exchange": exchange,
                "code": "999999",
                "name": "测试ETF",
                "trade_date": trade_date,
                "shares": 100,
                "share_unit": "share",
                "provider": "test",
                "observed_at": None,
                "fetched_at": datetime.now(timezone.utc),
                "data": {},
            }
            for exchange in ("sse", "szse")
        ]
    )
    run_repository = CollectionRunRepository()
    run_id = run_repository.start(
        task_name="test_market_observation",
        source_name=SUBJECT_ID,
    )
    run_repository.finish(run_id, status="success", saved_count=2)

    assert etf_repository.list_complete_dates([trade_date]) == {trade_date}
    etf_summary = etf_repository.latest_summary()
    assert etf_summary["trade_date"] >= trade_date
    assert etf_summary["exchange_count"] >= 2
    run_summary = run_repository.summarize_since(
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    assert run_summary["by_status"]["success"] >= 1
    latest_runs = run_repository.list_latest(limit=1000)
    assert any(row["id"] == run_id for row in latest_runs)
    with get_session() as session:
        run = session.get(CollectionRun, run_id)
        assert run is not None
        assert run.status == "success"
        assert run.saved_count == 2
