from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from src.application.services.agent_market_query_service import (
    AgentMarketQueryService,
    _display_number,
    _native_table_quote_preview,
    _us_breadth_preview,
    _us_sector_preview,
)
from src.application.services.market_evidence_locator import (
    decode_market_evidence_locator,
)
from src.application.services.market_tracking_service import (
    _history_data_type,
    _history_series_semantics,
    _history_subject_id,
    _history_window_evidence,
)


NOW = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
CUTOFF = datetime(2026, 8, 8, 6, 0, tzinfo=UTC)


def test_history_native_codes_resolve_to_snapshot_subject_ids() -> None:
    assert _history_subject_id("886033", "ths_sector_daily") == "ths:concept:886033"
    assert _history_subject_id("881121", "ths_sector_daily") == "ths:industry:881121"
    assert _history_subject_id("000001", "ths_index_daily") == "cn:index:000001"


def test_history_data_type_corrects_live_quote_type_for_canonical_index() -> None:
    assert (
        _history_data_type("cn:index:000001", "ths_cn_index_quote")
        == "ths_index_daily"
    )
    assert (
        _history_data_type("ths:concept:886033", "ths_sector_quote")
        == "ths_sector_daily"
    )


def test_ths_kline_semantics_do_not_invent_volume_unit() -> None:
    semantics = _history_series_semantics("ths_sector_daily")
    assert semantics is not None
    assert semantics["volume_field"] == "provider-native raw volume; unit is not established"


def test_history_window_evidence_is_bound_to_series_subject() -> None:
    evidence = _history_window_evidence([
        {
            "id": index,
            "trade_date": date(2026, 8, 12) - timedelta(days=index - 1),
            "observed_at": NOW,
            "data": {"close": 101 - index},
        }
        for index in range(1, 6)
    ], subject_id="cn:index:000001", data_type="ths_index_daily")
    identity = decode_market_evidence_locator(
        evidence["5_bars"]["baseline"]["evidence_locator"]
    )
    assert identity.subject_id == "cn:index:000001"
    assert identity.data_type == "ths_index_daily"


def test_model_facing_numbers_remove_binary_float_noise() -> None:
    assert _display_number(-25.11999999999989, unit="index_points") == -25.12
    assert _display_number(60.0, unit="percent_change") == 60
    assert _display_number(4068.0, unit="stocks") == 4068


def test_native_table_quote_preview_exposes_gold_prices_without_raw_dump() -> None:
    preview = _native_table_quote_preview(
        {
            "native_table": {
                "dataDict": {
                    "4": ["au9999"],
                    "55": ["沪金主连"],
                    "10": [952.74],
                    "34818": ["0.07%"],
                    "other": ["ignored"],
                }
            }
        }
    )

    assert preview == {
        "quotes": [
            {
                "code": "au9999",
                "name": "沪金主连",
                "latest": 952.74,
                "change_pct": "0.07%",
            }
        ],
        "quote_count": 1,
        "truncated": False,
        "available_field_codes": ["4", "55", "10", "34818", "other"],
    }


def test_us_market_previews_expose_semantic_breadth_and_sector_rows() -> None:
    assert _us_breadth_preview(
        {
            "breadth_today": {
                "increase_ranges": [{"count": 120}, {"count": 30}],
                "decline_ranges": [{"count": 50}],
                "zero_range": 20,
            }
        }
    ) == {
        "advancing": 150,
        "declining": 50,
        "unchanged": 20,
        "advance_decline_ratio": 3.0,
    }
    assert _us_sector_preview(
        {
            "native_table": {
                "dataDict": {
                    "55": ["半导体"],
                    "34313": ["1.25%"],
                    "35284": ["示例公司"],
                    "35286": ["3.5%"],
                }
            }
        }
    ) == [
        {
            "name": "半导体",
            "change_pct": "1.25%",
            "leader": "示例公司",
            "leader_change_pct": "3.5%",
        }
    ]


class _SnapshotRepository:
    def __init__(self) -> None:
        self.cutoffs = []
        self.rows = [
            {
                "id": 1,
                "data_type": "ths_cn_market_breadth",
                "subject_type": "market",
                "subject_id": "cn:a_share:ths_breadth",
                "market": "cn",
                "provider": "ths",
                "trade_date": date(2026, 8, 8),
                "observed_at": CUTOFF - timedelta(minutes=1),
                "fetched_at": CUTOFF,
                "bucket_at": CUTOFF - timedelta(minutes=1),
                "freshness_status": "realtime",
                "data": {
                    "up_count": 2200,
                    "down_count": 1900,
                    "large_rows": list(range(30)),
                },
            },
            {
                "id": 2,
                "data_type": "futures_intraday",
                "subject_type": "instrument",
                "subject_id": "global:futures:ftse_a50",
                "market": "global",
                "provider": "ths",
                "trade_date": date(2026, 8, 7),
                "observed_at": CUTOFF - timedelta(hours=2),
                "fetched_at": CUTOFF - timedelta(hours=2),
                "bucket_at": CUTOFF - timedelta(hours=2),
                "freshness_status": "realtime",
                "data": {"price": 12000.5},
            },
            {
                "id": 3,
                "data_type": "provider_new_signal",
                "subject_type": "market",
                "subject_id": "cn:new_signal",
                "market": "cn",
                "provider": "ths",
                "trade_date": date(2026, 8, 8),
                "observed_at": CUTOFF - timedelta(minutes=5),
                "fetched_at": CUTOFF - timedelta(minutes=4),
                "bucket_at": CUTOFF - timedelta(minutes=5),
                "freshness_status": "fresh",
                "data": {"signal": "new"},
            },
        ]

    def list_latest_metadata(
        self,
        *,
        data_types=None,
        cutoff_at=None,
        limit=2000,
        **_kwargs,
    ):
        self.cutoffs.append(cutoff_at)
        rows = self.rows
        if data_types:
            rows = [row for row in rows if row["data_type"] in data_types]
        return [
            {key: value for key, value in row.items() if key != "data"}
            for row in rows[:limit]
        ]

    def list_latest_for_agent(
        self,
        *,
        data_types,
        cutoff_at,
        limit,
    ):
        self.cutoffs.append(cutoff_at)
        rows = [row for row in self.rows if row["data_type"] in data_types]
        return {"total": len(rows), "items": rows[:limit]}

    def list_latest_per_data_type_for_agent(
        self,
        *,
        data_types,
        cutoff_at,
        per_data_type_limit,
    ):
        self.cutoffs.append(cutoff_at)
        selected = []
        counts = {}
        for data_type in data_types:
            rows = [row for row in self.rows if row["data_type"] == data_type]
            counts[data_type] = len(rows)
            selected.extend(rows[:per_data_type_limit])
        return {
            "total": sum(counts.values()),
            "items": selected,
            "counts_by_data_type": counts,
        }

    def query_latest_series_at(self, *, series, cutoff_at, available_at=None):
        return {}

    def get_by_id_at(self, *, snapshot_id, cutoff_at):
        return next(
            (row for row in self.rows if row["id"] == snapshot_id),
            None,
        )


class _CollectionRepository:
    def __init__(self) -> None:
        self.inventory_cutoff = None
        self.records_kwargs = None

    def inventory(self, *, cutoff_at=None):
        self.inventory_cutoff = cutoff_at
        return [
            {
                "domain": "news",
                "title": "新闻原始数据",
                "available": True,
                "total": 10,
                "latest_at": CUTOFF - timedelta(minutes=10),
                "groups": [{"name": "ths", "count": 10}],
            },
            {
                "domain": "macro_regime",
                "title": "宏观状态",
                "available": False,
                "total": 0,
                "latest_at": None,
                "groups": [],
                "error": "database detail must not leak",
            },
        ]

    def list_records(self, **kwargs):
        self.records_kwargs = kwargs
        return {
            "domain": kwargs["domain_key"],
            "title": "新闻原始数据",
            "available": True,
            "total": 2,
            "items": [
                {
                    "id": 9,
                    "source": "ths",
                    "published_at": CUTOFF - timedelta(hours=1),
                    "title": "市场新闻",
                    "content": "x" * 1200,
                    "payload_hash": "hidden",
                }
            ],
        }

    @staticmethod
    def record_identity(*, domain_key, record):
        return {"id": record["id"]}

    def get_record_at(self, *, domain_key, identity, cutoff_at):
        result = self.list_records(
            domain_key=domain_key,
            cutoff_at=cutoff_at,
            group=None,
            query=None,
            limit=1,
            offset=0,
        )
        return next(
            (
                row
                for row in result["items"]
                if row.get("id") == identity.get("id")
            ),
            None,
        )


def _service():
    snapshots = _SnapshotRepository()
    collections = _CollectionRepository()
    service = AgentMarketQueryService(
        snapshot_repository=snapshots,
        collection_repository=collections,
        now=lambda: NOW,
    )
    return service, snapshots, collections


def test_data_catalog_is_cutoff_aware_and_hides_storage_errors() -> None:
    service, _snapshots, collections = _service()

    result = service.data_catalog(cutoff_at=CUTOFF)

    assert collections.inventory_cutoff == CUTOFF
    assert result["status"] == "partial"
    assert result["domains"][0]["entry_tool"] == "market_domain_open"
    assert result["domains"][1]["unavailable_reason"] == "storage_not_ready"
    assert "database detail" not in str(result)
    assert result["evidence_domains"][0]["domain"] == "knowledge_graph"


def test_data_catalog_exposes_semantic_topic_then_exact_domain_groups() -> None:
    service, _snapshots, _collections = _service()

    topic = service.data_catalog(cutoff_at=CUTOFF, topic="news_research")
    domain = service.data_catalog(
        cutoff_at=CUTOFF,
        topic="news_research",
        domain="news",
        group_limit=10,
    )

    assert topic["scope"] == "topic"
    assert topic["topic_detail"]["questions"]
    assert topic["topic_detail"]["specialized_tools"] == [
        "market_domain_open",
        "kg_relation_graph_search",
    ]
    assert domain["scope"] == "domain"
    assert domain["domains"][0]["groups"] == [{"name": "ths", "count": 10}]


def test_data_catalog_rejects_unknown_domain_instead_of_encouraging_guessing() -> None:
    service, _snapshots, _collections = _service()

    with pytest.raises(ValueError, match="unknown collection domain"):
        service.data_catalog(cutoff_at=CUTOFF, domain="sector_style")


def test_market_frame_is_bounded_and_exposes_mixed_trade_date_quality() -> None:
    service, snapshots, _collections = _service()

    result = service.market_frame(cutoff_at=CUTOFF)

    assert snapshots.cutoffs == [CUTOFF]
    assert result["frame_id"] == f"market-frame:{CUTOFF.isoformat()}"
    assert result["market_session"] == "non_trading_day"
    assert result["change_detection"]["status"] == "no_change"
    assert result["change_detection"]["baseline_cutoff_at"] < CUTOFF
    assert result["quality_issues"][0]["issue_code"] == "mixed_trade_dates"
    dimensions = {item["dimension"]: item for item in result["dimensions"]}
    assert dimensions["a_share_market"]["subject_count"] == 1
    assert dimensions["futures_commodities"]["subject_count"] == 1
    assert dimensions["other_market"]["subject_count"] == 1
    assert all("data" not in item for item in result["dimensions"])


def test_market_frame_accepts_next_trade_date_during_futures_night_session() -> None:
    service, snapshots, _collections = _service()
    cutoff = datetime(2026, 8, 11, 15, 32, tzinfo=UTC)
    service._now = lambda: cutoff
    snapshots.rows = [
        {
            **snapshots.rows[0],
            "trade_date": date(2026, 8, 11),
            "observed_at": cutoff - timedelta(minutes=1),
            "fetched_at": cutoff,
            "bucket_at": cutoff - timedelta(minutes=1),
        },
        {
            **snapshots.rows[1],
            "trade_date": date(2026, 8, 12),
            "observed_at": cutoff - timedelta(minutes=1),
            "fetched_at": cutoff,
            "bucket_at": cutoff - timedelta(minutes=1),
        },
    ]

    result = service.market_frame(cutoff_at=cutoff)

    assert not any(
        issue["issue_code"] == "trade_date_after_calendar_session"
        for issue in result["quality_issues"]
    )


def test_market_frame_does_not_apply_a_share_calendar_to_global_dimensions() -> None:
    service, snapshots, _collections = _service()
    snapshots.rows = [{
        **snapshots.rows[1],
        "data_type": "global_index",
        "subject_id": "us:index:spx",
        "trade_date": date(2026, 8, 15),
    }]

    result = service.market_frame(cutoff_at=CUTOFF)

    assert not any(
        issue["issue_code"] == "trade_date_after_calendar_session"
        for issue in result["quality_issues"]
    )


def test_market_frame_detects_material_same_session_metric_change() -> None:
    service, snapshots, _collections = _service()

    def query_latest_series_at(*, series, cutoff_at, available_at=None):
        temperature = 80 if cutoff_at == CUTOFF else 50
        return {
            ("market_sentiment", "cn:a_share:ths_temperature"): {
                "id": 100 if cutoff_at == CUTOFF else 90,
                "data_type": "market_sentiment",
                "subject_id": "cn:a_share:ths_temperature",
                "observed_at": cutoff_at,
                "bucket_at": cutoff_at,
                "fetched_at": cutoff_at,
                "data": {"temperature": temperature},
            }
        }

    snapshots.query_latest_series_at = query_latest_series_at

    result = service.market_frame(cutoff_at=CUTOFF)

    change = next(
        item
        for item in result["significant_changes"]
        if item["metric"] == "temperature"
    )
    assert change["current_value"] == 80
    assert change["baseline_value"] == 50
    assert change["percent_change"] == 60
    identity = decode_market_evidence_locator(
        change["current_evidence_locator"]
    )
    assert identity.identity == {"id": 100}


def test_market_frame_aligns_stale_intraday_signal_to_prior_same_time() -> None:
    service, snapshots, _collections = _service()
    cutoff = datetime(2026, 8, 10, 6, 24, tzinfo=UTC)  # 14:24 China time
    current_fact = datetime(2026, 8, 10, 3, 30, tzinfo=UTC)  # 11:30
    prior_same_time = datetime(2026, 8, 7, 3, 30, tzinfo=UTC)
    calls: list[datetime] = []

    def query_latest_series_at(*, series, cutoff_at, available_at=None):
        calls.append(cutoff_at)
        key = ("market_capital", "cn:a_share:market_capital")
        if key not in series:
            return {}
        if cutoff_at == cutoff:
            value, observed_at = 100.0, current_fact
        elif cutoff_at == prior_same_time:
            value, observed_at = 80.0, prior_same_time
        else:
            value, observed_at = 20.0, cutoff_at
        return {
            key: {
                "id": len(calls),
                "data_type": key[0],
                "subject_id": key[1],
                "observed_at": observed_at,
                "bucket_at": observed_at,
                "fetched_at": observed_at,
                "data": {"net_inflow": value},
            }
        }

    service._now = lambda: cutoff
    snapshots.query_latest_series_at = query_latest_series_at

    result = service.market_frame(cutoff_at=cutoff)

    change = next(
        item
        for item in result["significant_changes"]
        if item["metric"] == "net_inflow"
    )
    assert prior_same_time in calls
    assert change["current_value"] == 100
    assert change["baseline_value"] == 80


def test_market_change_brief_combines_relevant_dimensions_and_stop_state() -> None:
    service, snapshots, _collections = _service()
    for row in snapshots.rows:
        row["trade_date"] = date(2026, 8, 7)

    def query_latest_series_at(*, series, cutoff_at, available_at=None):
        temperature = 80 if cutoff_at == CUTOFF else 50
        return {
            ("market_sentiment", "cn:a_share:ths_temperature"): {
                "id": 100 if cutoff_at == CUTOFF else 90,
                "data_type": "market_sentiment",
                "subject_id": "cn:a_share:ths_temperature",
                "observed_at": cutoff_at,
                "bucket_at": cutoff_at,
                "fetched_at": cutoff_at,
                "data": {"temperature": temperature},
            }
        }

    snapshots.query_latest_series_at = query_latest_series_at

    result = service.market_change_brief(
        cutoff_at=CUTOFF,
        focus="risk_appetite",
        per_dimension_limit=2,
    )

    assert result["operation"] == "market_change_brief_open"
    assert result["research_state"] == "material_change_detected"
    assert result["selected_dimensions"][0] == "sentiment"
    assert len(result["selected_dimensions"]) == 4
    assert all(
        len(item["facts"]) <= 2 for item in result["dimension_facts"]
    )
    assert result["next_operations"] == [
        "market_topic_open",
        "market_dimension_open",
        "market_sector_compare_open",
        "market_evidence_open",
    ]


def test_market_dimension_returns_compact_facts_and_evidence_locator() -> None:
    service, _snapshots, _collections = _service()

    result = service.market_dimension(
        dimension="a_share_market",
        cutoff_at=CUTOFF,
        limit=10,
    )

    identity = decode_market_evidence_locator(
        result["facts"][0]["evidence_locator"]
    )
    assert identity.kind == "snapshot"
    assert identity.identity == {"id": 1, "trade_date": "2026-08-08"}
    assert result["facts"][0]["data_preview"]["large_rows"] == {
        "_item_count": 30
    }
    assert "large_rows" in result["facts"][0]["data_fields"]
    assert result["read_path"] == "database"


def test_market_dimension_can_select_exact_data_families_without_newest_feed_starvation() -> None:
    service, snapshots, _collections = _service()
    snapshots.rows.extend([
        {
            **snapshots.rows[0],
            "id": 10,
            "data_type": "ths_cn_index_quote",
            "subject_id": "cn:index:000001",
        },
        {
            **snapshots.rows[0],
            "id": 11,
            "data_type": "market_anomaly",
            "subject_id": "cn:a_share:ths_anomaly",
        },
    ])

    result = service.market_dimension(
        dimension="a_share_market",
        cutoff_at=CUTOFF,
        data_types=["ths_cn_market_breadth", "ths_cn_index_quote", "market_anomaly"],
    )

    assert [item["data_type"] for item in result["facts"]] == [
        "ths_cn_market_breadth",
        "ths_cn_index_quote",
        "market_anomaly",
    ]
    assert result["selected_data_types"] == [
        "ths_cn_market_breadth",
        "ths_cn_index_quote",
        "market_anomaly",
    ]


def test_global_market_overview_selects_us_indices_not_arbitrary_latest_rows() -> None:
    service, snapshots, _collections = _service()
    snapshots.rows.extend(
        [
            {
                "id": 20,
                "data_type": "ths_us_market_module",
                "subject_type": "us_market",
                "subject_id": "ranking_noise_stream",
                "market": "us",
                "provider": "ths_native_stream",
                "trade_date": date(2026, 8, 7),
                "bucket_at": CUTOFF,
                "data": {"native_table": {"dataDict": {"55": ["噪声"]}}},
            },
            {
                "id": 21,
                "data_type": "ths_us_market_module",
                "subject_type": "us_market",
                "subject_id": "indices_stream",
                "market": "us",
                "provider": "ths_native_stream",
                "trade_date": date(2026, 8, 7),
                "bucket_at": CUTOFF - timedelta(minutes=2),
                "data": {
                    "native_table": {
                        "dataDict": {
                            "4": ["DJI", "SPX"],
                            "55": ["道琼斯", "标普500"],
                            "10": [45000, 6500],
                            "34818": ["0.5%", "0.8%"],
                        }
                    }
                },
            },
        ]
    )

    result = service.global_market_overview(cutoff_at=CUTOFF)

    assert result["status"] == "available"
    assert [row["code"] for row in result["us_market"]["indices"]] == [
        "DJI",
        "SPX",
    ]
    assert result["us_market"]["trade_date"] == date(2026, 8, 7)
    assert len(result["us_market"]["evidence_locators"]) == 1
    assert result["us_market"]["indices_evidence_locator"] is not None
    identity = decode_market_evidence_locator(
        result["us_market"]["indices_evidence_locator"]
    )
    assert identity.identity["trade_date"] == "2026-08-07"
    assert result["us_market"]["breadth_evidence_locator"] is None
    assert result["us_market"]["leading_industries_evidence_locator"] is None
    assert result["us_market"]["leading_concepts_evidence_locator"] is None
    assert "ranking_noise_stream" not in str(result["us_market"])


def test_market_topic_covers_snapshot_and_persisted_domain_handles() -> None:
    service, _snapshots, _collections = _service()

    result = service.market_topic(
        topic="a_share",
        cutoff_at=CUTOFF,
        limit=5,
    )

    assert result["topic"] == "a_share"
    assert result["dimensions"] == ["a_share_market"]
    assert result["snapshot_facts"][0]["data_type"] == "ths_cn_market_breadth"
    assert result["related_domains"][0]["domain"] == "market_cache"
    assert result["related_domains"][0]["next_operation"] == "market_domain_open"


def test_market_domain_applies_cutoff_paging_and_compacts_provider_payload() -> None:
    service, _snapshots, collections = _service()

    result = service.market_domain(
        domain="news",
        cutoff_at=CUTOFF,
        group="ths",
        limit=5,
        offset=0,
    )

    assert collections.records_kwargs["cutoff_at"] == CUTOFF
    assert collections.records_kwargs["group"] == "ths"
    identity = decode_market_evidence_locator(
        result["items"][0]["evidence_locator"]
    )
    assert identity.kind == "domain"
    assert identity.domain == "news"
    assert identity.identity == {"id": 9}
    assert result["items"][0]["content"].endswith("…")
    assert "payload_hash" not in result["items"][0]
    assert result["truncated"] is True


def test_market_evidence_reopens_exact_snapshot_field_under_cutoff() -> None:
    service, _snapshots, _collections = _service()
    dimension = service.market_dimension(
        dimension="a_share_market",
        cutoff_at=CUTOFF,
        limit=1,
    )
    locator = dimension["facts"][0]["evidence_locator"]

    result = service.market_evidence(
        locator=locator,
        cutoff_at=CUTOFF,
        fields=["data.up_count"],
    )

    assert result["status"] == "available"
    assert result["values"][0]["value"] == 2200
    field_identity = decode_market_evidence_locator(
        result["values"][0]["evidence_locator"]
    )
    assert field_identity.field == "data.up_count"

    shorthand = service.market_evidence(
        locator=locator,
        cutoff_at=CUTOFF,
        fields=["up_count"],
    )
    assert shorthand["values"][0]["value"] == 2200
    assert shorthand["missing_fields"] == []


def test_agent_market_query_rejects_future_or_naive_cutoff() -> None:
    service, _snapshots, _collections = _service()

    with pytest.raises(ValueError, match="timezone"):
        service.market_frame(cutoff_at=datetime(2026, 8, 8, 6, 0))
    with pytest.raises(ValueError, match="future"):
        service.market_frame(cutoff_at=NOW + timedelta(seconds=6))
