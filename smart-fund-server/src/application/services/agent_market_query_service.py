"""Bounded, cutoff-aware market read models for financial Agents.

This service is the application boundary behind market MCP tools.  It reads
repositories directly and deliberately does not reuse the WebUI dashboard
payload, whose breadth and mixed presentation concerns are unsuitable for an
Agent context window.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from src.infrastructure.persistence.repositories import (
    CollectionObservabilityRepository,
    MarketSnapshotRepository,
)
from src.application.services.china_exchange_calendar_service import (
    CHINA_TIMEZONE,
    ChinaExchangeCalendarService,
)
from src.application.services.market_evidence_locator import (
    MarketEvidenceIdentity,
    decode_market_evidence_locator,
    encode_market_evidence_locator,
    with_evidence_field,
)


# Every observed snapshot type belongs to one research dimension. Unknown
# future types remain visible in ``other_market`` until deliberately mapped.
MARKET_DIMENSIONS: dict[str, frozenset[str]] = {
    "a_share_market": frozenset(
        {
            "market_breadth",
            "ths_cn_index_quote",
            "ths_cn_market_summary",
            "ths_cn_market_breadth",
            "ths_index_daily",
            "ths_market_profile",
            "call_auction",
            "market_anomaly",
        }
    ),
    "stock_activity": frozenset(
        {
            "stock_ranking",
            "stock_dynamic_group",
            "stock_change",
            "ths_stock_anomaly",
            "ths_large_order",
            "stock_flow",
        }
    ),
    "sector_style": frozenset(
        {
            "sector_quote",
            "sector_flow",
            "ths_sector_hot",
            "ths_sector_ranking",
            "ths_sector_flow",
            "ths_sector_rotation",
            "ths_industry_opportunity",
            "ths_sector_prosperity",
            "ths_sector_commodity_linkage",
            "ths_sector_constituents",
            "ths_sector_anomaly",
            "ths_sector_daily",
            "sector_reference",
            "sector_constituents",
        }
    ),
    "flow_liquidity": frozenset(
        {
            "market_capital",
            "northbound_capital",
            "northbound_capital_current",
            "northbound_turnover",
            "reverse_repo",
        }
    ),
    "sentiment": frozenset(
        {
            "market_sentiment",
            "index_sentiment",
        }
    ),
    "valuation_rates_bonds": frozenset(
        {
            "interest_rate",
            "government_bond_yield",
            "market_pe",
            "market_pb",
            "market_valuation_threshold",
            "bond_index",
            "bond_market_price",
        }
    ),
    "etf_fund": frozenset(
        {
            "etf_daily_quote",
            "etf_reference",
            "etf_estimated_net_inflow",
            "ths_etf_hot_ranking",
            "ths_etf_home_ranking",
            "ths_etf_ranking_universe",
            "ths_etf_zone",
            "ths_etf_cross_border",
            "ths_us_etf_catalog",
        }
    ),
    "futures_commodities": frozenset(
        {
            "futures_quote",
            "futures_intraday",
            "ths_futures_module",
            "ths_futures_zone",
            "commodity_daily",
            "intl_futures",
        }
    ),
    "gold": frozenset(
        {
            "ths_gold_module",
            "ths_gold_zone",
        }
    ),
    "global_us_hk_fx": frozenset(
        {
            "forex_quote",
            "forex_intraday",
            "forex",
            "global_index",
            "index_quote",
            "benchmark_daily",
            "ths_us_security_quote",
            "ths_us_market_module",
            "ths_us_sector_period",
            "ths_us_market_zone",
            "hk_quote",
            "us_quote",
            "hk_us_kline",
        }
    ),
    "instrument_tracking": frozenset(
        {
            "quote",
            "realtime",
            "minute_data",
            "kline",
        }
    ),
}

RESEARCH_TOPICS: dict[str, dict[str, tuple[str, ...]]] = {
    "a_share": {"dimensions": ("a_share_market",), "domains": ("market_cache",)},
    "stock": {"dimensions": ("stock_activity", "instrument_tracking"), "domains": ("instrument_profile", "instrument_observation")},
    "sector": {"dimensions": ("sector_style",), "domains": ("market_flow",)},
    "etf": {"dimensions": ("etf_fund",), "domains": ("etf_daily_share", "instrument_profile", "instrument_disclosure", "instrument_observation")},
    "futures_commodities": {"dimensions": ("futures_commodities",), "domains": ()},
    "gold": {"dimensions": ("gold",), "domains": ("news",)},
    "global_us_hk_fx": {"dimensions": ("global_us_hk_fx",), "domains": ()},
    "fund": {"dimensions": (), "domains": ("instrument_profile", "instrument_disclosure", "instrument_observation")},
    "flow_liquidity": {"dimensions": ("flow_liquidity",), "domains": ("market_flow",)},
    "sentiment": {"dimensions": ("sentiment",), "domains": ("sentiment", "sentiment_signal")},
    "macro_valuation": {"dimensions": ("valuation_rates_bonds",), "domains": ("macro_indicator", "macro_regime")},
    "news_research": {"dimensions": (), "domains": ("news",)},
    "data_health": {"dimensions": (), "domains": ("collection_state", "collection_run")},
}

RESEARCH_TOPIC_GUIDANCE: dict[str, dict[str, Any]] = {
    "a_share": {
        "title": "A股全市场",
        "questions": ("指数方向", "市场宽度", "涨跌停", "集合竞价", "异常事件"),
        "specialized_tools": ("market_change_brief_open", "market_dimension_open"),
    },
    "stock": {
        "title": "股票活动与个股",
        "questions": ("异动排行", "大单资金", "个股画像", "实时行情与历史"),
        "specialized_tools": ("market_instrument_realtime_open", "market_instrument_history"),
    },
    "sector": {
        "title": "行业、概念与风格",
        "questions": ("涨幅与热度", "对象对齐资金", "轮动", "景气", "成分重叠", "多日趋势"),
        "specialized_tools": (
            "market_sector_overview",
            "market_sector_rankings",
            "market_sector_compare_open",
            "market_sector_open",
        ),
    },
    "etf": {
        "title": "ETF与基金表达",
        "questions": ("ETF排名", "份额", "估值", "业绩", "持仓", "管理人", "表达重叠"),
        "specialized_tools": (
            "market_expression_compare_open",
            "market_instrument_realtime_open",
            "market_domain_open",
        ),
    },
    "futures_commodities": {
        "title": "期货与商品",
        "questions": ("国内外期货", "商品日线", "跨市场风险先验"),
        "specialized_tools": ("market_topic_open", "market_instrument_history"),
    },
    "gold": {
        "title": "黄金",
        "questions": ("金价", "黄金产品", "黄金资讯与驱动"),
        "specialized_tools": ("market_topic_open", "market_evidence_open"),
    },
    "global_us_hk_fx": {
        "title": "全球市场、美股、港股与外汇",
        "questions": ("全球指数", "美股宽度", "美股行业概念", "港股", "汇率"),
        "specialized_tools": ("market_global_overview_open", "market_instrument_history"),
    },
    "fund": {
        "title": "基金基本面与披露",
        "questions": ("基金经理", "规模", "持仓", "资产配置", "净值", "回撤"),
        "specialized_tools": ("market_domain_open", "market_instrument_realtime_open"),
    },
    "flow_liquidity": {
        "title": "资金与流动性",
        "questions": ("全市场资金", "北向成交额", "逆回购", "龙虎榜", "板块资金"),
        "specialized_tools": ("market_dimension_open", "market_domain_open"),
    },
    "sentiment": {
        "title": "市场情绪",
        "questions": ("市场温度", "涨跌停池", "热门股票与话题", "股吧情绪"),
        "specialized_tools": ("market_dimension_open", "market_domain_open"),
    },
    "macro_valuation": {
        "title": "宏观、估值、利率与债券",
        "questions": ("宏观指标", "宏观状态", "估值", "利率", "债券"),
        "specialized_tools": ("market_topic_open", "market_domain_open"),
    },
    "news_research": {
        "title": "新闻与研究材料",
        "questions": ("新闻原文", "研报", "政策与监管材料", "知识图谱"),
        "specialized_tools": ("market_domain_open", "kg_relation_graph_search"),
    },
    "data_health": {
        "title": "数据健康",
        "questions": ("采集检查点", "任务成功率", "数据缺失与延迟"),
        "specialized_tools": ("market_domain_open",),
    },
}

# Navigation reads use this order so high-value semantic families appear before
# implementation-detail feeds.  Exact candidate ranking remains the model's
# responsibility and is handled by specialized tools, not by this ordering.
MARKET_DIMENSION_PRIORITY: dict[str, tuple[str, ...]] = {
    name: tuple(sorted(data_types)) for name, data_types in MARKET_DIMENSIONS.items()
}
MARKET_DIMENSION_PRIORITY.update({
    "a_share_market": (
        "ths_cn_index_quote", "ths_cn_market_breadth", "ths_cn_market_summary",
        "ths_market_profile", "market_breadth", "call_auction",
        "market_anomaly", "ths_index_daily",
    ),
    "sector_style": (
        "ths_sector_ranking", "ths_sector_flow", "ths_sector_hot",
        "ths_sector_rotation", "ths_industry_opportunity",
        "ths_sector_prosperity", "ths_sector_commodity_linkage",
        "ths_sector_anomaly", "sector_quote", "sector_flow",
        "ths_sector_constituents", "sector_constituents", "sector_reference",
        "ths_sector_daily",
    ),
    "global_us_hk_fx": (
        "ths_us_market_module", "global_index", "index_quote", "hk_quote",
        "forex_quote", "forex_intraday", "forex", "benchmark_daily",
        "ths_us_sector_period", "ths_us_market_zone", "us_quote",
        "ths_us_security_quote", "hk_us_kline",
    ),
})

MARKET_CHANGE_FOCUS_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "overall": (
        # “整体市场复核”必须先获得全市场地图。每个维度只返回少量摘要，
        # 具体对象仍由 Agent 按信息价值自主下钻，避免把原始数据一次塞满上下文。
        *MARKET_DIMENSIONS.keys(),
    ),
    "risk_appetite": (
        "a_share_market",
        "sentiment",
        "flow_liquidity",
        "sector_style",
    ),
    "liquidity": (
        "flow_liquidity",
        "a_share_market",
        "etf_fund",
    ),
    "sector_rotation": (
        "sector_style",
        "a_share_market",
        "stock_activity",
    ),
    "data_quality": tuple(MARKET_DIMENSIONS),
}

# Deterministic frame-to-frame comparisons.  Every metric has an explicit
# subject, unit, and materiality threshold; arbitrary provider JSON is never
# compared heuristically.
FRAME_SIGNAL_SPECS = (
    ("a_share_market", "ths_cn_market_breadth", "cn:a_share:ths_breadth", "up_count", "stocks", 5.0),
    ("a_share_market", "ths_cn_market_breadth", "cn:a_share:ths_breadth", "down_count", "stocks", 5.0),
    ("a_share_market", "ths_cn_market_breadth", "cn:a_share:ths_breadth", "limit_up_count", "stocks", 10.0),
    ("a_share_market", "ths_cn_market_breadth", "cn:a_share:ths_breadth", "limit_down_count", "stocks", 10.0),
    ("a_share_market", "ths_cn_index_quote", "cn:index:000001", "close", "index_points", 0.5),
    ("a_share_market", "ths_cn_index_quote", "cn:index:399001", "close", "index_points", 0.5),
    ("a_share_market", "ths_cn_index_quote", "cn:index:399006", "close", "index_points", 0.7),
    ("flow_liquidity", "market_capital", "cn:a_share:market_capital", "net_inflow", "yuan", 10.0),
    ("flow_liquidity", "northbound_turnover", "cn:northbound:turnover:ths", "turnover", "yuan", 10.0),
    ("flow_liquidity", "etf_estimated_net_inflow", "cn:etf:szse:estimated_net_inflow", "net_inflow_yuan", "yuan", 10.0),
    ("sentiment", "market_sentiment", "cn:a_share:ths_temperature", "temperature", "score", 8.0),
    ("valuation_rates_bonds", "market_pe", "cn:market:sh", "pe", "ratio", 1.0),
    ("valuation_rates_bonds", "market_pe", "cn:market:sz", "pe", "ratio", 1.0),
    ("valuation_rates_bonds", "market_pb", "cn:market:sh", "pb", "ratio", 1.0),
    ("valuation_rates_bonds", "market_pb", "cn:market:sz", "pb", "ratio", 1.0),
    ("valuation_rates_bonds", "government_bond_yield", "cn:government_bond:10y", "yield", "percent", 1.0),
    ("valuation_rates_bonds", "interest_rate", "cn:shibor:隔夜", "rate", "percent", 1.0),
)


_A_SHARE_CALENDAR_DIMENSIONS = {
    "a_share_market",
    "stock_activity",
    "sector_style",
    "flow_liquidity",
    "sentiment",
    "valuation_rates_bonds",
    "etf_fund",
    "instrument_tracking",
}


class AgentMarketQueryService:
    """Build onion-style market catalogue, frame, and bounded drilldowns."""

    def __init__(
        self,
        *,
        snapshot_repository: MarketSnapshotRepository | None = None,
        collection_repository: CollectionObservabilityRepository | None = None,
        calendar_service: ChinaExchangeCalendarService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._snapshots = snapshot_repository or MarketSnapshotRepository()
        self._collections = (
            collection_repository or CollectionObservabilityRepository()
        )
        self._calendar = calendar_service or ChinaExchangeCalendarService()
        self._now = now or (lambda: datetime.now(UTC))

    def data_catalog(
        self,
        *,
        cutoff_at: datetime,
        topic: str = "overview",
        domain: str | None = None,
        group_offset: int = 0,
        group_limit: int = 40,
    ) -> dict[str, Any]:
        cutoff = self._validate_cutoff(cutoff_at)
        normalized_topic = str(topic or "overview")
        if normalized_topic != "overview" and normalized_topic not in RESEARCH_TOPICS:
            raise ValueError(f"unknown research topic: {normalized_topic}")
        normalized_domain = str(domain or "").strip() or None
        normalized_group_offset = max(0, int(group_offset))
        normalized_group_limit = max(1, min(int(group_limit), 80))
        inventory = self._collections.inventory(cutoff_at=cutoff)
        if normalized_domain and normalized_domain not in {
            str(item.get("domain")) for item in inventory
        }:
            raise ValueError(f"unknown collection domain: {normalized_domain}")
        domains = []
        latest_values: list[datetime | date] = []
        for row in inventory:
            groups = list(row.get("groups") or [])
            latest_at = row.get("latest_at")
            if isinstance(latest_at, (datetime, date)):
                latest_values.append(latest_at)
            available = bool(row.get("available"))
            all_groups = groups
            if normalized_domain == row.get("domain"):
                groups = all_groups[
                    normalized_group_offset:
                    normalized_group_offset + normalized_group_limit
                ]
            domains.append(
                {
                    "domain": row.get("domain"),
                    "title": row.get("title"),
                    "status": (
                        "available"
                        if available and int(row.get("total") or 0) > 0
                        else "empty" if available else "unavailable"
                    ),
                    "record_count": int(row.get("total") or 0),
                    "latest_at": latest_at,
                    "groups": groups[:40] if normalized_domain != row.get("domain") else groups,
                    "group_count": len(all_groups),
                    "group_offset": (
                        normalized_group_offset
                        if normalized_domain == row.get("domain")
                        else 0
                    ),
                    "groups_truncated": (
                        normalized_group_offset + len(groups) < len(all_groups)
                        if normalized_domain == row.get("domain")
                        else len(all_groups) > 40
                    ),
                    "entry_tool": "market_domain_open",
                    "unavailable_reason": (
                        "storage_not_ready" if not available else None
                    ),
                }
            )

        available_count = sum(
            item["status"] in {"available", "empty"} for item in domains
        )
        status = (
            "available"
            if available_count == len(domains)
            else "partial" if available_count else "unavailable"
        )
        mapped_snapshot_types = set().union(*MARKET_DIMENSIONS.values())
        market_snapshot_domain = next(
            (
                item
                for item in inventory
                if item.get("domain") == "market_snapshot"
            ),
            None,
        )
        observed_snapshot_types = {
            str(item.get("name") or "")
            for item in (market_snapshot_domain or {}).get("groups", [])
        }
        unmapped_snapshot_types = sorted(
            observed_snapshot_types - mapped_snapshot_types
        )
        selected_topic = (
            RESEARCH_TOPICS.get(normalized_topic)
            if normalized_topic != "overview"
            else None
        )
        selected_domains = (
            [item for item in domains if item["domain"] == normalized_domain]
            if normalized_domain
            else [
                item for item in domains
                if selected_topic is None or item["domain"] in selected_topic["domains"]
            ]
        )
        topic_dimensions = list((selected_topic or {}).get("dimensions") or [])
        topic_data_types = {
            dimension: list(MARKET_DIMENSION_PRIORITY[dimension])
            for dimension in topic_dimensions
        }
        return {
            "operation": "research_data_catalog_open",
            "status": status,
            "scope": "domain" if normalized_domain else "topic" if selected_topic else "overview",
            "topic": normalized_topic,
            "domain": normalized_domain,
            "as_of": _latest_time(latest_values),
            "cutoff_at": cutoff,
            "read_path": "database",
            "domain_count": len(domains),
            "available_domain_count": available_count,
            "snapshot_type_mapping": {
                "status": "complete" if not unmapped_snapshot_types else "partial",
                "mapped_type_count": len(
                    observed_snapshot_types & mapped_snapshot_types
                ),
                "unmapped_types": unmapped_snapshot_types,
            },
            "domains": selected_domains,
            "evidence_domains": [
                {
                    "domain": "knowledge_graph",
                    "entry_tool": "kg_relation_graph_search",
                    "proof_tools": ["kg_card_open", "kg_edge_open"],
                },
                {
                    "domain": "external_research",
                    "entry_tool": "external_web_search",
                    "proof_tools": [
                        "external_web_read",
                        "external_content_read",
                    ],
                },
            ],
            "research_topics": [
                {
                    "topic": topic,
                    "title": RESEARCH_TOPIC_GUIDANCE[topic]["title"],
                    "questions": list(RESEARCH_TOPIC_GUIDANCE[topic]["questions"]),
                    "dimensions": list(spec["dimensions"]),
                    "domains": list(spec["domains"]),
                    "specialized_tools": list(
                        RESEARCH_TOPIC_GUIDANCE[topic]["specialized_tools"]
                    ),
                    "entry_tool": "market_topic_open",
                }
                for topic, spec in RESEARCH_TOPICS.items()
            ],
            "topic_detail": (
                {
                    "title": RESEARCH_TOPIC_GUIDANCE[normalized_topic]["title"],
                    "questions": list(RESEARCH_TOPIC_GUIDANCE[normalized_topic]["questions"]),
                    "snapshot_data_types_by_dimension": topic_data_types,
                    "domains": list(selected_topic["domains"]),
                    "specialized_tools": list(
                        RESEARCH_TOPIC_GUIDANCE[normalized_topic]["specialized_tools"]
                    ),
                }
                if selected_topic else None
            ),
            "next_operations": [
                "market_frame_open",
                "market_topic_open",
                "market_domain_open",
            ],
        }

    def market_frame(self, *, cutoff_at: datetime) -> dict[str, Any]:
        cutoff = self._validate_cutoff(cutoff_at)
        session = self._calendar.resolve(cutoff)
        metadata_limit = 50_000
        rows = self._snapshots.list_latest_metadata(
            cutoff_at=cutoff,
            limit=metadata_limit,
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[_dimension_for(str(row.get("data_type") or ""))].append(row)

        dimensions = [
            self._dimension_summary(name, grouped.get(name, []), cutoff=cutoff)
            for name in [*MARKET_DIMENSIONS, "other_market"]
        ]
        nonempty = [item for item in dimensions if item["status"] == "available"]
        latest = _latest_time(
            [item["as_of"] for item in nonempty if item.get("as_of")]
        )
        trade_dates = sorted(
            {
                value
                for item in nonempty
                for value in item.get("trade_dates", [])
                if isinstance(value, date)
            },
            reverse=True,
        )
        quality_issues = []
        if len(trade_dates) > 1:
            quality_issues.append(
                {
                    "issue_code": "mixed_trade_dates",
                    "severity": "warning",
                    "description": "市场维度来自多个交易日，使用前应下钻核对。",
                    "affected_dimensions": [
                        item["dimension"]
                        for item in nonempty
                        if len(item.get("trade_dates", [])) > 1
                        or (
                            item.get("trade_dates")
                            and item["trade_dates"][0] != trade_dates[0]
                        )
                    ],
                }
            )
        if not rows:
            quality_issues.append(
                {
                    "issue_code": "no_market_snapshots",
                    "severity": "critical",
                    "description": "截止时间之前没有可用市场快照。",
                    "affected_dimensions": [item["dimension"] for item in dimensions],
                }
            )
        # 中国期货夜盘在自然日晚间开始，但业务交易日属于下一个交易所
        # 交易日。该日期领先 A 股日历是正常口径，不应把整份研究框架误判
        # 为关键数据故障。
        futures_night_trade_dates = {
            session.next_trade_date
        } if (
            session.market_session == "closed"
            and cutoff.astimezone(CHINA_TIMEZONE).time() >= time(20, 30)
        ) else set()
        unexpected_by_dimension = {
            item["dimension"]: [
                value
                for value in item.get("trade_dates", [])
                if item["dimension"] in _A_SHARE_CALENDAR_DIMENSIONS
                and value > session.trade_date
                and not (
                    item["dimension"] == "futures_commodities"
                    and value in futures_night_trade_dates
                )
            ]
            for item in nonempty
        }
        unexpected_trade_dates = sorted(
            {
                value
                for values in unexpected_by_dimension.values()
                for value in values
            }
        )
        if unexpected_trade_dates:
            quality_issues.append(
                {
                    "issue_code": "trade_date_after_calendar_session",
                    "severity": "critical",
                    "description": (
                        "快照交易日晚于交易所日历认定的最近交易日。"
                    ),
                    "affected_trade_dates": unexpected_trade_dates,
                    "affected_dimensions": [
                        dimension
                        for dimension, values in unexpected_by_dimension.items()
                        if values
                    ],
                }
            )
        if len(rows) >= metadata_limit:
            quality_issues.append(
                {
                    "issue_code": "frame_metadata_truncated",
                    "severity": "warning",
                    "description": "市场框架元数据达到查询上限，覆盖可能不完整。",
                    "affected_dimensions": [
                        item["dimension"] for item in dimensions
                    ],
                }
            )
        significant_changes = self._significant_changes(
            cutoff=cutoff,
            comparison_cutoff=session.comparison_cutoff_at,
        )

        return {
            "operation": "market_frame_open",
            "status": "available" if rows else "unavailable",
            "frame_id": f"market-frame:{cutoff.isoformat()}",
            "as_of": latest,
            "cutoff_at": cutoff,
            "market": "cn",
            "market_session": session.market_session,
            "session_basis": "exchange_calendars:XSHG",
            "is_trading_day": session.is_trading_day,
            "trade_date": session.trade_date,
            "previous_trade_date": session.previous_trade_date,
            "comparison_cutoff_at": session.comparison_cutoff_at,
            "overview": (
                f"{len(nonempty)} 个市场维度有数据，"
                f"覆盖 {len(rows)} 个最新对象快照。"
                if rows
                else "截止时间之前没有可用市场快照。"
            ),
            "dimensions": dimensions,
            "snapshot_identity_count": len(rows),
            "truncated": len(rows) >= metadata_limit,
            "significant_changes": significant_changes,
            "change_detection": {
                "status": "available" if significant_changes else "no_change",
                "baseline_cutoff_at": session.comparison_cutoff_at,
                "method": "explicit_metric_same_session_comparison",
                "compared_metric_count": len(FRAME_SIGNAL_SPECS),
            },
            "quality_issues": quality_issues,
            "read_path": "database",
            "next_operations": [
                "market_dimension_open",
                "market_domain_open",
                "market_sector_overview",
                "market_instrument_open",
            ],
        }

    def market_change_brief(
        self,
        *,
        cutoff_at: datetime,
        focus: str = "overall",
        per_dimension_limit: int = 3,
    ) -> dict[str, Any]:
        """Combine the repeated frame-and-dimension reads into one bounded brief."""

        cutoff = self._validate_cutoff(cutoff_at)
        dimensions = MARKET_CHANGE_FOCUS_DIMENSIONS.get(focus)
        if dimensions is None:
            raise ValueError(f"unknown market change focus: {focus}")
        normalized_limit = max(1, min(int(per_dimension_limit), 5))
        if focus == "overall":
            # 全市场地图每个维度只给一个入口，控制上下文体积；后续由 Agent
            # 对真正重要的维度调用下钻工具。
            normalized_limit = 1
        frame = self.market_frame(cutoff_at=cutoff)
        changed_dimensions = list(
            dict.fromkeys(
                str(item.get("dimension") or "")
                for item in frame["significant_changes"]
                if str(item.get("dimension") or "") in dimensions
            )
        )
        selected_dimensions = list(dict.fromkeys([*changed_dimensions, *dimensions]))
        drilldowns = [
            self.market_dimension(
                dimension=dimension,
                cutoff_at=cutoff,
                limit=normalized_limit,
            )
            for dimension in selected_dimensions
        ]
        critical_issues = [
            issue
            for issue in frame["quality_issues"]
            if issue.get("severity") == "critical"
        ]
        if critical_issues:
            research_state = "data_quality_blocked"
            research_implication = "关键数据不可用，先核实缺口再决定能否形成观点。"
        elif frame["significant_changes"]:
            research_state = "material_change_detected"
            research_implication = "存在超过阈值的变化，优先解释变化并寻找反证。"
        else:
            research_state = "baseline_stable"
            research_implication = (
                "阈值扫描未发现大幅变化，这只描述基线稳定性，不代表没有研究机会、"
                "不能预测或应停止研究。"
            )
        return {
            "operation": "market_change_brief_open",
            "status": frame["status"],
            "focus": focus,
            "frame_id": frame["frame_id"],
            "as_of": frame["as_of"],
            "cutoff_at": cutoff,
            "comparison_cutoff_at": frame["comparison_cutoff_at"],
            "market_session": frame["market_session"],
            "research_state": research_state,
            "research_implication": research_implication,
            "significant_change_count": len(frame["significant_changes"]),
            "significant_changes_truncated": len(frame["significant_changes"]) > 10,
            "significant_changes": frame["significant_changes"][:10],
            "quality_issues": frame["quality_issues"],
            "selected_dimensions": selected_dimensions,
            "dimension_facts": [
                {
                    "dimension": item["dimension"],
                    "status": item["status"],
                    "as_of": item["as_of"],
                    "total": item["total"],
                    "truncated": item["truncated"],
                    # The brief is a navigation layer.  Full previews belong
                    # to market_dimension_open / market_evidence_open and
                    # would otherwise be replayed through every LLM round.
                    "facts": [_brief_fact(fact) for fact in item["facts"]],
                }
                for item in drilldowns
            ],
            "read_path": "database",
            "next_operations": [
                "market_topic_open",
                "market_dimension_open",
                "market_sector_compare_open",
                "market_evidence_open",
            ],
        }

    def market_dimension(
        self,
        *,
        dimension: str,
        cutoff_at: datetime,
        limit: int = 8,
        data_types: list[str] | None = None,
    ) -> dict[str, Any]:
        cutoff = self._validate_cutoff(cutoff_at)
        if dimension not in {*MARKET_DIMENSIONS, "other_market"}:
            raise ValueError(f"unknown market dimension: {dimension}")
        normalized_limit = max(1, min(int(limit), 20))
        if dimension == "other_market":
            metadata = self._snapshots.list_latest_metadata(
                cutoff_at=cutoff,
                limit=20_000,
            )
            data_types = sorted(
                {
                    str(row.get("data_type") or "")
                    for row in metadata
                    if _dimension_for(str(row.get("data_type") or ""))
                    == "other_market"
                }
            )
            requested_types = list(data_types[:normalized_limit])
        else:
            available_types = MARKET_DIMENSION_PRIORITY[dimension]
            requested_types = list(dict.fromkeys(data_types or []))
            unknown_types = sorted(set(requested_types) - set(available_types))
            if unknown_types:
                raise ValueError(
                    f"data types do not belong to {dimension}: {unknown_types}"
                )
            data_types = requested_types or list(available_types)
        result = self._snapshots.list_latest_per_data_type_for_agent(
            data_types=list(data_types),
            cutoff_at=cutoff,
            per_data_type_limit=1,
        )
        selected = list(result.get("items") or [])
        if not requested_types:
            selected = selected[:normalized_limit]
        total = int(result.get("total") or 0)
        selected_data_types = list(dict.fromkeys(
            str(row.get("data_type") or "") for row in selected
        ))
        return {
            "operation": "market_dimension_open",
            "status": "available" if selected else "empty",
            "dimension": dimension,
            "as_of": _latest_time([_fact_time(row) for row in selected]),
            "cutoff_at": cutoff,
            "total": total,
            "limit": normalized_limit,
            "selected_data_types": selected_data_types,
            "counts_by_data_type": result.get("counts_by_data_type") or {},
            "truncated": total > len(selected),
            "facts": [_project_snapshot(row) for row in selected],
            "read_path": "database",
            "next_operations": [
                "market_dimension_open",
                "market_instrument_open",
                "market_instrument_history",
            ],
        }

    def premarket_context(
        self,
        *,
        cutoff_at: datetime,
        limit_per_dimension: int = 2,
    ) -> dict[str, Any]:
        """Return the compact cross-market prior needed before A-share opens."""

        cutoff = self._validate_cutoff(cutoff_at)
        session = self._calendar.resolve(cutoff)
        if session.market_session != "pre_open":
            return {
                "operation": "market_premarket_context_open",
                "status": "not_applicable",
                "market_session": session.market_session,
                "as_of": cutoff,
                "reason": "当前不是盘前；请按实际交易时段研究，避免重复加载跨市场盘前地图。",
                "dimensions": [],
                "next_operations": ["market_change_brief_open"],
            }
        limit = max(1, min(int(limit_per_dimension), 2))
        dimensions = (
            "futures_commodities",
            "gold",
            "etf_fund",
            "valuation_rates_bonds",
            "flow_liquidity",
            "sentiment",
        )
        results = [
            self.market_dimension(
                dimension=dimension,
                cutoff_at=cutoff,
                limit=limit,
            )
            for dimension in dimensions
        ]
        global_market = self.global_market_overview(cutoff_at=cutoff)
        return {
            "operation": "market_premarket_context_open",
            "status": "available",
            "as_of": cutoff,
            "purpose": (
                "建立A股开盘前的跨市场先验；这些数据用于提出条件情景和预测，"
                "不能被表述为已经发生的当日A股行情。"
            ),
            "global_market": global_market,
            "dimensions": [
                {
                    "dimension": result["dimension"],
                    "as_of": result["as_of"],
                    "total": result["total"],
                    "truncated": result["truncated"],
                    "facts": result["facts"],
                }
                for result in results
            ],
            "next_operations": [
                "market_global_overview_open",
                "market_evidence_open",
                "market_topic_open",
                "kg_relation_graph_search",
                "market_instrument_realtime_open",
            ],
        }

    def global_market_overview(
        self,
        *,
        cutoff_at: datetime,
    ) -> dict[str, Any]:
        """Return a semantic, bounded global-market map instead of arbitrary rows.

        The US dashboard is assembled from many snapshots. A generic newest-N
        query can therefore hide the index table behind ETF/ranking modules.
        This read model deliberately selects the US index, breadth and sector
        snapshots and projects their provider-native tables into readable facts.
        """

        cutoff = self._validate_cutoff(cutoff_at)
        us_result = self._snapshots.list_latest_for_agent(
            data_types=["ths_us_market_module"],
            cutoff_at=cutoff,
            limit=100,
        )
        other_types = sorted(
            MARKET_DIMENSIONS["global_us_hk_fx"]
            - {"ths_us_market_module", "ths_us_security_quote"}
        )
        other_result = self._snapshots.list_latest_for_agent(
            data_types=other_types,
            cutoff_at=cutoff,
            limit=20,
        )
        us_rows = list(us_result.get("items") or [])
        other_rows = list(other_result.get("items") or [])
        rows = [*us_rows, *other_rows]
        us_modules = {
            str(row.get("subject_id") or ""): row
            for row in rows
            if row.get("data_type") == "ths_us_market_module"
        }
        indices = _native_table_quote_preview(
            (us_modules.get("indices_stream") or {}).get("data")
        )
        breadth = _us_breadth_preview(
            (us_modules.get("overview") or us_modules.get("breadth") or {}).get(
                "data"
            )
        )
        sectors = {
            kind: _us_sector_preview((us_modules.get(subject) or {}).get("data"))
            for kind, subject in (
                ("industry", "industry_current_stream"),
                ("concept", "concept_current_stream"),
            )
        }
        selected_us_rows = [
            row
            for subject in (
                "indices_stream",
                "overview",
                "breadth",
                "industry_current_stream",
                "concept_current_stream",
            )
            if (row := us_modules.get(subject)) is not None
        ]
        us_locators = {
            subject: _snapshot_locator(row)
            for subject, row in us_modules.items()
            if subject in {
                "indices_stream",
                "overview",
                "breadth",
                "industry_current_stream",
                "concept_current_stream",
            }
        }
        other_global = [_project_snapshot(row) for row in other_rows][:12]
        return {
            "operation": "market_global_overview_open",
            "status": "available" if indices or breadth or other_global else "empty",
            "as_of": _latest_time([_fact_time(row) for row in rows]),
            "us_market": {
                "trade_date": (
                    us_modules.get("indices_stream") or {}
                ).get("trade_date"),
                "indices": (indices or {}).get("quotes", []),
                "indices_evidence_locator": us_locators.get("indices_stream"),
                "breadth": breadth,
                "breadth_evidence_locator": (
                    us_locators.get("overview") or us_locators.get("breadth")
                ),
                "leading_industries": sectors["industry"],
                "leading_industries_evidence_locator": us_locators.get(
                    "industry_current_stream"
                ),
                "leading_concepts": sectors["concept"],
                "leading_concepts_evidence_locator": us_locators.get(
                    "concept_current_stream"
                ),
                "evidence_locators": [
                    locator
                    for row in selected_us_rows
                    if (locator := _snapshot_locator(row)) is not None
                ],
            },
            "other_global_facts": other_global,
            "total_available": (
                int(us_result.get("total") or 0)
                + int(other_result.get("total") or 0)
            ),
            "other_global_truncated": max(
                0,
                int(other_result.get("total") or 0) - len(other_global),
            ) > 0,
            "next_operations": [
                "market_evidence_open",
                "market_instrument_realtime_open",
                "market_instrument_history",
            ],
        }

    def market_topic(
        self,
        *,
        topic: str,
        cutoff_at: datetime,
        limit: int = 8,
    ) -> dict[str, Any]:
        cutoff = self._validate_cutoff(cutoff_at)
        spec = RESEARCH_TOPICS.get(topic)
        if spec is None:
            raise ValueError(f"unknown research topic: {topic}")
        normalized_limit = max(1, min(int(limit), 20))
        dimensions = list(spec["dimensions"])
        data_types = list(dict.fromkeys(
            data_type
            for dimension in dimensions
            for data_type in MARKET_DIMENSION_PRIORITY[dimension]
        ))
        selected_data_types = data_types[:normalized_limit]
        snapshot_result = self._snapshots.list_latest_per_data_type_for_agent(
            data_types=selected_data_types,
            cutoff_at=cutoff,
            per_data_type_limit=1,
        )
        inventory = {
            str(row.get("domain")): row
            for row in self._collections.inventory(cutoff_at=cutoff)
            if str(row.get("domain")) in set(spec["domains"])
        }
        related_domains = []
        for domain in spec["domains"]:
            row = inventory.get(domain, {})
            groups = list(row.get("groups") or [])
            related_domains.append(
                {
                    "domain": domain,
                    "title": row.get("title"),
                    "status": (
                        "available"
                        if row.get("available")
                        and int(row.get("total") or 0) > 0
                        else "empty"
                        if row.get("available")
                        else "unavailable"
                    ),
                    "record_count": int(row.get("total") or 0),
                    "latest_at": row.get("latest_at"),
                    "groups": groups[:30],
                    "groups_truncated": len(groups) > 30,
                    "next_operation": "market_domain_open",
                }
            )
        facts = [
            _project_snapshot(row)
            for row in snapshot_result.get("items") or []
        ]
        total = int(snapshot_result.get("total") or 0)
        available = bool(facts) or any(
            item["status"] == "available" for item in related_domains
        )
        return {
            "operation": "market_topic_open",
            "status": "available" if available else "empty",
            "topic": topic,
            "as_of": _latest_time(
                [
                    value
                    for value in [
                        _latest_time([_fact_time(row) for row in facts]),
                        _latest_time(
                            [
                                item["latest_at"]
                                for item in related_domains
                                if isinstance(
                                    item.get("latest_at"),
                                    (datetime, date),
                                )
                            ]
                        ),
                    ]
                    if isinstance(value, (datetime, date))
                ]
            ),
            "cutoff_at": cutoff,
            "dimensions": dimensions,
            "snapshot_data_types": data_types,
            "selected_snapshot_data_types": selected_data_types,
            "snapshot_total": total,
            "snapshot_facts": facts,
            "snapshot_truncated": (
                len(selected_data_types) < len(data_types) or total > len(facts)
            ),
            "related_domains": related_domains,
            "read_path": "database",
            "next_operations": [
                "market_topic_open",
                "market_domain_open",
                "market_evidence_open",
            ],
        }

    def market_domain(
        self,
        *,
        domain: str,
        cutoff_at: datetime,
        group: str | None = None,
        query: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        cutoff = self._validate_cutoff(cutoff_at)
        normalized_limit = max(1, min(int(limit), 50))
        normalized_offset = max(0, int(offset))
        result = self._collections.list_records(
            domain_key=domain,
            group=group,
            query=query,
            cutoff_at=cutoff,
            limit=normalized_limit,
            offset=normalized_offset,
        )
        items = [
            _project_domain_record(
                domain,
                row,
                identity=self._collections.record_identity(
                    domain_key=domain,
                    record=row,
                ),
            )
            for row in result.get("items") or []
            if isinstance(row, Mapping)
        ]
        available = bool(result.get("available"))
        return {
            "operation": "market_domain_open",
            "status": (
                "available" if items else "empty" if available else "unavailable"
            ),
            "domain": result.get("domain") or domain,
            "title": result.get("title"),
            "as_of": _latest_record_time(items),
            "cutoff_at": cutoff,
            "total": int(result.get("total") or 0),
            "limit": normalized_limit,
            "offset": normalized_offset,
            "items": items,
            "truncated": normalized_offset + len(items) < int(result.get("total") or 0),
            "read_path": "database",
            "unavailable_reason": "storage_not_ready" if not available else None,
            "next_operations": ["market_domain_open"],
        }

    def market_evidence(
        self,
        *,
        locator: str,
        cutoff_at: datetime,
        fields: list[str] | None = None,
        max_chars: int = 12_000,
    ) -> dict[str, Any]:
        cutoff = self._validate_cutoff(cutoff_at)
        identity = decode_market_evidence_locator(locator)
        if identity.kind == "snapshot":
            snapshot_id = identity.identity.get("id")
            if snapshot_id is None:
                raise ValueError("snapshot evidence locator has no id")
            record = self._snapshots.get_by_id_at(
                snapshot_id=int(snapshot_id),
                cutoff_at=cutoff,
            )
        elif identity.kind == "domain":
            record = self._collections.get_record_at(
                domain_key=identity.domain,
                identity=identity.identity,
                cutoff_at=cutoff,
            )
        else:
            raise ValueError(f"unsupported market evidence kind: {identity.kind}")
        if record is None:
            return {
                "operation": "market_evidence_open",
                "status": "not_found",
                "evidence_locator": locator,
                "cutoff_at": cutoff,
            }
        _validate_evidence_identity(identity, record)
        requested_fields = list(dict.fromkeys(fields or []))
        if identity.field:
            if requested_fields and requested_fields != [identity.field]:
                raise ValueError(
                    "field-bound evidence locator cannot open other fields"
                )
            requested_fields = [identity.field]
        if len(requested_fields) > 12:
            raise ValueError("market_evidence_open accepts at most 12 fields")
        normalized_max_chars = max(200, min(int(max_chars), 20_000))
        values = []
        missing_fields = []
        for field in requested_fields:
            found, value = _field_value(record, field)
            if not found and "." not in field:
                # Snapshot previews expose keys from the nested ``data``
                # payload.  Accept those model-facing names directly instead
                # of requiring the hidden persistence path ``data.<field>``.
                found, value = _field_value(record, f"data.{field}")
            if not found:
                missing_fields.append(field)
                continue
            values.append(
                {
                    "field": field,
                    "value": _bounded_evidence_value(
                        value,
                        max_chars=normalized_max_chars,
                    ),
                    "evidence_locator": with_evidence_field(locator, field),
                }
            )
        return {
            "operation": "market_evidence_open",
            "status": "available",
            "evidence_locator": locator,
            "cutoff_at": cutoff,
            "identity": {
                "kind": identity.kind,
                "domain": identity.domain,
                "record_identity": identity.identity,
                "data_type": identity.data_type,
                "subject_id": identity.subject_id,
                "provider": identity.provider,
                "fact_time": identity.fact_time,
                "version": identity.version,
            },
            "record": (
                _compact_evidence_record(record)
                if not requested_fields else None
            ),
            "values": values,
            "missing_fields": missing_fields,
            "read_path": "database",
            "next_operations": ["market_evidence_open"],
        }

    def _validate_cutoff(self, cutoff_at: datetime) -> datetime:
        if cutoff_at.tzinfo is None or cutoff_at.utcoffset() is None:
            raise ValueError("cutoff_at must include a timezone")
        cutoff = cutoff_at.astimezone(UTC)
        if cutoff > self._now().astimezone(UTC) + timedelta(seconds=5):
            raise ValueError("cutoff_at cannot be in the future")
        return cutoff

    def _significant_changes(
        self,
        *,
        cutoff: datetime,
        comparison_cutoff: datetime,
    ) -> list[dict[str, Any]]:
        series = list(
            dict.fromkeys(
                (data_type, subject_id)
                for _dimension, data_type, subject_id, _metric, _unit, _threshold
                in FRAME_SIGNAL_SPECS
            )
        )
        current = self._snapshots.query_latest_series_at(
            series=series,
            cutoff_at=cutoff,
            available_at=cutoff,
        )
        baseline = self._snapshots.query_latest_series_at(
            series=series,
            cutoff_at=comparison_cutoff,
            available_at=cutoff,
        )
        # Some cumulative intraday series stop updating before the frame cutoff
        # (for example a midday capital-flow snapshot viewed at 14:30).  Comparing
        # that row with yesterday's 14:30 value creates a false "same-period"
        # change.  Re-read only those baselines at the current fact's own clock
        # time so both sides represent the same amount of elapsed trading time.
        cutoff_local = cutoff.astimezone(CHINA_TIMEZONE)
        comparison_local = comparison_cutoff.astimezone(CHINA_TIMEZONE)
        for key, current_row in current.items():
            current_fact_local = _fact_time(current_row).astimezone(CHINA_TIMEZONE)
            if current_fact_local.date() != cutoff_local.date():
                continue
            if current_fact_local.time() >= cutoff_local.time():
                continue
            aligned_cutoff = datetime.combine(
                comparison_local.date(),
                current_fact_local.time().replace(tzinfo=None),
                tzinfo=CHINA_TIMEZONE,
            ).astimezone(UTC)
            aligned = self._snapshots.query_latest_series_at(
                series=[key],
                cutoff_at=aligned_cutoff,
                available_at=cutoff,
            )
            if key in aligned:
                baseline[key] = aligned[key]
        changes = []
        for (
            dimension,
            data_type,
            subject_id,
            metric,
            unit,
            threshold_percent,
        ) in FRAME_SIGNAL_SPECS:
            key = (data_type, subject_id)
            current_row = current.get(key)
            baseline_row = baseline.get(key)
            if current_row is None or baseline_row is None:
                continue
            current_value = _number_at(current_row.get("data"), metric)
            baseline_value = _number_at(baseline_row.get("data"), metric)
            if current_value is None or baseline_value is None:
                continue
            absolute_change = current_value - baseline_value
            percent_change = (
                absolute_change / abs(baseline_value) * 100
                if baseline_value != 0
                else None
            )
            material = (
                abs(percent_change) >= threshold_percent
                if percent_change is not None
                else absolute_change != 0
            )
            if not material:
                continue
            score = (
                abs(percent_change)
                if percent_change is not None
                else float("inf")
            )
            changes.append(
                {
                    "dimension": dimension,
                    "data_type": data_type,
                    "subject_id": subject_id,
                    "metric": metric,
                    "unit": unit,
                    "direction": (
                        "up" if absolute_change > 0 else "down"
                    ),
                    "current_value": _display_number(
                        current_value, unit=unit
                    ),
                    "baseline_value": _display_number(
                        baseline_value, unit=unit
                    ),
                    "absolute_change": _display_number(
                        absolute_change, unit=unit
                    ),
                    "percent_change": _display_number(
                        percent_change, unit="percent_change"
                    ),
                    "current_as_of": _fact_time(current_row),
                    "baseline_as_of": _fact_time(baseline_row),
                    "current_evidence_locator": _snapshot_locator(current_row),
                    "baseline_evidence_locator": _snapshot_locator(baseline_row),
                    "_materiality_score": score,
                }
            )
        changes.sort(
            key=lambda item: item["_materiality_score"],
            reverse=True,
        )
        for item in changes:
            item.pop("_materiality_score", None)
        return changes[:20]

    @staticmethod
    def _dimension_summary(
        dimension: str,
        rows: list[dict[str, Any]],
        *,
        cutoff: datetime,
    ) -> dict[str, Any]:
        times = [_fact_time(row) for row in rows]
        trade_dates = sorted(
            {row["trade_date"] for row in rows if isinstance(row.get("trade_date"), date)},
            reverse=True,
        )
        freshness = Counter(str(row.get("freshness_status") or "unknown") for row in rows)
        data_types = Counter(str(row.get("data_type") or "unknown") for row in rows)
        return {
            "dimension": dimension,
            "status": "available" if rows else "empty",
            "as_of": _latest_time(times),
            "cutoff_at": cutoff,
            "subject_count": len(rows),
            "trade_dates": trade_dates[:5],
            "trade_dates_truncated": len(trade_dates) > 5,
            "freshness": dict(sorted(freshness.items())),
            "data_types": [
                {"data_type": name, "subject_count": count}
                for name, count in data_types.most_common(12)
            ],
            "data_types_truncated": len(data_types) > 12,
            "drilldown_handle": f"market-dimension:{dimension}:{cutoff.isoformat()}",
            "next_operation": "market_dimension_open",
        }


def _dimension_for(data_type: str) -> str:
    for dimension, data_types in MARKET_DIMENSIONS.items():
        if data_type in data_types:
            return dimension
    return "other_market"


def _fact_time(row: Mapping[str, Any]) -> datetime:
    for key in ("observed_at", "bucket_at", "fetched_at"):
        value = row.get(key)
        if isinstance(value, datetime):
            return value
    return datetime.min.replace(tzinfo=UTC)


def _latest_time(values: list[datetime | date]) -> datetime | date | None:
    return max(values, key=_time_sort_key, default=None)


def _time_sort_key(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC)
            if value.tzinfo is None
            else value.astimezone(UTC)
        )
    return datetime.combine(value, time.min, tzinfo=UTC)


def _latest_record_time(items: list[dict[str, Any]]) -> datetime | date | None:
    values: list[datetime | date] = []
    for item in items:
        for key in (
            "observed_at",
            "bucket_at",
            "fetched_at",
            "published_at",
            "trade_date",
            "snapshot_date",
            "report_date",
            "updated_at",
            "created_at",
        ):
            value = item.get(key)
            if isinstance(value, (datetime, date)):
                values.append(value)
                break
    return _latest_time(values)


def _project_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    snapshot_id = row.get("id")
    return {
        key: value
        for key, value in {
            "evidence_locator": _snapshot_locator(row),
            "snapshot_id": snapshot_id,
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
            "data_fields": (
                list(row.get("data", {}).keys())[:12]
                if isinstance(row.get("data"), Mapping)
                else []
            ),
            "data_preview": _snapshot_data_preview(row.get("data")),
        }.items()
        if value not in (None, "", [], {})
    }


def _snapshot_locator(row: Mapping[str, Any]) -> str | None:
    snapshot_id = row.get("id")
    if snapshot_id is None:
        return None
    identity: dict[str, Any] = {"id": snapshot_id}
    trade_date = row.get("trade_date")
    if trade_date is not None:
        identity["trade_date"] = str(trade_date)[:10]
    return encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="snapshot",
            domain="market_snapshot",
            identity=identity,
            data_type=str(row.get("data_type") or "") or None,
            subject_id=str(row.get("subject_id") or "") or None,
            provider=str(row.get("provider") or "") or None,
            fact_time=_iso_time(_fact_time(row)),
            version=str(row.get("payload_hash") or "") or None,
        )
    )


def _brief_fact(fact: Mapping[str, Any]) -> dict[str, Any]:
    """Project one small, actionable record handle for a change brief."""

    keys = (
        "evidence_locator",
        "data_type",
        "subject_id",
        "trade_date",
        "observed_at",
        "freshness_status",
        "data_fields",
    )
    return {
        key: fact[key]
        for key in keys
        if key in fact and fact[key] not in (None, "", [], {})
    }


def _number_at(value: Any, path: str) -> float | None:
    current = value
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    if isinstance(current, bool) or not isinstance(current, (int, float)):
        return None
    return float(current)


def _display_number(value: float | None, *, unit: str) -> int | float | None:
    """Remove binary-float noise from model-facing market summaries."""

    if value is None:
        return None
    if unit == "stocks":
        return int(round(value))
    digits = 4 if unit in {"percent", "ratio"} else 2
    rounded = round(value, digits)
    return int(rounded) if rounded.is_integer() else rounded


def _project_domain_record(
    domain: str,
    row: Mapping[str, Any],
    *,
    identity: dict[str, Any],
) -> dict[str, Any]:
    projected = {
        str(key): _compact_value(value)
        for key, value in row.items()
        if str(key) not in {"payload_hash"}
    }
    projected["evidence_locator"] = encode_market_evidence_locator(
        MarketEvidenceIdentity(
            kind="domain",
            domain=domain,
            identity=identity,
            data_type=str(
                row.get("data_type") or row.get("indicator") or ""
            )
            or None,
            subject_id=str(row.get("code") or "") or None,
            provider=str(row.get("provider") or row.get("source") or "")
            or None,
            fact_time=_record_fact_time(row),
            version=str(
                row.get("payload_hash")
                or row.get("fingerprint")
                or row.get("updated_at")
                or ""
            )
            or None,
        )
    )
    return projected


def _validate_evidence_identity(
    identity: MarketEvidenceIdentity,
    record: Mapping[str, Any],
) -> None:
    comparisons = {
        "data_type": (
            identity.data_type,
            str(record.get("data_type") or record.get("indicator") or "")
            or None,
        ),
        "subject_id": (
            identity.subject_id,
            str(record.get("subject_id") or record.get("code") or "")
            or None,
        ),
        "provider": (
            identity.provider,
            str(record.get("provider") or record.get("source") or "")
            or None,
        ),
        "version": (
            identity.version,
            str(
                record.get("payload_hash")
                or record.get("fingerprint")
                or record.get("updated_at")
                or ""
            )
            or None,
        ),
    }
    for name, (expected, actual) in comparisons.items():
        if expected is not None and expected != actual:
            raise ValueError(f"market evidence {name} no longer matches")
    actual_fact_time = _record_fact_time(record)
    if identity.fact_time is not None and identity.fact_time != actual_fact_time:
        raise ValueError("market evidence fact_time no longer matches")


def _field_value(record: Mapping[str, Any], field: str) -> tuple[bool, Any]:
    current: Any = record
    for part in str(field or "").split("."):
        if not part or not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _bounded_evidence_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "…"
    compact = _compact_value(value)
    return compact


def _record_fact_time(row: Mapping[str, Any]) -> str | None:
    for key in (
        "observed_at",
        "bucket_at",
        "published_at",
        "report_date",
        "observation_date",
        "trade_date",
        "snapshot_date",
        "fetched_at",
        "updated_at",
        "created_at",
    ):
        value = row.get(key)
        if isinstance(value, (datetime, date)):
            return _iso_time(value)
    return None


def _iso_time(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=UTC)
            if value.tzinfo is None
            else value.astimezone(UTC)
        ).isoformat()
    return value.isoformat()


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    """Keep provider facts useful while enforcing a deterministic size bound."""

    if depth >= 4:
        if isinstance(value, (Mapping, list, tuple)):
            return "[deeper content omitted]"
        return value
    if isinstance(value, Mapping):
        items = list(value.items())
        result = {
            str(key): _compact_value(item, depth=depth + 1)
            for key, item in items[:30]
        }
        if len(items) > 30:
            result["_truncated_keys"] = len(items) - 30
        return result
    if isinstance(value, (list, tuple)):
        selected = list(value[:12])
        result = [_compact_value(item, depth=depth + 1) for item in selected]
        if len(value) > len(selected):
            result.append({"_truncated_items": len(value) - len(selected)})
        return result
    if isinstance(value, str) and len(value) > 800:
        return value[:800] + "…"
    return value


_NATIVE_QUOTE_FIELDS = {
    "4": "code",
    "55": "name",
    "10": "latest",
    "6": "open",
    "7": "high",
    "66": "previous_close",
    "34818": "change_pct",
    "34821": "change_amount",
}


def _native_table_quote_preview(value: Any) -> dict[str, Any] | None:
    """Turn THS numeric-column tables into bounded, readable quote rows."""

    if not isinstance(value, Mapping):
        return None
    native_table = value.get("native_table")
    if not isinstance(native_table, Mapping):
        return None
    data_dict = native_table.get("dataDict")
    if not isinstance(data_dict, Mapping):
        return None
    columns = {
        output_name: data_dict.get(field_code)
        for field_code, output_name in _NATIVE_QUOTE_FIELDS.items()
        if isinstance(data_dict.get(field_code), (list, tuple))
    }
    row_count = max((len(column) for column in columns.values()), default=0)
    if row_count == 0:
        return None
    rows = []
    for index in range(min(row_count, 12)):
        row = {
            name: column[index]
            for name, column in columns.items()
            if index < len(column)
        }
        if row:
            rows.append(row)
    return {
        "quotes": rows,
        "quote_count": row_count,
        "truncated": row_count > len(rows),
        "available_field_codes": list(data_dict.keys())[:30],
    }


def _us_breadth_preview(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    today = value.get("breadth_today") or value.get("today")
    if not isinstance(today, Mapping):
        return None

    def total(key: str) -> int:
        rows = today.get(key)
        if not isinstance(rows, (list, tuple)):
            return 0
        return sum(
            int(row.get("count") or 0)
            for row in rows
            if isinstance(row, Mapping)
        )

    advancing = total("increase_ranges")
    declining = total("decline_ranges")
    unchanged = int(today.get("zero_range") or 0)
    return {
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "advance_decline_ratio": (
            round(advancing / declining, 2) if declining else None
        ),
    }


def _us_sector_preview(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        return []
    native_table = value.get("native_table")
    data = native_table.get("dataDict") if isinstance(native_table, Mapping) else None
    if not isinstance(data, Mapping):
        return []
    names = data.get("55")
    changes = data.get("34313")
    leaders = data.get("35284")
    leader_changes = data.get("35286")
    if not isinstance(names, (list, tuple)):
        return []
    rows = []
    for index, name in enumerate(names[:5]):
        rows.append(
            {
                key: item
                for key, item in {
                    "name": name,
                    "change_pct": (
                        changes[index]
                        if isinstance(changes, (list, tuple)) and index < len(changes)
                        else None
                    ),
                    "leader": (
                        leaders[index]
                        if isinstance(leaders, (list, tuple)) and index < len(leaders)
                        else None
                    ),
                    "leader_change_pct": (
                        leader_changes[index]
                        if isinstance(leader_changes, (list, tuple))
                        and index < len(leader_changes)
                        else None
                    ),
                }.items()
                if item not in (None, "")
            }
        )
    return rows


def _compact_evidence_record(record: Mapping[str, Any]) -> Any:
    compact = _compact_value(record)
    quote_preview = _native_table_quote_preview(record.get("data"))
    if quote_preview is not None and isinstance(compact, dict):
        compact["data"] = quote_preview
    return compact


def _snapshot_data_preview(value: Any) -> Any:
    """Expose a shallow clue; exact nested values require evidence opening."""
    native_quote_preview = _native_table_quote_preview(value)
    if native_quote_preview is not None:
        return native_quote_preview
    if not isinstance(value, Mapping):
        return _preview_leaf(value)
    preview: dict[str, Any] = {}
    for key, item in list(value.items())[:12]:
        if isinstance(item, Mapping):
            nested = {
                str(nested_key): _preview_leaf(nested_value)
                for nested_key, nested_value in list(item.items())[:6]
                if not isinstance(nested_value, (Mapping, list, tuple))
            }
            nested["_field_count"] = len(item)
            preview[str(key)] = nested
        elif isinstance(item, (list, tuple)):
            preview[str(key)] = {"_item_count": len(item)}
        else:
            preview[str(key)] = _preview_leaf(item)
    if len(value) > 12:
        preview["_omitted_field_count"] = len(value) - 12
    return preview


def _preview_leaf(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 240:
        return value[:240] + "…"
    if isinstance(value, (Mapping, list, tuple)):
        return "[open exact evidence field to inspect]"
    return value
