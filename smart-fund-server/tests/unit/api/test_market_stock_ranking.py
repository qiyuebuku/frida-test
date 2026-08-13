from __future__ import annotations

from pathlib import Path

from src.application.services.market_observability_service import (
    MarketObservabilityService,
)
from src.interfaces.cli.main import COLLECTION_WORKER_TASKS


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_stock_ranking_collector_is_enabled_for_default_worker() -> None:
    assert "collect_stock_rankings" in COLLECTION_WORKER_TASKS
    assert "collect_stock_dynamic_groups" in COLLECTION_WORKER_TASKS


def test_stock_ranking_reads_and_slices_database_snapshot() -> None:
    snapshot = {
        "provider": "ths_native",
        "market": "cn",
        "bucket_at": "2026-08-05T08:00:00+00:00",
        "freshness_status": "fetch_time",
        "data": {
            "sort": "rise",
            "count": 50,
            "stocks": [
                {"code": f"{index:06d}", "name": f"stock-{index}"}
                for index in range(50)
            ],
        },
    }

    class Snapshots:
        def query_latest(self, *, subject_ids, data_types) -> list[dict]:
            assert subject_ids == ["rise"]
            assert data_types == ["stock_ranking"]
            return [snapshot]

    result = MarketObservabilityService(
        snapshot_repository=Snapshots(),
    ).stock_ranking(sort="rise", count=20)

    assert result["data"]["count"] == 20
    assert result["data"]["available_count"] == 50
    assert len(result["data"]["stocks"]) == 20
    assert len(snapshot["data"]["stocks"]) == 50
    assert result["provider_metadata"]["upstream_requested"] is False


def test_stock_ranking_returns_empty_when_snapshot_is_missing() -> None:
    class Snapshots:
        def query_latest(self, **_kwargs) -> list[dict]:
            return []

    result = MarketObservabilityService(
        snapshot_repository=Snapshots(),
    ).stock_ranking(sort="quick", count=20)

    assert result["status"] == "empty"
    assert result["data"]["stocks"] == []
    assert result["provider_metadata"]["upstream_requested"] is False


def test_stock_ranking_accepts_all_native_ranking_modes() -> None:
    class Snapshots:
        def query_latest(self, *, subject_ids, data_types) -> list[dict]:
            assert data_types == ["stock_ranking"]
            return [{
                "provider": "ths_native",
                "data": {
                    "sort": subject_ids[0],
                    "stocks": [{"code": "000001", "name": "测试股票"}],
                },
            }]

    service = MarketObservabilityService(snapshot_repository=Snapshots())
    modes = {
        "rise",
        "fall",
        "quick",
        "turnover",
        "large_order",
        "volume_ratio",
        "turnover_rate",
        "main_net_inflow",
        "amplitude",
    }

    assert {
        service.stock_ranking(sort=mode, count=20)["data"]["sort"]
        for mode in modes
    } == modes


def test_stock_dynamic_groups_read_latest_persisted_snapshots() -> None:
    rows = [
        {
            "subject_id": "second",
            "bucket_at": "2026-08-02T07:00:00+00:00",
            "fetched_at": "2026-08-02T07:00:01+00:00",
            "freshness_status": "fetch_time",
            "data": {
                "display_order": 1,
                "data_code": "second",
                "title": "第二组",
                "featured_stocks": [
                    {"code": "000004", "name": "精选股票四"},
                ],
                "candidate_stocks": [
                    {"code": "000002", "name": "股票二"},
                    {"code": "000003", "name": "股票三"},
                ],
                "stocks": [
                    {"code": "000002", "name": "股票二"},
                    {"code": "000003", "name": "股票三"},
                ],
            },
        },
        {
            "subject_id": "first",
            "bucket_at": "2026-08-02T07:00:00+00:00",
            "fetched_at": "2026-08-02T07:00:01+00:00",
            "freshness_status": "fetch_time",
            "data": {
                "display_order": 0,
                "data_code": "first",
                "title": "第一组",
                "stocks": [{"code": "000001", "name": "股票一"}],
            },
        },
    ]

    class Snapshots:
        def list_latest(self, **kwargs) -> list[dict]:
            assert kwargs == {
                "data_types": ["stock_dynamic_group"],
                "subject_type": "ranking",
                "limit": 100,
            }
            return rows

    result = MarketObservabilityService(
        snapshot_repository=Snapshots(),
    ).stock_dynamic_groups(count_per_group=1)

    assert result["status"] == "ok"
    assert [group["data_code"] for group in result["data"]["groups"]] == [
        "first",
        "second",
    ]
    assert result["data"]["groups"][1]["available_count"] == 1
    assert len(result["data"]["groups"][1]["stocks"]) == 1
    assert result["data"]["groups"][1]["stocks"][0]["code"] == "000004"
    candidate_result = MarketObservabilityService(
        snapshot_repository=Snapshots(),
    ).stock_dynamic_groups(count_per_group=10, scope="candidates")
    assert candidate_result["data"]["groups"][1]["available_count"] == 2
    assert candidate_result["data"]["groups"][1]["stocks"][0]["code"] == "000002"
    assert result["provider_metadata"]["upstream_requested"] is False


def test_stock_dynamic_groups_return_empty_without_snapshots() -> None:
    class Snapshots:
        def list_latest(self, **_kwargs) -> list[dict]:
            return []

    result = MarketObservabilityService(
        snapshot_repository=Snapshots(),
    ).stock_dynamic_groups()

    assert result["status"] == "empty"
    assert result["data"]["groups"] == []


def test_market_overview_frontend_only_calls_persisted_read_apis() -> None:
    source = (
        PROJECT_ROOT / "static/market_observability_dashboard.js"
    ).read_text(encoding="utf-8")

    assert "/api/market/" not in source
    assert "`/api/market/" not in source


def test_market_frontend_has_dedicated_stock_market_view() -> None:
    html = (
        PROJECT_ROOT / "static/market_observability_dashboard.html"
    ).read_text(encoding="utf-8")
    script = (
        PROJECT_ROOT / "static/market_observability_dashboard.js"
    ).read_text(encoding="utf-8")
    styles = (
        PROJECT_ROOT / "static/market_observability_dashboard.css"
    ).read_text(encoding="utf-8")

    assert 'data-view="stocks"' in html
    assert 'id="view-stocks"' in html
    assert "stock-dynamic-groups?count_per_group=100&scope=featured" in script
    assert ".view-panel.stock-market-view.active" in styles
    assert ".stock-market-view {\n  display: grid;" not in styles


def test_market_observability_read_path_has_no_upstream_clients() -> None:
    paths = [
        PROJECT_ROOT / "src/interfaces/api/routes/market_observability.py",
        PROJECT_ROOT
        / "src/application/services/market_observability_service.py",
    ]
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "src.infrastructure.clients" not in source
        assert "_utils." not in source
