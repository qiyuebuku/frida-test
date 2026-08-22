from src.interfaces.cli.main import COLLECTION_WORKER_GROUPS, COLLECTION_WORKER_TASKS


def test_collection_worker_groups_are_reduced_and_non_overlapping() -> None:
    assert set(COLLECTION_WORKER_GROUPS) == {
        "ths",
        "ths-sector",
        "ths-sector-fragment",
        "http",
        "general",
    }

    owners: dict[str, str] = {}
    for group, queues in COLLECTION_WORKER_GROUPS.items():
        for queue in queues:
            assert queue not in owners, (
                f"queue {queue} belongs to both {owners.get(queue)} and {group}"
            )
            owners[queue] = group


def test_general_worker_owns_internal_work() -> None:
    queues = set(COLLECTION_WORKER_GROUPS["general"])

    assert "collect_market_daily_bars" in queues
    assert "scan_watchlist_instruments" in queues
    assert "run_research_agent" in queues
    assert "consolidate_research_memory" in queues


def test_every_default_collection_queue_has_exactly_one_group_owner() -> None:
    owned = {
        queue
        for queues in COLLECTION_WORKER_GROUPS.values()
        for queue in queues
    }

    assert set(COLLECTION_WORKER_TASKS) <= owned


def test_latency_sensitive_ths_sector_remains_isolated() -> None:
    assert set(COLLECTION_WORKER_GROUPS["ths-sector"]) == {
        "collect_ths_sector_reference_snapshot_v2",
        "collect_ths_sector_signal_fragment_v2",
    }
    assert set(COLLECTION_WORKER_GROUPS["ths-sector-fragment"]) == {
        "collect_ths_sector_fragment_v2",
    }


def test_http_collection_isolated_from_general_backlogs() -> None:
    assert COLLECTION_WORKER_GROUPS["http"] == ("collect_collection_source",)
    assert "collect_collection_source" not in COLLECTION_WORKER_GROUPS["general"]
