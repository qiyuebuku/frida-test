from src.interfaces.cli.schedules import (
    FLAT_COLLECTION_SOURCE_CONFIGS,
    SCHEDULES,
)
from src.interfaces.cli.main import COLLECTION_WORKER_TASKS


def test_collection_schedules_have_unique_names_and_one_trigger() -> None:
    names = [schedule.name for schedule in SCHEDULES]

    assert len(names) == len(set(names))
    for schedule in SCHEDULES:
        configured = [
            schedule.cron_expression is not None,
            schedule.interval_seconds is not None,
            schedule.run_at is not None,
        ]
        assert sum(configured) == 1, schedule.name


def test_generic_aggregator_sources_are_flat_jettask_schedules() -> None:
    from src.domain.collection.services.fund_flow import FundFlowAggregator
    from src.domain.collection.services.macro import MacroAggregator
    from src.domain.collection.services.news import NewsAggregator
    from src.domain.collection.services.sentiment import SentimentAggregator

    flat = [
        schedule
        for schedule in SCHEDULES
        if schedule.queue == "collect_collection_source"
    ]
    expected = {
        (aggregator, source_name): interval
        for aggregator, source_name, interval, _ in FLAT_COLLECTION_SOURCE_CONFIGS
    }
    actual = {
        (schedule.kwargs["aggregator"], schedule.kwargs["source_name"]):
        schedule.interval_seconds
        for schedule in flat
    }

    assert len(flat) == 40
    assert actual == expected
    configured_sources = {
        **{("news", name): cfg["interval"] for name, cfg in NewsAggregator.SOURCE_CONFIGS.items()},
        **{("fund_flow", name): cfg["interval"] for name, cfg in FundFlowAggregator.SOURCE_CONFIGS.items()},
        **{("sentiment", name): cfg["interval"] for name, cfg in SentimentAggregator.SOURCE_CONFIGS.items()},
        **{("macro", name): cfg["interval"] for name, cfg in MacroAggregator.SOURCE_CONFIGS.items()},
    }
    assert actual == configured_sources
    assert {
        "collect_news",
        "collect_fund_flow",
        "collect_sentiment",
        "collect_macro",
    }.isdisjoint({schedule.queue for schedule in SCHEDULES})
    assert "collect_collection_source" in COLLECTION_WORKER_TASKS


def test_daily_and_boundary_schedules_remain_cron_based() -> None:
    cron_names = {
        "collect_market_daily_bars_after_close",
        "collect_market_reference_after_close",
        "scan_watchlist_daily_after_close",
        "scan_watchlist_reference_weekly",
        "collect_etf_daily_shares_night",
        "collect_etf_daily_shares_catchup",
        "collect_market_daily_catchup_morning",
    }
    by_name = {schedule.name: schedule for schedule in SCHEDULES}

    for name in cron_names:
        assert by_name[name].cron_expression
        assert by_name[name].interval_seconds is None
        serialized = by_name[name]._to_dict()
        assert serialized["trigger_type"] == "cron"
        assert serialized["trigger_config"]["minute"]
        assert "interval_seconds" not in serialized


def test_etf_estimated_flow_runs_every_minute() -> None:
    schedule = next(
        item
        for item in SCHEDULES
        if item.name == "collect_etf_estimated_net_inflow_60s"
    )

    assert schedule.queue == "collect_etf_estimated_net_inflow"
    assert schedule.interval_seconds == 60
    assert schedule.cron_expression is None


def test_cn_intraday_schedules_delegate_sessions_to_jettask() -> None:
    by_name = {schedule.name: schedule for schedule in SCHEDULES}
    constrained = {
        "collect_stock_rankings_120s",
        "collect_ths_market_events_30s",
        "collect_ths_market_context_60s",
        "collect_etf_estimated_net_inflow_60s",
    }

    for name in constrained:
        schedule = by_name[name]
        serialized = schedule._to_dict()
        assert serialized["timezone"] == "Asia/Shanghai"
        assert len(serialized["active_windows"]) == 2
        assert serialized["calendar"]["weekdays"] == [1, 2, 3, 4, 5]
        assert "2026-10-01" in serialized["calendar"]["excluded_dates"]

    assert by_name["collect_ths_market_events_30s"].active_windows[0]["start"] == "09:15"
    assert by_name["collect_stock_rankings_120s"].active_windows[0]["start"] == "09:25"


def test_all_day_ths_schedules_do_not_have_cn_trading_windows() -> None:
    by_name = {schedule.name: schedule for schedule in SCHEDULES}
    for name in (
        "collect_ths_etf_zone_15s",
        "collect_ths_futures_cycle_60s",
        "collect_ths_gold_zone_120s",
        "collect_ths_us_overview_60s",
        "collect_stock_dynamic_groups_60s",
    ):
        assert by_name[name].active_windows == []


def test_ths_etf_zone_runs_all_day_every_fifteen_seconds() -> None:
    schedule = next(
        item for item in SCHEDULES if item.name == "collect_ths_etf_zone_15s"
    )

    assert schedule.queue == "collect_ths_etf_zone"
    assert schedule.interval_seconds == 15
    assert schedule.cron_expression is None
    assert "collect_ths_etf_zone" in COLLECTION_WORKER_TASKS


def test_ths_futures_fragments_run_as_one_serial_cycle_every_minute() -> None:
    schedules = [
        item for item in SCHEDULES
        if item.name.startswith("collect_ths_futures_")
    ]
    assert len(schedules) == 1
    assert schedules[0].queue == "collect_ths_futures_cycle"
    assert schedules[0].interval_seconds == 60
    assert "collect_ths_futures_cycle" in COLLECTION_WORKER_TASKS


def test_ths_gold_zone_runs_all_day_every_two_minutes() -> None:
    schedule = next(
        item for item in SCHEDULES if item.name == "collect_ths_gold_zone_120s"
    )
    assert schedule.queue == "collect_ths_gold_zone"
    assert schedule.interval_seconds == 120
    assert "collect_ths_gold_zone" in COLLECTION_WORKER_TASKS


def test_ths_us_market_modules_are_independently_scheduled() -> None:
    expected = {
        "collect_ths_us_overview": 60,
        "collect_ths_us_sectors": 120,
        "collect_ths_us_stock_rankings": 120,
        "collect_ths_us_etf_sectors": 600,
    }
    schedules = {
        item.queue: item.interval_seconds
        for item in SCHEDULES if item.queue in expected
    }
    assert schedules == expected
    assert expected.keys() <= set(COLLECTION_WORKER_TASKS)


def test_ths_sector_core_is_split_into_independent_schedules() -> None:
    fragments = [
        item for item in SCHEDULES if item.queue == "collect_ths_sector_fragment_v2"
    ]

    assert len(fragments) == 16
    assert sum(item.kwargs["kind"] == "hot" for item in fragments) == 3
    assert sum(item.kwargs["kind"] == "table" for item in fragments) == 5
    assert sum(item.kwargs["kind"] == "ranking" for item in fragments) == 5
    assert sum(item.kwargs["kind"] == "flow" for item in fragments) == 3
    assert all(item.interval_seconds == 60 for item in fragments)
    assert "collect_ths_sector_core_snapshot" not in {
        item.queue for item in SCHEDULES
    }

    reference = next(
        item
        for item in SCHEDULES
        if item.name == "collect_ths_sector_references_5min"
    )
    assert reference.queue == "collect_ths_sector_reference_snapshot_v2"
    assert reference.interval_seconds == 300
    assert "collect_ths_sector_fragment_v2" in set(COLLECTION_WORKER_TASKS)
    assert "collect_ths_sector_reference_snapshot_v2" in set(COLLECTION_WORKER_TASKS)


def test_stock_protocol_cadence_exceeds_serial_bridge_runtime() -> None:
    by_name = {schedule.name: schedule for schedule in SCHEDULES}

    rankings = by_name["collect_stock_rankings_120s"]
    groups = by_name["collect_stock_dynamic_groups_60s"]
    assert rankings.queue == "collect_stock_rankings"
    assert rankings.interval_seconds == 120
    assert groups.queue == "collect_stock_dynamic_groups"
    assert groups.interval_seconds == 60
    assert groups.active_windows == []
    assert groups.calendar == {}


def test_ths_sector_signals_are_independently_scheduled() -> None:
    fragments = [
        item
        for item in SCHEDULES
        if item.queue == "collect_ths_sector_signal_fragment_v2"
    ]

    assert len(fragments) == 13
    assert sum(item.kwargs["kind"] == "rotation" for item in fragments) == 10
    assert {
        item.kwargs["kind"]
        for item in fragments
        if item.kwargs["kind"] != "rotation"
    } == {"industry_opportunity", "prosperity", "commodity_linkage"}
    assert all(item.interval_seconds == 300 for item in fragments)
    assert "collect_ths_sector_signal_snapshot" not in {
        item.queue for item in SCHEDULES
    }
    assert "collect_ths_sector_signal_fragment_v2" in set(COLLECTION_WORKER_TASKS)


def test_all_scheduled_collection_queues_have_worker_consumers() -> None:
    collection_queues = {
        schedule.queue
        for schedule in SCHEDULES
        if schedule.queue.startswith(("collect_", "scan_"))
    }

    assert collection_queues <= set(COLLECTION_WORKER_TASKS)
