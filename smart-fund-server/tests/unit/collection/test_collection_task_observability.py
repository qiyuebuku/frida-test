from datetime import datetime, timezone

from src.application.services import collection_task_observability_service as module
from src.application.services.collection_task_observability_service import (
    CollectionTaskObservabilityService,
    _push_data_is_delayed,
)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def list_all(self):
        return self.rows

    def list_latest(self, **kwargs):
        return self.rows


def test_catalogue_uses_scheduler_rows_and_metadata_as_primary_source(monkeypatch):
    now = datetime.now(timezone.utc)
    schedules = Rows([{
        "scheduler_id": "collect_news_cls_180s",
        "task_type": "interval", "queue_name": "collect_collection_source",
        "task_args": [], "task_kwargs": {"aggregator": "news", "source_name": "cls"},
        "cron_expression": None, "interval_seconds": 180.0,
        "next_run_time": now, "last_run_time": now, "enabled": True,
        "description": "ignored fallback",
        "metadata": {"observability": {
            "source": "结构化来源", "module": "新闻资讯", "category": "资讯与基础数据",
            "display_name": "结构化名称", "channel": "http", "channel_label": "HTTP 拉取",
        }},
        "tags": ["smart-fund"], "schedule_timezone": "Asia/Shanghai",
        "active_windows": [], "calendar_config": {},
    }])
    service = CollectionTaskObservabilityService(
        schedule_repository=schedules, state_repository=Rows([]),
    )
    service._runs = Rows([{
        "task_name": "collect_news_cls", "started_at": now,
        "finished_at": now, "status": "success", "saved_count": 2,
    }])
    service._snapshots = Rows([])
    monkeypatch.setattr(module, "_stream_connected", lambda: True)

    result = service.catalogue()
    item = next(row for row in result["items"] if row["id"] == "collect_news_cls_180s")
    assert item["source"] == "结构化来源"
    assert item["name"] == "结构化名称"
    assert item["next_run_at"] == now
    assert item["period_seconds"] == 180


def test_catalogue_adds_active_backfill_from_collection_state(monkeypatch):
    service = CollectionTaskObservabilityService(
        schedule_repository=Rows([]),
        state_repository=Rows([{
            "aggregator": "news", "source_name": "cls", "mode": "backfill",
            "target_time": "2026-07-01", "oldest_time": "2026-07-20",
            "cursor": 8, "backfill_status": None, "last_error": "",
            "enabled": True, "total_saved": 100,
        }]),
    )
    service._runs = Rows([])
    service._snapshots = Rows([])
    monkeypatch.setattr(module, "_stream_connected", lambda: True)
    item = next(row for row in service.catalogue()["items"] if row["id"] == "backfill:news:cls")
    assert item["category"] == "临时回填"
    assert item["backfill"]["cursor"] == 8


def test_a_share_push_is_not_delayed_during_weekend_market_hours():
    now = datetime(2026, 8, 8, 5, 20, tzinfo=timezone.utc)  # 周六 13:20
    last_data_at = datetime(2026, 8, 8, 5, 14, tzinfo=timezone.utc)

    for task_id in ("push_cn_indices", "push_market_context", "push_etf_quotes"):
        assert not _push_data_is_delayed(task_id, last_data_at, now)


def test_a_share_push_is_delayed_during_weekday_market_hours():
    now = datetime(2026, 8, 7, 5, 20, tzinfo=timezone.utc)  # 周五 13:20
    last_data_at = datetime(2026, 8, 7, 5, 14, tzinfo=timezone.utc)

    for task_id in ("push_cn_indices", "push_market_context", "push_etf_quotes"):
        assert _push_data_is_delayed(task_id, last_data_at, now)
