"""定时调度配置 — 被 main.py init schedules 命令导入"""

from dataclasses import dataclass, field
from datetime import date

import exchange_calendars
import pandas as pd
from jettask import Schedule as JetTaskSchedule


@dataclass
class Schedule(JetTaskSchedule):
    """Project schedule contract with exchange-session extensions."""

    # Production JetTask validates timezone for every schedule.  Keep legacy
    # schedules on the base UTC semantics while research/market schedules set
    # an explicit exchange timezone.
    timezone: str = "UTC"
    active_windows: list[dict] = field(default_factory=list)
    calendar: dict = field(default_factory=dict)

    def _to_dict(self) -> dict:
        value = super()._to_dict()
        value["timezone"] = self.timezone
        if self.active_windows:
            value["active_windows"] = self.active_windows
        if self.calendar:
            value["calendar"] = self.calendar
        return value


def _exchange_calendar_config(calendar_name: str) -> dict:
    """Materialize exchange closures for JetTask without runtime handler checks."""

    current_year = date.today().year
    start = pd.Timestamp(date(current_year - 1, 1, 1))
    end = pd.Timestamp(date(current_year + 5, 12, 31))
    calendar = exchange_calendars.get_calendar(calendar_name)
    start = max(start, calendar.first_session)
    end = min(end, calendar.last_session)
    sessions = set(calendar.sessions_in_range(start, end).date)
    weekdays = set(pd.date_range(start=start, end=end, freq="B").date)
    return {
        "valid_from": start.date().isoformat(),
        "valid_until": end.date().isoformat(),
        "weekdays": [1, 2, 3, 4, 5],
        "excluded_dates": sorted(day.isoformat() for day in weekdays - sessions),
        "included_dates": sorted(
            day.isoformat() for day in sessions if day.weekday() >= 5
        ),
    }


CN_EXCHANGE_CALENDAR = _exchange_calendar_config("XSHG")
CN_CONTINUOUS_TRADING_WINDOWS = [
    {"start": "09:25", "end": "11:31", "weekdays": [1, 2, 3, 4, 5]},
    {"start": "12:59", "end": "15:06", "weekdays": [1, 2, 3, 4, 5]},
]
CN_EVENT_TRADING_WINDOWS = [
    {"start": "09:15", "end": "11:31", "weekdays": [1, 2, 3, 4, 5]},
    {"start": "12:59", "end": "15:06", "weekdays": [1, 2, 3, 4, 5]},
]


def _cn_schedule_constraints(*, event_window: bool = False) -> dict:
    return {
        "timezone": "Asia/Shanghai",
        "active_windows": (
            CN_EVENT_TRADING_WINDOWS
            if event_window
            else CN_CONTINUOUS_TRADING_WINDOWS
        ),
        "calendar": CN_EXCHANGE_CALENDAR,
    }


def _observability_metadata(schedule: Schedule) -> dict:
    """Materialize stable business labels into JetTask metadata at registration."""
    name = schedule.name
    description = schedule.description
    queue = schedule.queue
    kwargs = schedule.kwargs or {}
    aggregator = str(kwargs.get("aggregator") or "")
    source_name = str(kwargs.get("source_name") or "")
    source_map = {
        "cls": "财联社", "cls_depth": "财联社", "cls_hot_article": "财联社",
        "ths": "同花顺", "ths_discover": "同花顺",
        "em_news": "东方财富", "em_reports": "东方财富",
        "pboc_omo": "中国人民银行", "pboc_monetary": "中国人民银行",
        "sina": "新浪财经", "xueqiu": "雪球",
        "gov": "政府网站", "northbound": "东方财富",
        "dragon_tiger_em": "东方财富", "dragon_tiger_ths": "同花顺",
        "guba_popularity": "东方财富", "guba_posts": "东方财富",
        "limit_pool_up": "同花顺", "limit_pool_down": "同花顺",
        "mofcom_social_fin": "商务部",
        "akshare_us_cpi": "AKShare", "akshare_cnus_yield": "AKShare",
    }
    if queue == "collect_collection_source":
        source = source_map.get(source_name)
        if source is None:
            if source_name.startswith("em_"):
                source = "东方财富"
            elif source_name.startswith("pboc_"):
                source = "中国人民银行"
            elif source_name.startswith("xueqiu_"):
                source = "雪球"
            elif source_name.startswith("tencent_"):
                source = "腾讯财经"
            else:
                source = "来源待识别"
        module = "新闻资讯" if aggregator == "news" else "市场总览"
        channel = "http"
    elif "watchlist" in name:
        source, module, channel = "系统内部", "跟踪标的", "internal"
    elif "ths" in name or "同花顺" in description:
        source = "同花顺"
        module = "ETF" if "etf" in name else "期货" if "futures" in name else "黄金" if "gold" in name else "美股" if "_us_" in name else "板块市场" if "sector" in name else "个股市场" if "stock" in name or "market_events" in name else "市场总览"
        channel = "native_callback" if any(token in name for token in ("zone", "rankings", "dynamic_groups", "sector_", "futures")) else "app_http"
    else:
        source = "中国人民银行" if "pboc" in name else "交易所 / 公共行情" if any(token in name for token in ("market_daily", "market_reference", "etf_daily")) else "系统内部" if any(token in name for token in ("materialize_", "catchup")) else "来源待识别"
        module = "ETF" if "etf" in name else "市场总览"
        channel = "internal" if source == "系统内部" else "http"
    labels = {"http": "HTTP 拉取", "internal": "内部处理", "native_callback": "App 请求回调", "app_http": "App HTTP"}
    return {
        "owner": {"system": "smart-fund", "component": "collection"},
        "observability": {
            "source": source,
            "module": module,
            "category": "资讯与基础数据" if module in {"新闻资讯", "跟踪标的"} else "行情快照",
            "display_name": description.split("—", 1)[0].strip() or name,
            "channel": channel,
            "channel_label": labels[channel],
        },
    }


# 一个条目对应一个真实数据源。JetTask schedules 表中的 interval_seconds
# 是唯一调度周期来源，ft_collection_state 不再参与周期判断。
FLAT_COLLECTION_SOURCE_CONFIGS = (
    # 新闻
    ("news", "cls", 180, "财联社电报"),
    ("news", "cls_depth", 600, "财联社深度文章"),
    ("news", "cls_hot_article", 300, "财联社热门文章"),
    ("news", "gov", 10800, "政府网站资讯"),
    ("news", "pboc_omo", 86400, "人民银行公开市场操作公告"),
    ("news", "pboc_monetary", 86400, "人民银行货币政策资讯"),
    ("news", "em_news", 900, "东方财富新闻"),
    ("news", "em_reports", 7200, "东方财富研报"),
    ("news", "ths", 1800, "同花顺资讯"),
    ("news", "sina", 3600, "新浪财经资讯"),
    ("news", "xueqiu", 1800, "雪球资讯"),
    ("news", "ths_discover", 60, "同花顺刷新推荐与热榜"),
    # 资金流
    ("fund_flow", "northbound", 180, "北向资金"),
    ("fund_flow", "dragon_tiger_em", 21600, "东方财富龙虎榜"),
    ("fund_flow", "dragon_tiger_ths", 21600, "同花顺龙虎榜"),
    # 情绪
    ("sentiment", "guba_popularity", 1800, "股吧人气"),
    ("sentiment", "limit_pool_up", 10800, "同花顺涨停池"),
    ("sentiment", "limit_pool_down", 10800, "同花顺跌停池"),
    ("sentiment", "xueqiu_hot_topics", 1800, "雪球热门话题"),
    ("sentiment", "xueqiu_hot_stocks", 1800, "雪球热门股票"),
    ("sentiment", "tencent_hot_stocks", 1800, "腾讯热门股票"),
    ("sentiment", "guba_posts", 1800, "持仓股吧帖子"),
    # 宏观
    ("macro", "em_cpi", 21600, "东方财富 CPI"),
    ("macro", "em_ppi", 21600, "东方财富 PPI"),
    ("macro", "em_pmi", 21600, "东方财富 PMI"),
    ("macro", "em_gdp", 21600, "东方财富 GDP"),
    ("macro", "em_industrial_va", 21600, "东方财富工业增加值"),
    ("macro", "em_m2", 21600, "东方财富 M2"),
    ("macro", "em_rmb_loan", 21600, "东方财富人民币贷款"),
    ("macro", "em_forex_reserve", 21600, "东方财富外汇储备"),
    ("macro", "em_fai", 21600, "东方财富固定资产投资"),
    ("macro", "em_retail_sales", 21600, "东方财富社会消费品零售"),
    ("macro", "em_customs_export", 21600, "东方财富海关进出口"),
    ("macro", "em_fdi", 21600, "东方财富 FDI"),
    ("macro", "pboc_shibor_lpr", 3600, "人民银行 Shibor 与 LPR"),
    ("macro", "pboc_usdcny", 3600, "人民银行美元人民币汇率"),
    ("macro", "pboc_omo_net", 21600, "人民银行公开市场净投放"),
    ("macro", "mofcom_social_fin", 21600, "社会融资规模"),
    ("macro", "akshare_us_cpi", 21600, "美国 CPI"),
    ("macro", "akshare_cnus_yield", 21600, "中美国债利差"),
)

FLAT_COLLECTION_SOURCE_SCHEDULES = [
    Schedule(
        name=f"collect_{aggregator}_{source_name}_{interval}s",
        queue="collect_collection_source",
        interval_seconds=interval,
        kwargs={"aggregator": aggregator, "source_name": source_name},
        description=f"{description} — 每 {interval} 秒",
    )
    for aggregator, source_name, interval, description
    in FLAT_COLLECTION_SOURCE_CONFIGS
]

THS_LEGACY_SECTOR_SCHEDULE_NAMES = [
    # Retired monolithic schedules. Their work is now represented by the
    # one-minute table/hot/flow fragments and five-minute signal fragments.
    "collect_ths_sector_core_5min",
    "collect_ths_sector_signals_5min",
    *[
        f"collect_ths_sector_ranking_{classification}_{metric}_120s"
        for classification in ("all", "industry", "concept", "style", "region")
        for metric in ("change", "speed", "volume_ratio", "limit_up_count")
    ],
]

THS_SECTOR_FRAGMENT_SCHEDULES = [
    *[
        Schedule(
            name=f"collect_ths_sector_hot_{classification}_60s",
            queue="collect_ths_sector_fragment_v2",
            interval_seconds=60,
            kwargs={"kind": "hot", "classification": classification},
            description=f"同花顺 {classification} 热门板块原子快照 — 每 60 秒",
        )
        for classification in ("concept", "industry", "index")
    ],
    *[
        Schedule(
            name=f"collect_ths_sector_table_{classification}_60s",
            queue="collect_ths_sector_fragment_v2",
            interval_seconds=60,
            kwargs={
                "kind": "table",
                "classification": classification,
            },
            description=(
                f"同花顺 {classification} 板块全表及三类排行 — 每 60 秒"
            ),
        )
        for classification in ("all", "industry", "concept", "style", "region")
    ],
    *[
        Schedule(
            name=f"collect_ths_sector_limit_up_{classification}_60s",
            queue="collect_ths_sector_fragment_v2",
            interval_seconds=60,
            kwargs={
                "kind": "ranking",
                "classification": classification,
                "metric": "limit_up_count",
            },
            description=f"同花顺 {classification} 板块涨停数 — 每 60 秒",
        )
        for classification in ("all", "industry", "concept", "style", "region")
    ],
    *[
        Schedule(
            name=f"collect_ths_sector_flow_{classification}_60s",
            queue="collect_ths_sector_fragment_v2",
            interval_seconds=60,
            kwargs={"kind": "flow", "classification": classification},
            description=f"同花顺 {classification} 板块资金原子快照 — 每 60 秒",
        )
        for classification in ("industry", "concept", "region")
    ],
]

THS_SECTOR_SIGNAL_FRAGMENT_SCHEDULES = [
    *[
        Schedule(
            name=f"collect_ths_sector_rotation_{sector_type}_{metric}_5min",
            queue="collect_ths_sector_signal_fragment_v2",
            interval_seconds=300,
            kwargs={
                "kind": "rotation",
                "sector_type": sector_type,
                "metric": metric,
            },
            description=(
                f"同花顺 {sector_type}/{metric} 板块轮动原子快照 — 每 5 分钟"
            ),
        )
        for sector_type in ("industry", "concept")
        for metric in (
            "change",
            "five_day_change",
            "rise_rate",
            "limit_up_count",
            "main_net_inflow",
        )
    ],
    *[
        Schedule(
            name=f"collect_ths_sector_{kind}_5min",
            queue="collect_ths_sector_signal_fragment_v2",
            interval_seconds=300,
            kwargs={"kind": kind},
            description=f"同花顺 {kind} 板块信号原子快照 — 每 5 分钟",
        )
        for kind in ("industry_opportunity", "prosperity", "commodity_linkage")
    ],
]

THS_LEGACY_FUTURES_SCHEDULE_NAMES = [
    *[
        f"collect_ths_futures_{kind}_120s"
        for kind in (
            "hot", "indices", "fund_inflow", "fund_outflow",
            "market_state", "market_net_flow",
        )
    ],
    *[
        f"collect_ths_futures_ranking_{group}_120s"
        for group in (
            "all", "night", "energy_chemical", "nonferrous", "precious",
            "ferrous", "agriculture", "financial", "shfe", "dce",
            "czce", "ine", "gfex", "cffex",
        )
    ],
]

RETIRED_REDUNDANT_MARKET_SCHEDULE_NAMES = [
    "materialize_sentiment_signal_after_close",
    "collect_rate_liquidity_1h",
    "collect_stock_change_events_30s",
    "collect_sector_market_60s",
    "collect_sector_fund_flow_60s",
    "collect_cross_market_60s",
    "market_boundary_open_0925",
    "market_boundary_noon_1130",
    "market_boundary_reopen_1300",
    "market_boundary_close_1505",
    "collect_news_1min",
    "collect_fund_flow_1min",
    "collect_sentiment_15min",
    "collect_macro_1h",
    "collect_stock_dynamic_groups_180s",
]


SCHEDULES = [
    Schedule(
        name="research_outcome_evaluation_hourly",
        queue="evaluate_research_outcomes",
        cron_expression="10 * * * *",
        kwargs={},
        description="Research Forecast 到期结果评估 — 每小时第10分钟",
    ),
    Schedule(
        name="research_agent_premarket",
        queue="run_research_agent",
        cron_expression="50 0 * * 1-5",
        kwargs={
            "trigger_slot": "premarket",
            "reason": "盘前检查隔夜信息、宏观变化与当日待验证观点",
        },
        description="Research Agent 盘前研究 — 北京时间 08:50",
    ),
    Schedule(
        name="research_agent_intraday",
        queue="run_research_agent",
        cron_expression="35 3 * * 1-5",
        kwargs={
            "trigger_slot": "intraday",
            "reason": "盘中检查市场反应、竞争假设与观点失效条件",
        },
        description="Research Agent 盘中研究 — 北京时间 11:35",
    ),
    Schedule(
        name="research_agent_postmarket",
        queue="run_research_agent",
        cron_expression="35 7 * * 1-5",
        kwargs={
            "trigger_slot": "postmarket",
            "reason": "盘后复盘当日事实、市场反应并更新最新观点",
        },
        description="Research Agent 盘后研究 — 北京时间 15:35",
    ),
    # ── 数据采集 ──────────────────────────
    *FLAT_COLLECTION_SOURCE_SCHEDULES,
    Schedule(name="scan_watchlist_instruments_15s", queue="scan_watchlist_instruments", interval_seconds=15, description="跟踪标的到期扫描 — 每 15 秒"),
    Schedule(name="collect_stock_rankings_120s", queue="collect_stock_rankings", interval_seconds=120, description="同花顺 9 类个股排行全量快照 — 交易窗口内每 120 秒", **_cn_schedule_constraints()),
    Schedule(name="collect_stock_dynamic_groups_60s", queue="collect_stock_dynamic_groups", interval_seconds=60, description="同花顺个股动态分组全量快照 — 每 60 秒，全天运行（盘后与周末热榜仍会更新）"),
    Schedule(name="collect_ths_market_events_30s", queue="collect_ths_market_events", interval_seconds=30, description="同花顺个股、板块、大笔委托、大盘异动与集合竞价 — 有效窗口内每 30 秒", **_cn_schedule_constraints(event_window=True)),
    Schedule(name="collect_ths_market_context_60s", queue="collect_ths_market_context", interval_seconds=60, description="同花顺大盘资金、市场温度与北向资金 — 交易窗口内每 60 秒", **_cn_schedule_constraints()),
    # App 首页在收盘后仍可能刷新最终统计；全天调度也避免本地日历与
    # App 服务端交易日不一致时漏掉整批卡片数据。
    Schedule(name="collect_ths_market_profile_60s", queue="collect_ths_market_profile", interval_seconds=60, description="同花顺涨跌停、昨日涨停与大小盘对比 — 每 60 秒，全天运行"),
    *THS_SECTOR_FRAGMENT_SCHEDULES,
    Schedule(name="collect_ths_sector_references_5min", queue="collect_ths_sector_reference_snapshot_v2", interval_seconds=300, description="同花顺活跃板块原生成分参考补齐 — 每 5 分钟"),
    *THS_SECTOR_SIGNAL_FRAGMENT_SCHEDULES,
    Schedule(name="collect_etf_estimated_net_inflow_60s", queue="collect_etf_estimated_net_inflow", interval_seconds=60, description="同花顺深市 ETF 预估申购净流入 — 交易窗口内每 60 秒", **_cn_schedule_constraints()),
    Schedule(name="collect_ths_etf_zone_15s", queue="collect_ths_etf_zone", interval_seconds=15, description="同花顺 App ETF 专区全量快照 — 每 15 秒，全天运行（单轮防重叠；盘后热度与海外行情仍更新）"),
    Schedule(name="collect_ths_futures_cycle_60s", queue="collect_ths_futures_cycle", interval_seconds=60, description="同花顺 App 期货全模块单通道原子采集 — 每 60 秒，单轮防重叠"),
    Schedule(name="collect_ths_gold_zone_120s", queue="collect_ths_gold_zone", interval_seconds=120, description="同花顺 App 黄金专区全量快照 — 每 120 秒，全天运行（覆盖境内、国际和线下金价）"),
    Schedule(name="collect_ths_us_overview_60s", queue="collect_ths_us_overview", interval_seconds=60, description="同花顺 App 美股涨跌统计与指数 — 每 60 秒，全天运行"),
    Schedule(name="collect_ths_us_sectors_120s", queue="collect_ths_us_sectors", interval_seconds=120, description="同花顺 App 美股行业与概念板块 — 每 120 秒，全天运行"),
    Schedule(name="collect_ths_us_stock_rankings_120s", queue="collect_ths_us_stock_rankings", interval_seconds=120, description="同花顺 App 美股七类股票排行 — 每 120 秒，按协议族串行且失败分组不覆盖成功快照"),
    Schedule(name="collect_ths_us_etf_sectors_120s", queue="collect_ths_us_etf_sectors", interval_seconds=600, description="同花顺 App 美股 ETF 九分类全量校验与缺口修复 — 每 10 分钟；实时变化由长连接订阅维护"),
    Schedule(name="collect_pboc_rate_liquidity_1h", queue="collect_pboc_rate_liquidity", interval_seconds=3600, description="中国人民银行利率与国债收益率 — 每 1 小时"),
    Schedule(name="collect_ths_index_sentiment_15min", queue="collect_ths_index_sentiment", interval_seconds=900, description="同花顺上证50与创成长指数情绪 — 每 15 分钟"),

    # ── 盘后 ─────────────────────────────
    # jettask-rs 当前按 UTC 计算 cron；北京时间 15:30 对应 UTC 07:30。
    Schedule(name="collect_market_daily_bars_after_close", queue="collect_market_daily_bars", cron_expression="20 7 * * 1-5", description="板块、ETF、指数与商品日级数据 — 北京时间 15:20"),
    Schedule(name="collect_market_reference_after_close", queue="collect_market_reference_data", cron_expression="0 8 * * 1-5", description="板块、成分与 ETF 参考目录 — 北京时间 16:00"),
    Schedule(name="collect_market_valuation_1h", queue="collect_market_valuation", interval_seconds=3600, description="上证与深证市场 PE/PB 及同花顺估值阈值 — 每 1 小时追赶最新交易日"),
    Schedule(name="collect_bond_index_60s", queue="collect_bond_index", interval_seconds=60, description="中债指数及同花顺长短期国债主连 — 每 60 秒追赶当日行情"),
    Schedule(name="scan_watchlist_daily_after_close", queue="scan_watchlist_daily", cron_expression="30 12 * * 1-5", description="跟踪标的日频数据 — 北京时间 20:30"),
    Schedule(name="scan_watchlist_reference_weekly", queue="scan_watchlist_reference", cron_expression="0 13 * * 5", description="跟踪标的低频参考资料 — 北京时间周五 21:00"),
    Schedule(name="collect_etf_daily_shares_night", queue="collect_etf_daily_shares", cron_expression="10 15 * * 1-5", description="交易所 ETF 份额主采 — 北京时间 23:10"),
    Schedule(name="collect_etf_daily_shares_catchup", queue="collect_etf_daily_shares", cron_expression="15 0 * * 1-5", description="交易所 ETF 份额补偿采集 — 北京时间 08:15"),
    Schedule(name="collect_market_daily_catchup_morning", queue="collect_market_daily_catchup", cron_expression="30 0 * * 1-5", description="市场日级数据停机追赶 — 北京时间 08:30"),
]

for _schedule in SCHEDULES:
    _schedule.metadata = _observability_metadata(_schedule)
    _schedule.tags = ["smart-fund", "collection", _schedule.metadata["observability"]["module"]]
