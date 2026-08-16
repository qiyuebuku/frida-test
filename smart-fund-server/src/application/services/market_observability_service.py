"""Read models for the persisted market-data observability dashboard."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from src.application.services.watchlist_service import WatchlistService
from src.infrastructure.persistence.repositories import (
    CollectionObservabilityRepository,
    CollectionRunRepository,
    EtfDailyShareRepository,
    MarketSnapshotRepository,
)


MARKET_OVERVIEW_TYPES = [
    "market_breadth",
    "ths_cn_index_quote",
    "ths_cn_market_summary",
    "ths_cn_market_breadth",
    "ths_market_profile",
    "sector_quote",
    "sector_flow",
    "index_quote",
    "futures_quote",
    "forex_quote",
    "quote",
    "realtime",
    "minute_data",
    "stock_flow",
    "interest_rate",
    "government_bond_yield",
    "market_pe",
    "market_pb",
    "bond_index",
    "etf_estimated_net_inflow",
    "market_anomaly",
    "call_auction",
    "market_capital",
    "market_sentiment",
    "futures_intraday",
    "forex_intraday",
    "reverse_repo",
    "index_sentiment",
    "northbound_capital",
    "northbound_capital_current",
    "northbound_turnover",
    "market_valuation_threshold",
    "bond_market_price",
]
CROSS_MARKET_TYPES = {
    "index_quote",
    "futures_quote",
    "forex_quote",
    "futures_intraday",
    "forex_intraday",
}
MACRO_MARKET_TYPES = {
    "interest_rate",
    "government_bond_yield",
    "market_pe",
    "market_pb",
    "bond_index",
    "reverse_repo",
    "index_sentiment",
    "market_valuation_threshold",
    "bond_market_price",
}
THS_MARKET_SIGNAL_TYPES = {
    "market_anomaly",
    "call_auction",
    "market_capital",
    "market_sentiment",
    "northbound_capital",
}
THS_SECTOR_TYPES = {
    "ths_sector_hot",
    "ths_sector_ranking",
    "ths_sector_flow",
    "ths_sector_rotation",
    "ths_industry_opportunity",
    "ths_sector_prosperity",
    "ths_sector_commodity_linkage",
}
THS_SECTOR_DETAIL_TYPES = THS_SECTOR_TYPES | {
    "ths_sector_constituents",
    "ths_sector_daily",
}

# Stable frontend series. Intraday series only read their latest trade date;
# daily series retain enough observations for a medium-term view.
CHART_SERIES_SPECS = {
    "breadth_intraday": (
        "ths_cn_market_breadth", "cn:a_share:ths_breadth", 300, True
    ),
    "etf_intraday": ("etf_estimated_net_inflow", "cn:etf:szse:estimated_net_inflow", 300, True),
    "market_capital": ("market_capital", "cn:a_share:market_capital", 300, True),
    "northbound_capital": ("northbound_turnover", "cn:northbound:turnover:ths", 300, False),
    "market_sentiment": ("market_sentiment", "cn:a_share:ths_temperature", 300, True),
    "sentiment_sh50": ("index_sentiment", "cn:index:sh50", 120, False),
    "sentiment_growth": ("index_sentiment", "cn:index:growth", 120, False),
    "futures_a50": ("futures_intraday", "global:futures:ftse_a50", 1600, True),
    "futures_dow": ("futures_intraday", "global:futures:dow_jones", 1600, True),
    "usd_cny": ("forex_intraday", "cn:forex:usd_cny:ths", 600, True),
    "reverse_repo": ("reverse_repo", "cn:monetary:reverse_repo", 260, False),
    "valuation_sh": (
        "market_valuation_threshold",
        "cn:market:sh",
        300,
        False,
    ),
    "valuation_sz": (
        "market_valuation_threshold",
        "cn:market:sz",
        300,
        False,
    ),
    "bond_long": ("bond_market_price", "cn:bond_futures:T9999", 520, False),
    "bond_short": ("bond_market_price", "cn:bond_futures:TS9999", 520, False),
    "bond_benchmark": ("bond_market_price", "cn:index:ths_all_a", 520, False),
    "market_anomaly": ("market_anomaly", "cn:a_share:ths_anomaly", 300, True),
    "call_auction": ("call_auction", "cn:a_share:call_auction", 120, True),
}
CHINA_TIMEZONE = ZoneInfo("Asia/Shanghai")


class MarketObservabilityService:
    """Build a frontend-ready view from persisted collection facts."""

    def __init__(
        self,
        *,
        snapshot_repository: MarketSnapshotRepository | None = None,
        run_repository: CollectionRunRepository | None = None,
        etf_share_repository: EtfDailyShareRepository | None = None,
        watchlist_service: WatchlistService | None = None,
        collection_repository: CollectionObservabilityRepository | None = None,
    ) -> None:
        self._snapshots = snapshot_repository or MarketSnapshotRepository()
        self._runs = run_repository or CollectionRunRepository()
        self._etf_shares = (
            etf_share_repository or EtfDailyShareRepository()
        )
        self._watchlist = watchlist_service or WatchlistService()
        self._collections = (
            collection_repository or CollectionObservabilityRepository()
        )
        self._sector_detail_cache: dict[datetime, list[dict[str, Any]]] = {}
        self._sector_detail_cache_lock = Lock()

    def stock_ranking(
        self,
        *,
        sort: str,
        count: int = 20,
    ) -> dict[str, Any]:
        """Read the latest persisted ranking snapshot without upstream I/O."""

        valid = {
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
        if sort not in valid:
            raise ValueError(f"sort 必须是 {'/'.join(sorted(valid))}")
        normalized_count = max(1, min(int(count), 50))
        rows = self._snapshots.query_latest(
            subject_ids=[sort],
            data_types=["stock_ranking"],
        )
        if not rows:
            return {
                "status": "empty",
                "provider": "database_snapshot",
                "market": "cn",
                "data": {
                    "sort": sort,
                    "count": 0,
                    "available_count": 0,
                    "stocks": [],
                },
                "provider_metadata": {
                    "data_type": "stock_ranking",
                    "subject_id": sort,
                    "upstream_requested": False,
                },
            }

        row = rows[0]
        result = {
            "status": "ok",
            "provider": row.get("provider") or "database_snapshot",
            "market": row.get("market") or "cn",
            "trade_date": row.get("trade_date"),
            "observed_at": row.get("observed_at"),
            "fetched_at": row.get("fetched_at"),
            "data": dict(row.get("data") or {}),
        }
        data = dict(result["data"])
        available_stocks = list(data.get("stocks") or [])
        selected_stocks = available_stocks[:normalized_count]
        data.update(
            {
                "sort": sort,
                "count": len(selected_stocks),
                "available_count": len(available_stocks),
                "stocks": selected_stocks,
            }
        )
        result["data"] = data
        metadata = dict(result.get("provider_metadata") or {})
        metadata.update(
            {
                "data_type": "stock_ranking",
                "subject_id": sort,
                "bucket_at": row.get("bucket_at"),
                "freshness_status": row.get("freshness_status"),
                "upstream_requested": False,
            }
        )
        result["provider_metadata"] = metadata
        return result

    def stock_dynamic_groups(
        self,
        *,
        count_per_group: int = 20,
        scope: str = "featured",
    ) -> dict[str, Any]:
        """Read the latest persisted THS dynamic stock groups."""

        normalized_count = max(1, min(int(count_per_group), 100))
        normalized_scope = str(scope or "featured").strip().lower()
        if normalized_scope not in {"featured", "candidates"}:
            raise ValueError("scope must be featured or candidates")
        rows = self._snapshots.list_latest(
            data_types=["stock_dynamic_group"],
            subject_type="ranking",
            limit=100,
        )
        groups: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row.get("data") or {})
            featured_stocks = list(
                data.get("featured_stocks") or data.get("stocks") or []
            )
            candidate_stocks = list(data.get("candidate_stocks") or [])
            stocks = (
                candidate_stocks
                if normalized_scope == "candidates"
                else featured_stocks
            )
            data.update(
                {
                    "data_code": str(
                        data.get("data_code") or row.get("subject_id") or ""
                    ),
                    "available_count": len(stocks),
                    "count": min(len(stocks), normalized_count),
                    "stocks": stocks[:normalized_count],
                    "scope": normalized_scope,
                    "featured_count": len(featured_stocks),
                    "candidate_count": len(candidate_stocks),
                    "snapshot": {
                        "bucket_at": row.get("bucket_at"),
                        "fetched_at": row.get("fetched_at"),
                        "freshness_status": row.get("freshness_status"),
                    },
                }
            )
            groups.append(data)

        groups.sort(
            key=lambda item: (
                int(item.get("display_order", 10000)),
                str(item.get("title") or item.get("data_code") or ""),
            )
        )
        return {
            "status": "ok" if groups else "empty",
            "provider": "database_snapshot",
            "market": "cn",
            "data": {
                "count": len(groups),
                "count_per_group": normalized_count,
                "scope": normalized_scope,
                "groups": groups,
            },
            "provider_metadata": {
                "data_type": "stock_dynamic_group",
                "upstream_requested": False,
            },
        }

    def sector_overview(
        self,
        *,
        limit_per_group: int = 20,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Build the THS sector workspace exclusively from persisted rows."""

        normalized_limit = max(1, min(int(limit_per_group), 100))
        rows = self._snapshots.list_latest(
            data_types=sorted(THS_SECTOR_TYPES),
            cutoff_at=cutoff_at,
            limit=10000,
        )
        grouped = _group_by_data_type(rows)
        hot_rows = _prefer_latest_hot_sector_batches(
            _prefer_current_index_hot_rows(
                grouped.get("ths_sector_hot", [])
            )
        )
        commodity_rows = _prefer_typed_commodity_linkage_rows(
            grouped.get("ths_sector_commodity_linkage", [])
        )
        ranking_rows = _prefer_latest_sector_ranking_batches(
            grouped.get("ths_sector_ranking", [])
        )
        return {
            "generated_at": datetime.now(timezone.utc),
            "provider": "database_snapshot",
            "upstream_requested": False,
            "facts": {
                "hot": _group_sector_snapshots(
                    hot_rows,
                    group_key="sector_type",
                    sort_key="heat_rank",
                    descending=False,
                    limit=normalized_limit,
                ),
                "rankings": _group_sector_snapshots_nested(
                    ranking_rows,
                    outer_key="sector_type",
                    inner_key="metric",
                    sort_key="rank",
                    descending=False,
                    limit=normalized_limit,
                ),
                "fund_flows": _group_sector_flow_extremes(
                    grouped.get("ths_sector_flow", []),
                    limit=normalized_limit,
                ),
            },
            "provider_signals": {
                "rotation": self._sector_rotation_overview(
                    grouped.get("ths_sector_rotation", []),
                    day_limit=3,
                    rank_limit=min(normalized_limit, 10),
                    cutoff_at=cutoff_at,
                ),
                "industry_opportunities": _group_sector_snapshots(
                    grouped.get("ths_industry_opportunity", []),
                    group_key="opportunity_category",
                    sort_key="rank",
                    descending=False,
                    limit=normalized_limit,
                ),
                "prosperity": _sector_snapshot_rows(
                    grouped.get("ths_sector_prosperity", []),
                    sort_key="rank",
                    descending=False,
                )[:normalized_limit],
                "commodity_linkage": _group_sector_snapshots(
                    commodity_rows,
                    group_key="linkage_type",
                    sort_key="rank",
                    descending=False,
                    limit=normalized_limit,
                ),
            },
            "freshness": _sector_freshness(rows),
            "total": len(rows),
        }

    def _sector_rotation_overview(
        self,
        latest_rows: list[dict[str, Any]],
        *,
        day_limit: int,
        rank_limit: int,
        cutoff_at: datetime | None = None,
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        series = [
            ("ths_sector_rotation", str(row.get("subject_id")))
            for row in latest_rows
            if row.get("subject_id")
        ]
        if not series:
            return {}
        history_rows = latest_rows
        query_histories = getattr(self._snapshots, "query_histories", None)
        if callable(query_histories):
            histories = query_histories(
                series=series,
                cutoff_at=cutoff_at,
                limit_per_series=120,
            )
            queried_rows = [
                row
                for rows in histories.values()
                for row in rows
            ]
            if queried_rows:
                history_rows = queried_rows
        return _group_sector_rotation_periods(
            history_rows,
            day_limit=day_limit,
            rank_limit=rank_limit,
        )

    def sector_ranking(
        self,
        *,
        data_type: str,
        metric: str | None = None,
        sector_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        if data_type not in THS_SECTOR_TYPES:
            raise ValueError("unsupported THS sector data_type")
        rows = self._snapshots.list_latest(
            data_types=[data_type],
            cutoff_at=cutoff_at,
            limit=10000,
        )
        items = _sector_snapshot_rows(rows)
        if metric:
            items = [item for item in items if item.get("metric") == metric]
        if sector_type:
            items = [
                item for item in items
                if item.get("sector_type") == sector_type
            ]
        trade_dates = [
            str(value)[:10]
            for item in items
            if (
                value := (
                    item.get("trade_date")
                    or item.get("source_date")
                    or item.get("observed_at")
                    or item.get("bucket_at")
                )
            )
        ]
        latest_trade_date = max(trade_dates) if trade_dates else None
        if latest_trade_date:
            # A ranking is one market cross-section. ``list_latest`` returns
            # the last row per subject, so inactive subjects may otherwise
            # contribute stale rows from older trading days to today's table.
            items = [
                item
                for item in items
                if str(
                    item.get("trade_date")
                    or item.get("source_date")
                    or item.get("observed_at")
                    or item.get("bucket_at")
                )[:10] == latest_trade_date
            ]
        items.sort(key=_sector_rank_sort_key)
        normalized_offset = max(0, int(offset))
        normalized_limit = max(1, min(int(limit), 200))
        return {
            "data_type": data_type,
            "metric": metric,
            "sector_type": sector_type,
            "trade_date": latest_trade_date,
            "total": len(items),
            "offset": normalized_offset,
            "limit": normalized_limit,
            "items": items[
                normalized_offset : normalized_offset + normalized_limit
            ],
            "upstream_requested": False,
        }

    def sector_detail(
        self,
        *,
        provider_sector_code: str,
        sector_type: str | None = None,
        history_limit: int = 300,
        cutoff_at: datetime | None = None,
    ) -> dict[str, Any]:
        code = provider_sector_code.strip()
        if not code:
            raise ValueError("provider_sector_code is required")
        latest_rows = self._sector_detail_latest_rows(cutoff_at=cutoff_at)
        matched = [
            row for row in latest_rows
            if str((row.get("data") or {}).get("provider_sector_code") or "")
            == code
            and (
                not sector_type
                or str((row.get("data") or {}).get("sector_type") or "")
                == sector_type
            )
        ]
        reference_rows = [
            row
            for row in matched
            if row.get("data_type") != "ths_sector_constituents"
        ]
        series_keys = [
            (str(row["data_type"]), str(row["subject_id"]))
            for row in reference_rows
        ]
        batch_query = getattr(self._snapshots, "query_histories", None)
        history_by_series = (
            batch_query(
                series=series_keys,
                cutoff_at=cutoff_at,
                limit_per_series=max(1, min(int(history_limit), 1000)),
            )
            if callable(batch_query)
            else {}
        )
        series = []
        for row in reference_rows:
            key = (str(row["data_type"]), str(row["subject_id"]))
            history = history_by_series.get(key)
            if history is None:
                history = self._snapshots.query_history(
                    subject_id=key[1],
                    data_type=key[0],
                    cutoff_at=cutoff_at,
                    limit=max(1, min(int(history_limit), 1000)),
                )
            series.append(
                {
                    "data_type": row["data_type"],
                    "subject_id": row["subject_id"],
                    "items": [
                        _compact_snapshot(item) for item in reversed(history)
                    ],
                }
            )
        # Constituents can contain hundreds of rows. Return them exactly once
        # through the dedicated field instead of duplicating them in latest
        # and historical series.
        latest_items = _sector_snapshot_rows(reference_rows)
        constituent_row = next(
            (
                row
                for row in matched
                if row.get("data_type") == "ths_sector_constituents"
                and _daily_bucket_matches_trade_date(row)
            ),
            None,
        )
        constituent_data = (
            constituent_row.get("data") or {} if constituent_row else {}
        )
        # THS embeds a related ETF in several sector payloads, but that value
        # is a navigation recommendation rather than a stable sector identity.
        # Different payloads and collection times can legitimately nominate
        # different funds.  Preserve every distinct candidate and force the
        # caller to validate identity/holdings instead of arbitrarily selecting
        # whichever row happens to be first.
        etf_candidates_by_code: dict[str, dict[str, Any]] = {}
        for row in matched:
            data = row.get("data") or {}
            etf_code = str(data.get("representative_etf_code") or "").strip()
            if not etf_code:
                continue
            candidate = etf_candidates_by_code.setdefault(
                etf_code,
                {"code": etf_code, "name": data.get("representative_etf_name")},
            )
            if not candidate.get("name") and data.get("representative_etf_name"):
                candidate["name"] = data["representative_etf_name"]
        return {
            "provider_sector_code": code,
            "sector_type": sector_type,
            "found": bool(matched),
            "latest": latest_items,
            "series": series,
            "constituents": constituent_data.get("constituents") or [],
            "constituent_count": constituent_data.get("total_count")
            or constituent_data.get("count")
            or 0,
            "constituent_evidence": (
                _compact_snapshot(constituent_row)
                if constituent_row is not None
                else None
            ),
            "etf_navigation_candidates": [
                etf_candidates_by_code[key] for key in sorted(etf_candidates_by_code)
            ],
            "etf_navigation_note": (
                "同花顺关联ETF仅用于导航，不是稳定板块代理；输出具体表达前必须调用"
                " market_expression_compare_open 核验身份、跟踪指数、持仓和流动性。"
            ),
            "upstream_requested": False,
        }

    def _sector_detail_latest_rows(
        self,
        *,
        cutoff_at: datetime | None,
    ) -> list[dict[str, Any]]:
        if cutoff_at is None:
            return self._snapshots.list_latest(
                data_types=sorted(THS_SECTOR_DETAIL_TYPES),
                limit=10000,
            )
        with self._sector_detail_cache_lock:
            cached = self._sector_detail_cache.get(cutoff_at)
            if cached is not None:
                return cached
            rows = self._snapshots.list_latest(
                data_types=sorted(THS_SECTOR_DETAIL_TYPES),
                cutoff_at=cutoff_at,
                limit=10000,
            )
            self._sector_detail_cache[cutoff_at] = rows
            while len(self._sector_detail_cache) > 4:
                self._sector_detail_cache.pop(next(iter(self._sector_detail_cache)))
            return rows

    def dashboard(self, *, hours: int = 24) -> dict[str, Any]:
        normalized_hours = max(1, min(int(hours), 168))
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=normalized_hours)
        snapshot_summary = self._snapshots.summarize_since(since)
        run_summary = self._runs.summarize_since(since)
        latest = self._snapshots.list_latest(
            data_types=MARKET_OVERVIEW_TYPES,
            limit=5000,
        )
        latest_by_type = _group_by_data_type(latest)
        chart_series = self._chart_series(latest_by_type)
        northbound_current = max(
            latest_by_type.get("northbound_capital_current", [])
            or latest_by_type.get("northbound_capital", []),
            key=lambda item: item.get("bucket_at")
            or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        if northbound_current:
            chart_series["northbound_capital"]["latest"] = (
                _compact_snapshot(northbound_current)
            )
        dashboard_context = self._collections.dashboard_context()
        # THS is the authoritative dashboard source. Do not silently replace a
        # broken native stream with Eastmoney, because that hides incidents.
        breadth_rows = latest_by_type.get("ths_cn_market_breadth", [])
        latest_breadth = max(
            breadth_rows,
            key=lambda item: item.get("bucket_at")
            or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        latest_breadth = _merge_breadth_with_ths_indices(
            latest_breadth,
            latest_by_type.get("ths_cn_index_quote", []),
            latest_by_type.get("ths_cn_market_summary", []),
            breadth_rows,
        )
        breadth_history = chart_series["breadth_intraday"]["history"]
        previous_breadth_history = (
            self._snapshots.query_previous_trade_date_history(
                subject_id="cn:a_share:ths_breadth",
                data_type="ths_cn_market_breadth",
                before_date=latest_breadth["trade_date"],
                limit=1000,
            )
            if latest_breadth and latest_breadth.get("trade_date")
            else []
        )
        etf_estimated_rows = latest_by_type.get(
            "etf_estimated_net_inflow",
            [],
        )
        latest_etf_estimated_flow = max(
            etf_estimated_rows,
            key=lambda item: item.get("bucket_at")
            or datetime.min.replace(tzinfo=timezone.utc),
            default=None,
        )
        etf_estimated_history = chart_series["etf_intraday"]["history"]
        watchlist_items = self._watchlist.list_all()
        active_watchlist_codes = {
            str(item.code)
            for item in watchlist_items
            if item.enabled
        }
        collection_sources = _current_collection_sources(
            self._runs.list_latest(limit=200),
            active_watchlist_codes=active_watchlist_codes,
        )
        instrument_snapshots = {
            (row["subject_id"], row["data_type"]): row
            for row in latest
            if row.get("subject_type") == "instrument"
        }
        return {
            "generated_at": now,
            "window_hours": normalized_hours,
            "summary": {
                "snapshots": snapshot_summary,
                "runs": run_summary,
                "watchlist_count": sum(
                    1 for item in watchlist_items if item.enabled
                ),
                "etf_daily_shares": self._etf_shares.latest_summary(),
            },
            "market_breadth": {
                "latest": (
                    _compact_snapshot(latest_breadth)
                    if latest_breadth
                    else None
                ),
                "display_status": _cn_intraday_display_status(
                    latest_breadth,
                    now=now,
                ),
                "previous_same_time": _previous_same_time_comparison(
                    latest_breadth,
                    previous_breadth_history,
                ),
                "history": [
                    {
                        "bucket_at": row.get("bucket_at"),
                        "observed_at": row.get("observed_at"),
                        "freshness_status": row.get("freshness_status"),
                        "up_count": _number(row.get("data"), "up_count"),
                        "down_count": _number(
                            row.get("data"),
                            "down_count",
                        ),
                        "flat_count": _number(
                            row.get("data"),
                            "flat_count",
                        ),
                        "turnover": _number(row.get("data"), "turnover"),
                        "sh_index": _index_value(
                            row.get("data"),
                            code="000001",
                            key="close",
                        ),
                    }
                    for row in breadth_history
                ],
            },
            "etf_estimated_flow": {
                "latest": (
                    _compact_snapshot(latest_etf_estimated_flow)
                    if latest_etf_estimated_flow
                    else None
                ),
                "display_status": _cn_intraday_display_status(
                    latest_etf_estimated_flow,
                    now=now,
                ),
                "history": [
                    {
                        "bucket_at": row.get("bucket_at"),
                        "observed_at": row.get("observed_at"),
                        "freshness_status": row.get(
                            "freshness_status"
                        ),
                        "net_inflow_yuan": _number(
                            row.get("data"),
                            "net_inflow_yuan",
                        ),
                    }
                    for row in etf_estimated_history
                ],
            },
            "sector_quotes": _sector_rows(
                latest_by_type.get("sector_quote", []),
                value_key="change_pct",
            ),
            "sector_flows": _sector_rows(
                latest_by_type.get("sector_flow", []),
                value_key="main_net_inflow",
            ),
            "market_context": _market_context(
                dashboard_context,
                latest_profile=max(
                    latest_by_type.get("ths_market_profile", []),
                    key=lambda item: item.get("bucket_at")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    default=None,
                ),
                latest_breadth=latest_breadth,
                index_quotes=latest_by_type.get("ths_cn_index_quote", []),
            ),
            "macro_market": [
                _compact_snapshot(row)
                for data_type in MACRO_MARKET_TYPES
                for row in latest_by_type.get(data_type, [])
            ],
            "ths_market_signals": [
                _compact_snapshot(row)
                for data_type in THS_MARKET_SIGNAL_TYPES
                for row in latest_by_type.get(data_type, [])
            ],
            "cross_market": [
                _compact_snapshot(row)
                for data_type in CROSS_MARKET_TYPES
                for row in latest_by_type.get(data_type, [])
            ],
            "chart_series": chart_series,
            "watchlist": [
                _watchlist_row(item, instrument_snapshots)
                for item in watchlist_items
            ],
            "collection_sources": collection_sources,
            "latest_records": [
                _compact_snapshot(row)
                for row in sorted(
                    latest,
                    key=lambda item: item.get("bucket_at")
                    or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True,
                )[:300]
            ],
        }

    def _chart_series(
        self,
        latest_by_type: dict[str, list[dict[str, Any]]],
    ) -> dict[str, dict[str, Any]]:
        batch_query = getattr(self._snapshots, "query_histories", None)
        latest_by_series = {
            (data_type, subject_id): next(
                (
                    row
                    for row in latest_by_type.get(data_type, [])
                    if row.get("subject_id") == subject_id
                ),
                None,
            )
            for data_type, subject_id, _limit, _intraday in (
                CHART_SERIES_SPECS.values()
            )
        }
        active_series = [
            key for key, latest in latest_by_series.items() if latest
        ]
        date_windows = {}
        for key, latest in latest_by_series.items():
            if not latest or latest.get("trade_date") is None:
                continue
            spec = next(
                value
                for value in CHART_SERIES_SPECS.values()
                if value[:2] == key
            )
            trade_date = latest["trade_date"]
            history_days = (
                2_000
                if key[0] == "market_valuation_threshold"
                else 900
            )
            date_windows[key] = (
                trade_date
                if spec[3]
                else trade_date - timedelta(days=history_days),
                trade_date,
            )
        batch_rows = (
            batch_query(
                series=active_series,
                date_windows=date_windows,
                limit_per_series=max(
                    spec[2] for spec in CHART_SERIES_SPECS.values()
                ),
            )
            if callable(batch_query)
            else None
        )
        result: dict[str, dict[str, Any]] = {}
        for key, (data_type, subject_id, limit, latest_trade_only) in (
            CHART_SERIES_SPECS.items()
        ):
            latest = latest_by_series[(data_type, subject_id)]
            trade_date = latest.get("trade_date") if latest else None
            if batch_rows is not None:
                rows = batch_rows.get((data_type, subject_id), [])
                if latest_trade_only and trade_date is not None:
                    rows = [
                        row
                        for row in rows
                        if row.get("trade_date") == trade_date
                    ]
                rows = rows[:limit]
            else:
                rows = self._snapshots.query_history(
                    subject_id=subject_id,
                    data_type=data_type,
                    date_start=(trade_date if latest_trade_only else None),
                    date_end=(trade_date if latest_trade_only else None),
                    limit=limit,
                )
            result[key] = {
                "latest": _compact_snapshot(latest) if latest else None,
                "history": [
                    _compact_snapshot(row) for row in reversed(rows)
                ],
            }
        return result

    def list_snapshots(
        self,
        *,
        data_type: str | None = None,
        subject_type: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        rows = self._snapshots.list_latest(
            data_types=[data_type] if data_type else None,
            subject_type=subject_type,
            limit=limit,
        )
        return {
            "total": len(rows),
            "items": [_compact_snapshot(row) for row in rows],
        }

    def history(
        self,
        *,
        subject_id: str,
        data_type: str,
        limit: int = 500,
    ) -> dict[str, Any]:
        rows = self._snapshots.query_history(
            subject_id=subject_id,
            data_type=data_type,
            limit=limit,
        )
        return {
            "subject_id": subject_id,
            "data_type": data_type,
            "total": len(rows),
            "items": [_compact_snapshot(row) for row in rows],
        }

    def inventory(self) -> dict[str, Any]:
        domains = self._collections.inventory()
        return {
            "domain_count": len(domains),
            "available_count": sum(
                1 for domain in domains if domain.get("available")
            ),
            "total_records": sum(
                int(domain.get("total") or 0)
                for domain in domains
                if domain.get("available")
            ),
            "domains": domains,
        }

    def collection_records(
        self,
        *,
        domain: str,
        group: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._collections.list_records(
            domain_key=domain,
            group=group,
            query=query,
            limit=limit,
            offset=offset,
        )


def _market_context(
    context: dict[str, Any],
    *,
    latest_profile: dict[str, Any] | None = None,
    latest_breadth: dict[str, Any] | None = None,
    index_quotes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cache = context.get("market_cache") or {}
    sentiment = context.get("sentiment") or {}
    overview_row = cache.get("market_overview") or {}
    environment_row = cache.get("market_environment") or {}
    overview = _unwrap_collector_payload(overview_row.get("data"))
    environment = _unwrap_collector_payload(environment_row.get("data"))
    limit_pool_row = sentiment.get("limit_pool") or {}
    limit_pool = limit_pool_row.get("data") or {}
    hot_stocks_row = sentiment.get("xueqiu_hot_stocks") or {}

    overview_limit_up = overview.get("limit_up") or {}
    overview_limit_down = overview.get("limit_down") or {}
    limit_up = limit_pool.get("limit_up_count") or overview_limit_up
    limit_down = limit_pool.get("limit_down_count") or overview_limit_down
    if overview_limit_up.get("total") is not None:
        limit_up = dict(limit_up)
        limit_up.setdefault("today", {})["num"] = overview_limit_up["total"]
    if overview_limit_down.get("total") is not None:
        limit_down = dict(limit_down)
        limit_down.setdefault("today", {})["num"] = overview_limit_down["total"]
    yesterday_limit = overview.get("yesterday_limit") or {}
    profile = (latest_profile or {}).get("data") or {}
    if profile:
        yesterday_limit = profile.get("yesterday_limit") or yesterday_limit
        profile_limit_up = profile.get("limit_up_count")
        profile_limit_down = profile.get("limit_down_count")
        if profile_limit_up is not None:
            limit_up = dict(limit_up)
            limit_up.setdefault("today", {})["num"] = profile_limit_up
        if profile_limit_down is not None:
            limit_down = dict(limit_down)
            limit_down.setdefault("today", {})["num"] = profile_limit_down

    # The persistent native stream is newer than the legacy market cache and
    # is an authoritative fallback when the atomic profile request is absent.
    breadth = (latest_breadth or {}).get("data") or {}
    # hqMarketZdt is a page-state JSBridge call. Behind the multi-App gateway
    # different replicas can return different page sessions (observed 140:1
    # versus the canonical stream's 74:4). The single stateful push stream is
    # authoritative for limit counts even when an atomic profile exists.
    if breadth:
        if breadth.get("limit_up_count") is not None:
            limit_up = dict(limit_up)
            limit_up.setdefault("today", {})["num"] = breadth["limit_up_count"]
        if breadth.get("limit_down_count") is not None:
            limit_down = dict(limit_down)
            limit_down.setdefault("today", {})["num"] = breadth["limit_down_count"]
    if yesterday_limit.get("change_rate") is not None:
        limit_up = dict(limit_up)
        limit_up["yesterday"] = {
            "rate": yesterday_limit["change_rate"] / 100,
            "leader_name": yesterday_limit.get("leader_name"),
            "leader_change_rate": yesterday_limit.get("leader_change_rate"),
        }
    cap_comparison = profile.get("cap_comparison") or overview.get("cap_comparison")
    if cap_comparison:
        large_rate = (cap_comparison.get("largeCap") or {}).get("changeRate")
        small_rate = (cap_comparison.get("smallCap") or {}).get("changeRate")
        if large_rate is not None and small_rate is not None:
            cap_comparison = dict(cap_comparison)
            cap_comparison["diff"] = round(float(small_rate) - float(large_rate), 2)
            cap_comparison["stronger"] = (
                "小盘股更强" if small_rate > large_rate
                else "大盘股更强" if large_rate > small_rate
                else "大小盘持平"
            )
    if not profile and index_quotes:
        by_native_code = {
            str((row.get("data") or {}).get("native_code")): row.get("data") or {}
            for row in index_quotes
        }
        large = by_native_code.get("1B0300")
        small = by_native_code.get("1B0852")
        if large and small:
            large_rate = large.get("change_percent")
            small_rate = small.get("change_percent")
            if large_rate is not None and small_rate is not None:
                cap_comparison = {
                    "largeCap": {"name": large.get("name"), "code": "1B0300", "changeRate": large_rate},
                    "smallCap": {"name": small.get("name"), "code": "1B0852", "changeRate": small_rate},
                    "diff": round(float(small_rate) - float(large_rate), 2),
                    "stronger": "小盘股更强" if small_rate > large_rate else "大盘股更强" if large_rate > small_rate else "大小盘持平",
                }
    return {
        "market_status": limit_pool.get("trade_status"),
        "market_stats": overview.get("market_stats"),
        "capital_flow": overview.get("capital_flow"),
        "cap_comparison": cap_comparison,
        "limit_up": limit_up,
        "limit_down": limit_down,
        "limit_stocks": limit_pool.get("info") or [],
        "environment": environment,
        "hot_stocks": (
            hot_stocks_row.get("data")
            if isinstance(hot_stocks_row.get("data"), list)
            else []
        ),
        "updated_at": max(
            (
                value
                for value in (
                    overview_row.get("created_at"),
                    environment_row.get("created_at"),
                    limit_pool_row.get("created_at"),
                    hot_stocks_row.get("created_at"),
                    (latest_profile or {}).get("fetched_at"),
                )
                if value is not None
            ),
            default=None,
            key=_timestamp_sort_key,
        ),
    }


def _unwrap_collector_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("data")
    return inner if isinstance(inner, dict) else payload


def _timestamp_sort_key(value: Any) -> float:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return float("-inf")
    if not isinstance(value, datetime):
        return float("-inf")
    normalized = (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )
    return normalized.timestamp()


def _current_collection_sources(
    rows: list[dict[str, Any]],
    *,
    active_watchlist_codes: set[str],
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if (
            row.get("task_name") != "collect_watchlist_instruments"
            or str(row.get("source_name") or "") in active_watchlist_codes
        )
    ]


def _group_by_data_type(
    rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("data_type") or "unknown"), []).append(
            row
        )
    return grouped


def _prefer_current_index_hot_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    has_current_index_source = any(
        (row.get("data") or {}).get("sector_type") == "index"
        and row.get("provider") == "ths_app_http"
        for row in rows
    )
    if not has_current_index_source:
        return rows
    return [
        row
        for row in rows
        if (row.get("data") or {}).get("sector_type") != "index"
        or row.get("provider") == "ths_app_http"
    ]


def _prefer_latest_hot_sector_batches(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep one coherent collection batch for each hot-board classification."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sector_type = str((row.get("data") or {}).get("sector_type") or "unknown")
        grouped.setdefault(sector_type, []).append(row)

    result: list[dict[str, Any]] = []
    for items in grouped.values():
        latest = max(
            (_timestamp_sort_key(item.get("fetched_at")) for item in items),
            default=float("-inf"),
        )
        batch = [
            item
            for item in items
            if _timestamp_sort_key(item.get("fetched_at")) == latest
        ]
        seen: set[str] = set()
        for item in batch:
            code = str((item.get("data") or {}).get("provider_sector_code") or "")
            if code and code in seen:
                continue
            if code:
                seen.add(code)
            result.append(item)
    return result


def _prefer_latest_sector_ranking_batches(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Discard superseded ranking batches independently per type and metric."""

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        data = row.get("data") or {}
        key = (
            str(data.get("sector_type") or "unknown"),
            str(data.get("metric") or "unknown"),
        )
        grouped.setdefault(key, []).append(row)
    result: list[dict[str, Any]] = []
    for items in grouped.values():
        latest = max(
            (_timestamp_sort_key(item.get("fetched_at")) for item in items),
            default=float("-inf"),
        )
        result.extend(
            item
            for item in items
            if _timestamp_sort_key(item.get("fetched_at")) == latest
        )
    return result


def _prefer_typed_commodity_linkage_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    valid_types = {"futures", "spot", "industry"}
    has_typed_rows = any(
        (row.get("data") or {}).get("linkage_type") in valid_types
        for row in rows
    )
    if not has_typed_rows:
        return rows
    typed_rows = [
        row
        for row in rows
        if (row.get("data") or {}).get("linkage_type") in valid_types
    ]
    if any(
        (row.get("data") or {}).get("identity_version") == 2
        for row in typed_rows
    ):
        return [
            row
            for row in typed_rows
            if (row.get("data") or {}).get("identity_version") == 2
        ]
    return typed_rows


def _latest_row(
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not rows:
        return None
    return _compact_snapshot(
        max(
            rows,
            key=lambda item: item.get("bucket_at")
            or datetime.min.replace(tzinfo=timezone.utc),
        )
    )


def _previous_same_time_comparison(
    latest: dict[str, Any] | None,
    history: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not latest:
        return None
    current_trade_date = latest.get("trade_date")
    current_bucket = latest.get("bucket_at")
    current_turnover = _number(latest.get("data"), "turnover")
    provider_comparison = (
        (latest.get("data") or {}).get("previous_same_time_turnover")
    )
    if (
        isinstance(provider_comparison, dict)
        and current_turnover is not None
    ):
        previous_turnover = _number(
            provider_comparison,
            "previous_turnover",
        )
        if previous_turnover is not None:
            change = current_turnover - previous_turnover
            return {
                "trade_date": provider_comparison.get(
                    "previous_trade_date"
                ),
                "comparison_time": provider_comparison.get(
                    "comparison_time"
                ),
                "turnover": previous_turnover,
                "turnover_change": change,
                "turnover_change_percent": (
                    change / previous_turnover * 100
                    if previous_turnover
                    else None
                ),
                "source": "provider_intraday",
            }
    if (
        current_trade_date is None
        or not isinstance(current_bucket, datetime)
        or current_turnover is None
    ):
        return None

    previous_dates = {
        row.get("trade_date")
        for row in history
        if row.get("trade_date") is not None
        and row.get("trade_date") < current_trade_date
    }
    if not previous_dates:
        return None
    previous_trade_date = max(previous_dates)
    previous_rows = [
        row
        for row in history
        if row.get("trade_date") == previous_trade_date
        and isinstance(row.get("bucket_at"), datetime)
        and _number(row.get("data"), "turnover") is not None
    ]
    if not previous_rows:
        return None

    current_local = current_bucket.astimezone(CHINA_TIMEZONE)
    target_local = datetime.combine(
        previous_trade_date,
        current_local.time().replace(tzinfo=None),
        tzinfo=CHINA_TIMEZONE,
    )
    target_utc = target_local.astimezone(timezone.utc)
    previous = min(
        previous_rows,
        key=lambda row: abs(
            (row["bucket_at"].astimezone(timezone.utc) - target_utc)
            .total_seconds()
        ),
    )
    difference_seconds = abs(
        (
            previous["bucket_at"].astimezone(timezone.utc)
            - target_utc
        ).total_seconds()
    )
    if difference_seconds > 300:
        return None

    previous_turnover = _number(previous.get("data"), "turnover")
    if previous_turnover is None:
        return None
    change = current_turnover - previous_turnover
    change_percent = (
        change / previous_turnover * 100
        if previous_turnover
        else None
    )
    return {
        "trade_date": previous_trade_date,
        "bucket_at": previous.get("bucket_at"),
        "turnover": previous_turnover,
        "turnover_change": change,
        "turnover_change_percent": change_percent,
        "matched_time_difference_seconds": difference_seconds,
        "source": "local_snapshot",
    }


def _sector_rows(
    rows: list[dict[str, Any]],
    *,
    value_key: str,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        data = row.get("data") or {}
        result.append(
            {
                "subject_id": row.get("subject_id"),
                "provider": row.get("provider"),
                "sector_name": (
                    data.get("sector_name")
                    or data.get("name")
                    or row.get("subject_id")
                ),
                "sector_type": data.get("sector_type"),
                "change_pct": _number(data, "change_pct"),
                "main_net_inflow": _number(data, "main_net_inflow"),
                "turnover": _number(data, "turnover"),
                "up_count": _number(data, "up_count"),
                "down_count": _number(data, "down_count"),
                "rank": _number(data, "rank"),
                "lead_stock_name": data.get("lead_stock_name"),
                "freshness_status": row.get("freshness_status"),
                "bucket_at": row.get("bucket_at"),
                "value": _number(data, value_key),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["value"] is not None,
            item["value"] or 0,
        ),
        reverse=True,
    )


def _sector_snapshot_rows(
    rows: list[dict[str, Any]],
    *,
    sort_key: str | None = None,
    descending: bool = False,
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        data = dict(row.get("data") or {})
        result.append(
            {
                **data,
                "id": row.get("id"),
                "subject_id": row.get("subject_id"),
                "data_type": row.get("data_type"),
                "provider": row.get("provider"),
                "trade_date": row.get("trade_date"),
                "observed_at": row.get("observed_at"),
                "fetched_at": row.get("fetched_at"),
                "bucket_at": row.get("bucket_at"),
                "freshness_status": row.get("freshness_status"),
                "payload_hash": row.get("payload_hash"),
            }
        )
    if sort_key:
        result.sort(
            key=lambda item: (
                _number(item, sort_key) is None,
                _number(item, sort_key) or 0,
            ),
            reverse=descending,
        )
    return result


def _group_sector_snapshots(
    rows: list[dict[str, Any]],
    *,
    group_key: str,
    sort_key: str,
    descending: bool,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in _sector_snapshot_rows(rows):
        group = str(item.get(group_key) or "unknown")
        grouped.setdefault(group, []).append(item)
    for items in grouped.values():
        items.sort(
            key=lambda item: (
                _number(item, sort_key) is None,
                _number(item, sort_key) or 0,
            ),
            reverse=descending,
        )
        del items[limit:]
    return grouped


def _group_sector_snapshots_nested(
    rows: list[dict[str, Any]],
    *,
    outer_key: str,
    inner_key: str,
    sort_key: str,
    descending: bool,
    limit: int,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for item in _sector_snapshot_rows(rows):
        outer = str(item.get(outer_key) or "unknown")
        inner = str(item.get(inner_key) or "unknown")
        grouped.setdefault(outer, {}).setdefault(inner, []).append(item)
    for inner_groups in grouped.values():
        for items in inner_groups.values():
            items.sort(
                key=lambda item: (
                    _number(item, sort_key) is None,
                    _number(item, sort_key) or 0,
                ),
                reverse=descending,
            )
            del items[limit:]
    return grouped


def _group_sector_flow_extremes(
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in _sector_snapshot_rows(rows):
        group = str(item.get("sector_type") or "unknown")
        grouped.setdefault(group, []).append(item)
    inflow_limit = (limit + 1) // 2
    outflow_limit = limit // 2
    for group, items in grouped.items():
        inflows = sorted(
            (
                item
                for item in items
                if _number(item, "main_net_inflow") is not None
                and (_number(item, "main_net_inflow") or 0) >= 0
            ),
            key=lambda item: _number(item, "main_net_inflow") or 0,
            reverse=True,
        )[:inflow_limit]
        outflows = sorted(
            (
                item
                for item in items
                if _number(item, "main_net_inflow") is not None
                and (_number(item, "main_net_inflow") or 0) < 0
            ),
            key=lambda item: _number(item, "main_net_inflow") or 0,
        )[:outflow_limit]
        outflows.sort(
            key=lambda item: _number(item, "main_net_inflow") or 0,
            reverse=True,
        )
        grouped[group] = [*inflows, *outflows]
    return grouped


def _group_sector_rotation_periods(
    rows: list[dict[str, Any]],
    *,
    day_limit: int,
    rank_limit: int,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {}
    for item in _sector_snapshot_rows(rows):
        sector_type = str(item.get("sector_type") or "unknown")
        metric = str(item.get("metric") or "unknown")
        source_date = str(item.get("source_date") or item.get("trade_date") or "")
        if not source_date:
            continue
        grouped.setdefault(sector_type, {}).setdefault(metric, {}).setdefault(
            source_date,
            [],
        ).append(item)
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for sector_type, metrics in grouped.items():
        result[sector_type] = {}
        for metric, periods in metrics.items():
            output_periods = []
            for source_date in sorted(periods, reverse=True)[:day_limit]:
                items = periods[source_date]
                latest_batch = max(
                    (_timestamp_sort_key(item.get("fetched_at")) for item in items),
                    default=float("-inf"),
                )
                items = [
                    item
                    for item in items
                    if _timestamp_sort_key(item.get("fetched_at"))
                    == latest_batch
                ]
                items.sort(
                    key=lambda item: _number(item, "rank") or 10_000
                )
                output_periods.append(
                    {
                        "source_date": source_date,
                        "items": items[:rank_limit],
                    }
                )
            result[sector_type][metric] = output_periods
    return result


def _sector_rank_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    rank = _number(item, "rank")
    if rank is None:
        rank = _number(item, "heat_rank")
    if rank is not None:
        return (0, rank)
    value = _number(item, "metric_value")
    if value is None:
        value = _number(item, "main_net_inflow")
    if value is None:
        value = _number(item, "heat_score")
    return (1, -(value or 0))


def _sector_freshness(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    latest = None
    for row in rows:
        status = str(row.get("freshness_status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
        timestamp = row.get("bucket_at")
        if isinstance(timestamp, datetime) and (
            latest is None or timestamp > latest
        ):
            latest = timestamp
    return {"by_status": counts, "latest_bucket_at": latest}


def _watchlist_row(
    item: Any,
    snapshots: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    latest = [
        _compact_snapshot(row)
        for (code, _data_type), row in snapshots.items()
        if code == item.code
    ]
    latest.sort(
        key=lambda row: row.get("bucket_at")
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    config = item.config or {}
    return {
        "code": item.code,
        "name": item.name,
        "type": item.type,
        "enabled": item.enabled,
        "priority": config.get("priority", "standard"),
        "realtime_interval_seconds": config.get(
            "realtime_interval_seconds",
            60,
        ),
        "last_success_at": item.last_success_at,
        "last_error": item.last_error,
        "latest": latest,
    }


def _compact_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "data_type": row.get("data_type"),
        "subject_type": row.get("subject_type"),
        "subject_id": row.get("subject_id"),
        "market": row.get("market"),
        "provider": row.get("provider"),
        "trade_date": row.get("trade_date"),
        "observed_at": row.get("observed_at"),
        "fetched_at": row.get("fetched_at"),
        "bucket_at": row.get("bucket_at"),
        "freshness_status": row.get("freshness_status"),
        "source_latency_seconds": row.get("source_latency_seconds"),
        "payload_hash": row.get("payload_hash"),
        "data": row.get("data") or {},
    }


def _merge_breadth_with_ths_indices(
    breadth: dict[str, Any] | None,
    quote_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]] | None = None,
    native_breadth_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Compose the dashboard exclusively from THS native push snapshots."""
    if not breadth or (not quote_rows and not summary_rows and not native_breadth_rows):
        return breadth
    breadth_observed = breadth.get("observed_at") or breadth.get("fetched_at")
    if not isinstance(breadth_observed, datetime):
        return breadth
    if breadth_observed.tzinfo is None:
        breadth_observed = breadth_observed.replace(tzinfo=timezone.utc)
    minimum_quote_time = breadth_observed - timedelta(seconds=90)
    breadth_trade_date = breadth.get("trade_date")
    market_close = datetime.min.time().replace(hour=15)
    breadth_is_closed = (
        breadth_observed.astimezone(CHINA_TIMEZONE).time() >= market_close
    )

    def compatible_quote_time(row: dict[str, Any], observed_at: datetime) -> bool:
        if observed_at >= minimum_quote_time:
            return True
        # Compatibility for snapshots written before breadth receive times were
        # clamped at 15:00.  Once the market is closed, same-trade-date closing
        # quotes and breadth are one coherent frame even if the App repeats the
        # breadth payload later.
        return bool(
            breadth_is_closed
            and breadth_trade_date
            and row.get("trade_date") == breadth_trade_date
            and observed_at.astimezone(CHINA_TIMEZONE).time() >= market_close
        )
    push_by_code: dict[str, tuple[datetime, dict[str, Any]]] = {}
    for row in quote_rows:
        data = row.get("data")
        observed_at = row.get("observed_at") or row.get("fetched_at")
        if not isinstance(data, dict) or not isinstance(observed_at, datetime):
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        code = str(data.get("code") or "")
        if not code or not compatible_quote_time(row, observed_at):
            continue
        current = push_by_code.get(code)
        if current is None or observed_at > current[0]:
            push_by_code[code] = (observed_at, data)
    summary: tuple[datetime, dict[str, Any]] | None = None
    for row in summary_rows or []:
        data = row.get("data")
        observed_at = row.get("observed_at") or row.get("fetched_at")
        if not isinstance(data, dict) or not isinstance(observed_at, datetime):
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if not compatible_quote_time(row, observed_at) or data.get("turnover") is None:
            continue
        if summary is None or observed_at > summary[0]:
            summary = (observed_at, data)
    native_breadth: tuple[datetime, dict[str, Any]] | None = None
    for row in native_breadth_rows or []:
        data = row.get("data")
        observed_at = row.get("observed_at") or row.get("fetched_at")
        if not isinstance(data, dict) or not isinstance(observed_at, datetime):
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if observed_at < minimum_quote_time:
            continue
        if native_breadth is None or observed_at > native_breadth[0]:
            native_breadth = (observed_at, data)
    if not push_by_code and summary is None and native_breadth is None:
        return breadth

    merged = dict(breadth)
    merged_data = dict(breadth.get("data") or {})
    indices = []
    seen_codes: set[str] = set()
    used_times: list[datetime] = []
    for original in merged_data.get("indices") or []:
        item = dict(original)
        original_code = str(item.get("code") or "")
        pushed = push_by_code.get(original_code)
        if pushed is not None:
            pushed_at, pushed_data = pushed
            item.update({
                "name": pushed_data.get("name") or item.get("name"),
                "close": pushed_data.get("close"),
                "change_percent": pushed_data.get("change_percent"),
                "speed": pushed_data.get("speed"),
                "source_time": pushed_data.get("source_time"),
                "provider": "ths_native_stream",
            })
            used_times.append(pushed_at)
            seen_codes.add(original_code)
        indices.append(item)
    for code, (pushed_at, pushed_data) in push_by_code.items():
        if code in seen_codes:
            continue
        indices.append({
            "code": code,
            "name": pushed_data.get("name"),
            "close": pushed_data.get("close"),
            "change_percent": pushed_data.get("change_percent"),
            "speed": pushed_data.get("speed"),
            "source_time": pushed_data.get("source_time"),
            "provider": "ths_native_stream",
        })
        used_times.append(pushed_at)
    merged_data["indices"] = indices
    if push_by_code:
        merged_data["index_quote_provider"] = "ths_native_stream"
    if summary is not None:
        summary_at, summary_data = summary
        merged_data["turnover"] = summary_data["turnover"]
        merged_data["turnover_unit"] = "yuan"
        merged_data["turnover_provider"] = "ths_native_stream"
        used_times.append(summary_at)
    if native_breadth is not None:
        native_at, native_data = native_breadth
        for field in ("up_count", "down_count", "flat_count"):
            if native_data.get(field) is not None:
                merged_data[field] = native_data[field]
        merged_data["breadth_provider"] = "ths_native_stream"
        used_times.append(native_at)
    if not used_times:
        return breadth
    merged["data"] = merged_data
    merged["provider"] = "ths_native_stream"
    merged["observed_at"] = max([breadth_observed, *used_times])
    merged["freshness_status"] = "realtime"
    return merged


def _cn_intraday_display_status(
    row: dict[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, str] | None:
    """Describe expected CN-market pauses without hiding intraday delays."""
    if not row:
        return None
    local_now = now.astimezone(CHINA_TIMEZONE)
    trade_date = row.get("trade_date")
    if isinstance(trade_date, str):
        try:
            trade_date = datetime.fromisoformat(trade_date).date()
        except ValueError:
            return None
    if trade_date != local_now.date():
        return None

    observed_at = row.get("observed_at") or row.get("bucket_at")
    if isinstance(observed_at, str):
        try:
            observed_at = datetime.fromisoformat(
                observed_at.replace("Z", "+00:00")
            )
        except ValueError:
            return None
    if not isinstance(observed_at, datetime):
        return None
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    local_observed = observed_at.astimezone(CHINA_TIMEZONE)
    now_minutes = local_now.hour * 60 + local_now.minute
    observed_minutes = local_observed.hour * 60 + local_observed.minute

    if now_minutes >= 15 * 60 and observed_minutes >= 15 * 60:
        return {"status": "closed", "label": "已收盘"}
    if (
        11 * 60 + 30 <= now_minutes < 13 * 60
        and observed_minutes >= 11 * 60 + 30
    ):
        return {"status": "lunch_break", "label": "午间休市"}
    return None


def _daily_bucket_matches_trade_date(row: dict[str, Any]) -> bool:
    """Reject legacy daily snapshots whose UTC floor changed the CN date."""

    trade_date = row.get("trade_date")
    if isinstance(trade_date, str):
        try:
            trade_date = date.fromisoformat(trade_date[:10])
        except ValueError:
            return False
    bucket_at = row.get("bucket_at")
    if isinstance(bucket_at, str):
        try:
            bucket_at = datetime.fromisoformat(bucket_at.replace("Z", "+00:00"))
        except ValueError:
            return False
    if not isinstance(trade_date, date) or not isinstance(bucket_at, datetime):
        return False
    if bucket_at.tzinfo is None:
        bucket_at = bucket_at.replace(tzinfo=timezone.utc)
    return bucket_at.astimezone(CHINA_TIMEZONE).date() == trade_date


def _number(data: Any, key: str) -> float | int | None:
    if not isinstance(data, dict):
        return None
    value = data.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _index_value(
    data: Any,
    *,
    code: str,
    key: str,
) -> float | int | None:
    if not isinstance(data, dict):
        return None
    for item in data.get("indices") or []:
        if str(item.get("code") or "") == code:
            return _number(item, key)
    return None
