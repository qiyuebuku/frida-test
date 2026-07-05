"""资金流聚合 (P0)

数据源: 北向资金(EM)、板块资金流(Sina)、个股主力资金(Tencent)、
        龙虎榜(EM+THS)、自选标的(THS+Tencent+Sina+EM)
目标表: ft_market_flow（全市场数据）+ ft_watchlist_data（自选标的数据）

DATA_TYPE 注册表
================
ft_market_flow 的 data_type：
  northbound      北向资金          源: northbound        客户端: eastmoney.get_northbound_recent
  sector_flow     板块资金流        源: sector_flow_sina   客户端: sina.get_sector_money_flow
  dragon_tiger    龙虎榜            源: dragon_tiger_em/ths 客户端: eastmoney.get_dragon_tiger / ths.get_ths_dragon_tiger

ft_watchlist_data 的 data_type（源: watchlist_data）：
  基金维度（20个）：
    nav             净值走势        ths.get_nav_trend                高频
    realtime        实时估值        ths.get_realtime_trend           高频
    nav_technical   技术指标        ths.get_nav_technical            高频
    performance     业绩排名        ths.get_performance_rank         日频
    flow_trend      资金流向趋势    ths.get_fund_flow_trend          日频
    periodic_rate   区间收益率      ths.get_periodic_rate            日频
    profit_contribution 收益贡献    ths.get_profit_contribution      日频
    nav_sina        Sina净值        sina.get_fund_nav                日频
    holdings        前十大持仓      ths.get_top10_holdings           低频
    scale           规模变动        ths.get_scale_change             低频
    holder_ratio    持有人结构      ths.get_holder_ratio             低频
    dividend        分红历史        ths.get_dividend_history         低频
    year_return     年度收益        ths.get_year_return              低频
    max_drawdown    最大回撤        ths.get_max_drawdown             低频
    holding_overview 持仓概览       ths.get_holding_overview         低频
    fund_detail     基金详情        ths.get_fund_detail              低频
    style_preference 风格偏好       ths.get_style_preference         低频
    asset_allocation 资产配置       ths.get_asset_allocation         低频
    position_detail  持仓明细       ths.get_position_detail          低频
    trade_rule       交易规则       ths.get_trade_rule               低频
    manager_info     基金经理       ths.get_manager_info             低频（二阶段：从fund_detail提取manager_id）
  场内基金/个股维度（5-6个）：
    stock_flow      主力资金流      tencent.get_stock_fund_flow
    quote           实时行情        tencent.get_stock_quote
    minute_data     分时数据        tencent.get_minute_data
    kline           日K线           sina.get_kline
    valuation       估值历史        eastmoney.get_stock_valuation_history（仅个股）
    plates          所属板块        tencent.get_stock_plates（低频，仅个股）
    guba_posts      股吧帖子        eastmoney.get_guba_posts（低频，fund+stock 通用）
  QDII 关联维度（3个，code='_market'）：
    global_index    全球指数        sina.get_global_index
    forex           外汇            sina.get_forex
    intl_futures    国际期货        tencent.get_intl_futures
    research        全市场研报      eastmoney.get_research_reports（低频，code='_market'）
"""

import json
import logging
from datetime import datetime, date

from src.domain.collection.services.base import BaseAggregator, SourceDef
from src.infrastructure.time_utils import app_today, app_today_iso

logger = logging.getLogger(__name__)

# ==================== Normalize 函数 ====================


def _today() -> str:
    return app_today_iso()


def normalize_northbound(raw) -> list[dict]:
    """东方财富北向资金 → 统一格式

    get_northbound_recent 返回: {code, result:{data:[{TRADE_DATE, DEAL_AMT, MUTUAL_TYPE}], count, pages}, message, success}
    """
    items_list = []
    if isinstance(raw, dict):
        # 优先 result.data（真实返回），回退 data.data
        result = raw.get("result") or {}
        if isinstance(result, dict):
            items_list = result.get("data") or []
        if not items_list:
            data = raw.get("data") or {}
            if isinstance(data, dict):
                items_list = data.get("data") or []
            elif isinstance(data, list):
                items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        trade_date = (item.get("TRADE_DATE") or "")[:10]
        if not trade_date:
            continue
        results.append({
            "data_type": "northbound",
            "trade_date": trade_date,
            "data": {
                "net_flow": item.get("DEAL_AMT"),
                "mutual_type": item.get("MUTUAL_TYPE"),
                "raw": {k: v for k, v in item.items() if v is not None},
            },
        })
    return results


def normalize_sector_flow_em(raw) -> list[dict]:
    """东方财富板块资金流 → 统一格式

    get_index_capital_flow_daily 返回 dict
    """
    if not raw or not isinstance(raw, dict):
        return []
    data = raw.get("data", raw)
    items_list = data.get("data", []) if isinstance(data, dict) else []
    if not items_list and isinstance(data, list):
        items_list = data

    results = []
    for item in items_list:
        results.append({
            "data_type": "sector_flow",
            "trade_date": _today(),
            "data": {
                "name": item.get("BOARD_NAME") or item.get("TRADE_NAME") or "",
                "net_amount": item.get("NET_INFLOW") or item.get("CHANGE_RATE") or 0,
                "source": "eastmoney",
                "raw": item,
            },
        })
    return results if results else [{
        "data_type": "sector_flow",
        "trade_date": _today(),
        "data": {"source": "eastmoney", "raw": raw},
    }]


def normalize_sector_flow_sina(raw) -> list[dict]:
    """新浪板块资金流 → 统一格式

    get_sector_money_flow 返回: {data:{type, count, sectors:[{name, bigIn, midIn, bigOut, midOut,
                                 largeIn, smallIn, largeOut, smallOut, netAmount}]}, status_code}
    """
    items_list = []
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items_list = data.get("sectors") or data.get("data") or []
        elif isinstance(data, list):
            items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        results.append({
            "data_type": "sector_flow",
            "trade_date": _today(),
            "data": {
                "name": item.get("name") or "",
                "net_amount": item.get("netAmount") or item.get("net") or 0,
                "big_in": item.get("bigIn") or 0,
                "big_out": item.get("bigOut") or 0,
                "mid_in": item.get("midIn") or 0,
                "mid_out": item.get("midOut") or 0,
                "large_in": item.get("largeIn") or 0,
                "large_out": item.get("largeOut") or 0,
                "small_in": item.get("smallIn") or 0,
                "small_out": item.get("smallOut") or 0,
                "source": "sina",
            },
        })
    return results


def _market_prefix(market_id: int | str) -> str:
    """同花顺 marketid → 腾讯接口前缀

    17 = 上交所主板 → sh
    33 = 深交所主板/创业板 → sz
    151 = 北交所 → bj
    """
    try:
        mid = int(market_id) if market_id else 0
    except (ValueError, TypeError):
        mid = 0
    if mid == 17:
        return "sh"
    elif mid in (33,):
        return "sz"
    elif mid == 151:
        return "bj"
    return "sh"  # 默认沪市


def _code_with_prefix(sec_code: str, market_id: int | str = None) -> str:
    """secCode → 带前缀的代码（如 sh600183）"""
    if not sec_code:
        return ""
    if sec_code.startswith(("sh", "sz", "bj")):
        return sec_code
    if market_id:
        return f"{_market_prefix(market_id)}{sec_code}"
    # 按代码前缀推断
    if sec_code.startswith("6"):
        return f"sh{sec_code}"
    elif sec_code.startswith(("0", "3")):
        return f"sz{sec_code}"
    elif sec_code.startswith(("4", "8", "9")):
        return f"bj{sec_code}"
    return f"sh{sec_code}"


def _is_a_share_code(sec_code: str) -> bool:
    """判断是否为 A 股代码（6 位数字，沪/深/北）

    腾讯个股资金流接口只支持 A 股，需要过滤掉港股(5 位)、美股(字母)、QDII 持仓等。
    """
    if not sec_code or not isinstance(sec_code, str):
        return False
    code = sec_code.strip()
    if len(code) != 6 or not code.isdigit():
        return False
    # 沪市 6/9 / 深市 0/3 / 北交所 4/8
    return code[0] in ("0", "3", "4", "6", "8", "9")



# ==================== 自选标的采集 ====================

# 基金采集维度配置（按变化频率分三档）
# HIGH:  每次 tick 都采（盘中高频数据）
# DAILY: 每天采一次（日频，收盘后变化）
# LOW:   每周采一次（变化极慢：季报/半年报/年度）
FUND_DIMENSIONS_HIGH = ["nav", "realtime", "nav_technical"]
FUND_DIMENSIONS_DAILY = [
    "performance", "flow_trend",
    "nav_sina",          # Sina 净值（支持任意日期范围，与 THS nav 互补）
    "periodic_rate",     # 区间收益率（日频更新）
    "profit_contribution",  # 收益贡献分析（threeMonth/halfYear/year）
]
FUND_DIMENSIONS_LOW = [
    "holdings", "scale", "holder_ratio", "dividend", "year_return",
    "max_drawdown", "holding_overview", "fund_detail", "style_preference",
    "asset_allocation",  # 大类资产配置（季度）
    "position_detail",   # 完整持仓明细（季度）
    "trade_rule",        # 交易费率（极低变化频率）
]

# 标的级并发上限（防止同花顺/腾讯限流）
_WATCHLIST_CONCURRENCY = 8


def _is_exchange_traded(code: str) -> bool:
    """判断是否场内基金（ETF/LOF）

    市场规则：
      深交所场内：15xxxx（ETF）、16xxxx（LOF）
      上交所场内：510xxx~518xxx（ETF）、501xxx~502xxx（LOF）
      注意：519xxx 开头是场外基金（OTC），不是场内！
    """
    if not code or not isinstance(code, str):
        return False
    if code.startswith(("15", "16")):
        return True
    # 51xxxx 中只有 510~518 是上交所场内，519xxx 是 OTC 场外
    if code.startswith("51") and not code.startswith("519"):
        return True
    return False


def _expand_timeseries(code: str, dim: str, data, today: date, since: str = None) -> list[dict]:
    """把时序维度的原始返回按日期展开为多行。非时序维度返回 [(今日快照)]。

    Args:
        since: 增量截止日期（ISO 格式），只保留 > since 的时序记录。
               None 表示全量（首次回填）。

    时序展开规则：
      - nav:        同花顺字符串 "YYYYMMDD;累计净值;单位净值;涨跌幅;...|..." → 每天一行
      - kline:      {bars:[{date,open,close,high,low,volume}]} → 每根 K 线一行
      - stock_flow: {history:[{date,mainNetIn,price}], today:{...}, fiveDayList:[...]}
                    → history 每天一行 + today 今日一行
    """
    rows: list[dict] = []
    from datetime import timedelta
    earliest = (today - timedelta(days=365)).isoformat()

    # ── nav: 字符串按 | 分隔，每行按 ; 分隔 ──
    if dim == "nav" and isinstance(data, str) and data:
        for seg in data.split("|"):
            parts = seg.split(";")
            if len(parts) < 3:
                continue
            raw_date = parts[0].strip()
            if len(raw_date) != 8 or not raw_date.isdigit():
                continue
            trade_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            if trade_date < earliest:
                continue
            if since and trade_date <= since:
                continue

            def _f(i):
                try:
                    return float(parts[i]) if parts[i] not in ("", None) else None
                except (ValueError, IndexError):
                    return None

            rows.append({
                "code": code, "data_type": "nav", "trade_date": trade_date,
                "data": {
                    "date": trade_date,
                    "cumNav": _f(1),
                    "unitNav": _f(2),
                    "dailyReturn": _f(3),
                    "unitNav2": _f(4),
                    "field5": _f(5),
                    "scale": _f(6),
                },
            })
        return rows

    # ── kline: bars 列表 ──
    if dim == "kline" and isinstance(data, dict):
        bars = data.get("bars") or []
        for bar in bars:
            if not isinstance(bar, dict):
                continue
            d = (bar.get("date") or "")[:10]
            if not d or d < earliest:
                continue
            if since and d <= since:
                continue
            rows.append({
                "code": code, "data_type": "kline", "trade_date": d,
                "data": {**bar, "symbol": data.get("symbol")},
            })
        return rows

    # ── stock_flow: history 每天一行 + today 今日一行 ──
    if dim == "stock_flow" and isinstance(data, dict):
        stock_code = data.get("code") or code
        today_str = today.isoformat()
        history = data.get("history") or []
        if isinstance(history, list):
            for h in history:
                if not isinstance(h, dict):
                    continue
                h_date = (h.get("date") or "")[:10]
                if not h_date or h_date < earliest:
                    continue
                if since and h_date <= since:
                    continue
                if h_date == today_str:
                    continue
                rows.append({
                    "code": code, "data_type": "stock_flow", "trade_date": h_date,
                    "data": {
                        "code": stock_code,
                        "date": h_date,
                        "price": h.get("price"),
                        "mainNetIn": h.get("mainNetIn"),
                        "source": "tencent",
                        "from": "history",
                    },
                })
        # today 实时值作为今日一行
        today_snap = data.get("today")
        if isinstance(today_snap, dict) and today_snap:
            # 全 0 跳过（指数或非交易日）
            numeric = [v for v in today_snap.values() if isinstance(v, (int, float))]
            if not numeric or any(v != 0 for v in numeric):
                rows.append({
                    "code": code, "data_type": "stock_flow", "trade_date": today.isoformat(),
                    "data": {
                        "code": stock_code,
                        "date": today.isoformat(),
                        "today": today_snap,
                        "source": "tencent",
                        "from": "today",
                    },
                })
        return rows

    # ── holdings: 按季报 endDate 展开 ──
    # 不过滤 since：同 position_detail，季报日期 <= since 会被误过滤
    if dim == "holdings" and isinstance(data, dict):
        stocks = data.get("stock") or []
        if stocks and isinstance(stocks, list) and isinstance(stocks[0], dict):
            raw_date = stocks[0].get("endDate") or ""
            if len(raw_date) == 8 and raw_date.isdigit():
                trade_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
                rows.append({
                    "code": code, "data_type": "holdings", "trade_date": trade_date,
                    "data": data,
                })
                return rows
        # 无法解析日期 → 存为今日快照
        if not stocks:
            return rows
        rows.append({"code": code, "data_type": dim, "trade_date": today.isoformat(), "data": data})
        return rows

    # ── scale: gmbd dict 按季度日期展开 ──
    if dim == "scale" and isinstance(data, dict):
        gmbd = data.get("gmbd")
        if isinstance(gmbd, dict) and gmbd:
            for d_key, info in gmbd.items():
                trade_date = d_key[:10]
                if since and trade_date <= since:
                    continue
                rows.append({
                    "code": code, "data_type": "scale", "trade_date": trade_date,
                    "data": info if isinstance(info, dict) else {"value": info},
                })
            return rows

    # ── holder_ratio: [{date, orgRate, ...}] 按报告日期展开 ──
    if dim == "holder_ratio" and isinstance(data, list) and data:
        for item in data:
            if not isinstance(item, dict):
                continue
            trade_date = (item.get("date") or "")[:10]
            if not trade_date:
                continue
            if since and trade_date <= since:
                continue
            rows.append({
                "code": code, "data_type": "holder_ratio", "trade_date": trade_date,
                "data": item,
            })
        return rows

    # ── dividend: dividends [{payDate, perShare, ...}] 按除权日展开 ──
    if dim == "dividend" and isinstance(data, dict):
        divs = data.get("dividends") or []
        if isinstance(divs, list) and divs:
            for item in divs:
                if not isinstance(item, dict):
                    continue
                trade_date = (item.get("payDate") or "")[:10]
                if not trade_date:
                    continue
                if since and trade_date <= since:
                    continue
                rows.append({
                    "code": code, "data_type": "dividend", "trade_date": trade_date,
                    "data": item,
                })
            return rows
        # 无分红 → 不写入
        return rows

    # ── flow_trend: quarters [{date, nav, subscribe, redeem, netFlow}] 按季度展开 ──
    if dim == "flow_trend" and isinstance(data, dict):
        quarters = data.get("quarters") or []
        if isinstance(quarters, list) and quarters:
            for item in quarters:
                if not isinstance(item, dict):
                    continue
                trade_date = (item.get("date") or "")[:10]
                if not trade_date:
                    continue
                if since and trade_date <= since:
                    continue
                rows.append({
                    "code": code, "data_type": "flow_trend", "trade_date": trade_date,
                    "data": item,
                })
            # 额外保留趋势摘要为今日快照
            summary_fields = {k: v for k, v in data.items() if k != "quarters"}
            if summary_fields:
                rows.append({
                    "code": code, "data_type": "flow_trend_summary",
                    "trade_date": today.isoformat(), "data": summary_fields,
                })
            return rows

    # ── year_return: [{time: "2014年", yield, rank, ...}] 按年份展开 ──
    if dim == "year_return" and isinstance(data, list) and data:
        for item in data:
            if not isinstance(item, dict):
                continue
            time_str = item.get("time") or ""
            # "2014年" → "2014-12-31"
            year_match = time_str[:4]
            if year_match.isdigit():
                trade_date = f"{year_match}-12-31"
                if since and trade_date <= since:
                    continue
                rows.append({
                    "code": code, "data_type": "year_return", "trade_date": trade_date,
                    "data": item,
                })
        return rows

    # ── nav_sina: navs [{date, nav, accNav}] 按日展开 ──
    if dim == "nav_sina" and isinstance(data, dict):
        for item in (data.get("navs") or []):
            if not isinstance(item, dict):
                continue
            d = (item.get("date") or "")[:10]
            if not d or d < earliest:
                continue
            if since and d <= since:
                continue
            rows.append({
                "code": code, "data_type": "nav_sina", "trade_date": d,
                "data": item,
            })
        return rows

    # ── asset_allocation: rateList [{time: "2021Q4", bondRatio, stockRatio, ...}] 按季展开 ──
    if dim == "asset_allocation" and isinstance(data, dict):
        _q_map = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}
        for item in (data.get("rateList") or []):
            if not isinstance(item, dict):
                continue
            time_str = item.get("time") or ""  # e.g. "2021Q4"
            if len(time_str) >= 6 and "Q" in time_str:
                trade_date = f"{time_str[:4]}-{_q_map.get(time_str[-1], '12-31')}"
            else:
                continue
            if since and trade_date <= since:
                continue
            rows.append({
                "code": code, "data_type": "asset_allocation", "trade_date": trade_date,
                "data": item,
            })
        return rows

    # ── position_detail: reportDate "20251231" + heavyStock 列表 ──
    # 不过滤 since：季报日期（如 2025-12-31）永远 <= since（如 2026-04-15），
    # 会被增量误过滤。依赖 upsert 覆盖即可。
    if dim == "position_detail" and isinstance(data, dict):
        raw_date = data.get("reportDate") or ""
        if len(raw_date) == 8 and raw_date.isdigit():
            trade_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            rows.append({
                "code": code, "data_type": "position_detail", "trade_date": trade_date,
                "data": data,
            })
        return rows

    # ── periodic_rate: periodicRateList [{date: "20260414", rate, similarRate}] 按日展开 ──
    if dim == "periodic_rate" and isinstance(data, dict):
        for item in (data.get("periodicRateList") or []):
            if not isinstance(item, dict):
                continue
            raw_date = item.get("date") or ""
            if len(raw_date) == 8 and raw_date.isdigit():
                d = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            else:
                d = raw_date[:10]
            if not d or d < earliest:
                continue
            if since and d <= since:
                continue
            rows.append({
                "code": code, "data_type": "periodic_rate", "trade_date": d,
                "data": item,
            })
        return rows

    # ── valuation: [{date, pe_ttm, pb, market_cap}] 按日展开（个股用） ──
    if dim == "valuation" and isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            d = (item.get("date") or "")[:10]
            if not d or d < earliest:
                continue
            if since and d <= since:
                continue
            rows.append({
                "code": code, "data_type": "valuation", "trade_date": d,
                "data": item,
            })
        return rows

    # ── hk_us_kline: 按日展开，支持两种输入 ──
    # 单股输入（dict with bars）：{code, period, bars: [{date,open,close,...}]}
    # 多股合并输入（list of dicts）：[{code, period, bars: [...]}, ...]
    if dim == "hk_us_kline":
        sources = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        date_bars: dict[str, list] = {}
        for src in sources:
            if not isinstance(src, dict):
                continue
            tc_code = src.get("code", "")
            period = src.get("period", "day")
            for bar in (src.get("bars") or []):
                if not isinstance(bar, dict):
                    continue
                d = (bar.get("date") or "")[:10]
                if not d or d < earliest:
                    continue
                if since and d <= since:
                    continue
                date_bars.setdefault(d, []).append({**bar, "tc_code": tc_code, "period": period})
        for d in sorted(date_bars):
            rows.append({
                "code": code, "data_type": "hk_us_kline", "trade_date": d,
                "data": date_bars[d],
            })
        return rows

    # ── guba_posts: 帖子列表快照，空 posts 不入库 ──
    if dim == "guba_posts":
        posts = data.get("posts") if isinstance(data, dict) else None
        if not posts:
            return rows
        rows.append({
            "code": code, "data_type": "guba_posts",
            "trade_date": today.isoformat(), "data": data,
        })
        return rows

    # ── 其他维度：快照，仅今日一条 ──
    # (nav_technical / profit_contribution / trade_rule / plates / hk_quote / us_quote 等)
    if not data:
        return rows
    rows.append({
        "code": code, "data_type": dim, "trade_date": today.isoformat(), "data": data,
    })
    return rows


async def _fetch_watchlist_data(ths_client, tencent_client, sina_client=None, em_client=None, cp=None) -> list[dict]:
    """遍历 watchlist，按 type 采集各维度数据

    增量策略（基于 cp.newest_time）：
        - 首次（无 newest_time）：全量回填 nav=year, kline=60, stock_flow=20，采集全部维度
        - 增量 gap=1（今日已采）：仅高频维度（nav/realtime）
        - 增量 gap 2-7：高频 + 日频（+performance/flow_trend）
        - 增量 gap>7：全部维度（含低频 holdings/scale/... 等）

    并发策略：
        - 标的级并发，最多 _WATCHLIST_CONCURRENCY 只同时采集
        - 每只标的内部各维度仍 asyncio.gather 并发

    返回 [{"code", "data_type", "trade_date", "data"}, ...]
    直接写入 ft_watchlist_data（不走 ft_market_flow）。
    """
    import asyncio as _asyncio
    from src.infrastructure.db import checkpoint_store
    import time as _time

    cp = cp or {}
    newest_time = cp.get("newest_time")
    today = app_today()

    if newest_time:
        if isinstance(newest_time, str):
            nt_date = date.fromisoformat(newest_time[:10])
        else:
            nt_date = newest_time if isinstance(newest_time, date) else newest_time.date()
        gap = (today - nt_date).days + 1
        # nav: API 只支持 month(~30天)/year(~250天)，选最小能覆盖 gap 的
        nav_period = "month" if gap <= 30 else "year"
        kline_len = min(max(gap, 3), 60)
        flow_days = min(max(gap, 3), 20)
        since = nt_date.isoformat()
    else:
        gap = None  # 首次全量
        nav_period = "year"
        kline_len = 60
        flow_days = 20
        since = None

    # 频率分级：决定本轮采哪些维度
    # gap=None  → 全量（首次），采全部
    # gap=1     → 今日已采，仅高频
    # gap 2-7   → 1-6 天前，高频+日频
    # gap>7     → 超过一周，全部
    collect_daily = (gap is None) or (gap > 1)
    collect_low   = (gap is None) or (gap > 7)

    if gap is None:
        tier_label = "全部（首次）"
        mode_label = "全量回填"
    elif not collect_daily:
        tier_label = "高频"
        mode_label = f"增量 gap={gap}d, since={since}"
    elif not collect_low:
        tier_label = "高频+日频"
        mode_label = f"增量 gap={gap}d, since={since}"
    else:
        tier_label = "全部"
        mode_label = f"增量 gap={gap}d, since={since}"

    all_items = checkpoint_store.list_all("watchlist")
    if not all_items:
        return []

    enabled = [x for x in all_items if x.get("enabled", True)]
    total = len(enabled)
    logger.info(
        f"[watchlist] 开始采集 {total} 只标的（{mode_label}, 维度层级={tier_label}, "
        f"并发={_WATCHLIST_CONCURRENCY}: nav={nav_period}, kline={kline_len}, flow={flow_days}d）"
    )

    results = []
    t0 = _time.time()
    sem = _asyncio.Semaphore(_WATCHLIST_CONCURRENCY)
    completed_count = [0]  # 用列表实现闭包内可变计数器

    async def _collect_one(item) -> list[dict]:
        code = item["source_name"]
        config = item.get("config", {})
        item_type = config.get("type", "fund")
        code_results = []

        async with sem:
            try:
                if item_type == "fund":
                    from datetime import timedelta as _td
                    dim_tasks = {}
                    # ── 高频（每次都采） ──
                    dim_tasks["nav"]          = ths_client.get_nav_trend(code, period=nav_period)
                    dim_tasks["realtime"]     = ths_client.get_realtime_trend(code)
                    dim_tasks["nav_technical"] = ths_client.get_nav_technical(code)
                    # ── 日频（每天最多采 1 次） ──
                    if collect_daily:
                        dim_tasks["performance"]       = ths_client.get_performance_rank(code)
                        dim_tasks["flow_trend"]        = ths_client.get_fund_flow_trend(code)
                        dim_tasks["periodic_rate"]     = ths_client.get_periodic_rate(code)
                        dim_tasks["profit_contribution"] = ths_client.get_profit_contribution(code)
                        # nav_sina 增量：用 since 作为 date_from，拉新数据
                        if sina_client:
                            nav_since = since or (today - _td(days=365)).isoformat()
                            dim_tasks["nav_sina"] = sina_client.get_fund_nav(
                                code, date_from=nav_since, date_to=today.isoformat()
                            )
                    # ── 低频（每周最多采 1 次） ──
                    if collect_low:
                        dim_tasks["holdings"]         = ths_client.get_top10_holdings(code)
                        dim_tasks["scale"]            = ths_client.get_scale_change(code)
                        dim_tasks["holder_ratio"]     = ths_client.get_holder_ratio(code)
                        dim_tasks["dividend"]         = ths_client.get_dividend_history(code)
                        dim_tasks["year_return"]      = ths_client.get_year_return(code)
                        dim_tasks["max_drawdown"]     = ths_client.get_max_drawdown(code)
                        dim_tasks["holding_overview"] = ths_client.get_holding_overview(code)
                        dim_tasks["fund_detail"]      = ths_client.get_fund_detail(code)
                        dim_tasks["style_preference"] = ths_client.get_style_preference(code)
                        dim_tasks["asset_allocation"] = ths_client.get_asset_allocation(code)
                        dim_tasks["position_detail"]  = ths_client.get_position_detail(code)
                        dim_tasks["trade_rule"]       = ths_client.get_trade_rule(code)
                        # guba_posts（低频，fund 也可有股吧）
                        if em_client:
                            pure = code[2:] if code[:2] in ("sh", "sz", "bj") else code
                            dim_tasks["guba_posts"] = em_client.get_guba_posts(pure)

                    keys = list(dim_tasks.keys())
                    raw_results = await _asyncio.gather(*dim_tasks.values(), return_exceptions=True)

                    fund_detail_data = None
                    holdings_data = None
                    for dim, raw in zip(keys, raw_results):
                        if isinstance(raw, Exception):
                            logger.debug(f"[watchlist:{code}] {dim} 失败: {raw}")
                            continue
                        data = raw.get("data", raw) if isinstance(raw, dict) else raw
                        if dim == "fund_detail":
                            fund_detail_data = data
                        if dim == "holdings":
                            holdings_data = data
                        code_results.extend(_expand_timeseries(code, dim, data, today, since))

                    # 二阶段（低频）：依赖第一阶段结果的请求
                    if collect_low:
                        phase2_tasks = {}

                        # manager_info：从 fund_detail 提取 manager_id
                        if fund_detail_data and isinstance(fund_detail_data, dict):
                            mgr_list = fund_detail_data.get("managerInfo") or []
                            mgr_ids = [m["id"] for m in mgr_list if isinstance(m, dict) and m.get("id")]
                            for mid in mgr_ids:
                                phase2_tasks[f"manager_info:{mid}"] = ths_client.get_manager_info(code, mid)

                        # hk_quote / us_quote / hk_us_kline：从 holdings 提取港美股代码
                        if holdings_data and isinstance(holdings_data, dict):
                            stocks = holdings_data.get("stock") or []
                            hk_codes = [s["secCode"] for s in stocks
                                        if isinstance(s, dict) and s.get("marketType") == "HK" and s.get("secCode")]
                            us_codes = [s["secCode"] for s in stocks
                                        if isinstance(s, dict) and s.get("marketType") == "US" and s.get("secCode")]
                            if hk_codes:
                                phase2_tasks["hk_quote"] = tencent_client.get_hk_quote(hk_codes)
                            if us_codes:
                                phase2_tasks["us_quote"] = tencent_client.get_us_quote(us_codes)
                            # hk_us_kline：前 5 只重仓股的日 K 线
                            for hk in hk_codes[:5]:
                                phase2_tasks[f"hk_us_kline:hk{hk}"] = tencent_client.get_hk_us_kline(
                                    f"hk{hk}", period="day", count=kline_len)
                            for us in us_codes[:5]:
                                phase2_tasks[f"hk_us_kline:us{us}.OQ"] = tencent_client.get_hk_us_kline(
                                    f"us{us}.OQ", period="day", count=kline_len)

                        if phase2_tasks:
                            p2_keys = list(phase2_tasks.keys())
                            p2_results = await _asyncio.gather(*phase2_tasks.values(), return_exceptions=True)

                            all_managers = []
                            all_kline_sources = []
                            for p2_key, raw in zip(p2_keys, p2_results):
                                if isinstance(raw, Exception):
                                    logger.debug(f"[watchlist:{code}] {p2_key} 失败: {raw}")
                                    continue
                                data = raw.get("data", raw) if isinstance(raw, dict) else raw

                                if p2_key.startswith("manager_info:"):
                                    if isinstance(data, list):
                                        all_managers.extend(data)
                                    elif data:
                                        all_managers.append(data)
                                elif p2_key in ("hk_quote", "us_quote"):
                                    code_results.extend(
                                        _expand_timeseries(code, p2_key, data, today, since)
                                    )
                                elif p2_key.startswith("hk_us_kline:"):
                                    if isinstance(data, dict):
                                        all_kline_sources.append(data)

                            if all_managers:
                                code_results.extend(
                                    _expand_timeseries(code, "manager_info", all_managers, today, since)
                                )
                            if all_kline_sources:
                                code_results.extend(
                                    _expand_timeseries(code, "hk_us_kline", all_kline_sources, today, since)
                                )

                    # 场内基金追加个股维度（高频：每次都采）
                    if _is_exchange_traded(code):
                        # 深交所：15xxxx ETF / 16xxxx LOF → sz
                        # 上交所：510~518xxx ETF → sh
                        prefix = "sz" if code.startswith(("15", "16")) else "sh"
                        full_code = f"{prefix}{code}"
                        stock_tasks = {
                            "stock_flow":  tencent_client.get_stock_fund_flow(full_code, history_days=flow_days),
                            "quote":       tencent_client.get_stock_quote([full_code]),
                            "minute_data": tencent_client.get_minute_data(full_code),
                        }
                        if sina_client:
                            stock_tasks["kline"] = sina_client.get_kline(full_code, scale=240, datalen=kline_len)

                        stock_keys = list(stock_tasks.keys())
                        stock_results = await _asyncio.gather(*stock_tasks.values(), return_exceptions=True)
                        for dim, raw in zip(stock_keys, stock_results):
                            if isinstance(raw, Exception):
                                logger.debug(f"[watchlist:{code}] {dim} 失败: {raw}")
                                continue
                            data = raw.get("data", raw) if isinstance(raw, dict) else raw
                            code_results.extend(_expand_timeseries(code, dim, data, today, since))

                elif item_type == "stock":
                    stock_tasks = {
                        "stock_flow":  tencent_client.get_stock_fund_flow(code, history_days=flow_days),
                        "quote":       tencent_client.get_stock_quote([code]),
                        "minute_data": tencent_client.get_minute_data(code),
                    }
                    if sina_client:
                        stock_tasks["kline"] = sina_client.get_kline(code, scale=240, datalen=kline_len)
                    if em_client:
                        pure_code = code[2:] if code[:2] in ("sh", "sz", "bj") else code
                        stock_tasks["valuation"] = em_client.get_stock_valuation_history(pure_code, years=1)
                        if collect_low:
                            stock_tasks["guba_posts"] = em_client.get_guba_posts(pure_code)
                    # 个股所属板块（低频）
                    if collect_low:
                        stock_tasks["plates"] = tencent_client.get_stock_plates(code)

                    stock_keys = list(stock_tasks.keys())
                    stock_results = await _asyncio.gather(*stock_tasks.values(), return_exceptions=True)
                    for dim, raw in zip(stock_keys, stock_results):
                        if isinstance(raw, Exception):
                            logger.debug(f"[watchlist:{code}] {dim} 失败: {raw}")
                            continue
                        data = raw.get("data", raw) if isinstance(raw, dict) else raw
                        code_results.extend(_expand_timeseries(code, dim, data, today, since))

                if code_results:
                    checkpoint_store.update_success("watchlist", code, {
                        "mode": "incremental",
                        "newest_time": today.isoformat(),
                    }, saved_count=len(code_results))

            except Exception as e:
                logger.warning(f"[watchlist:{code}] 采集异常: {e}")

        # 实时进度（在 sem 释放后更新，避免被锁影响日志顺序）
        completed_count[0] += 1
        idx = completed_count[0]
        if idx % 10 == 0 or idx == total:
            elapsed = _time.time() - t0
            done_items = sum(len(r) for r in [code_results])  # 本只
            logger.info(
                f"[watchlist] 进度 {idx}/{total} ({idx*100//total}%), "
                f"本只 {len(code_results)} 条, 耗时 {elapsed:.0f}s"
            )

        return code_results

    # 并发采集所有标的
    all_code_results = await _asyncio.gather(
        *[_collect_one(item) for item in enabled],
        return_exceptions=True,
    )
    for r in all_code_results:
        if isinstance(r, list):
            results.extend(r)
        elif isinstance(r, Exception):
            logger.warning(f"[watchlist] 标的采集异常: {r}")

    # ── QDII 关联：全市场级数据（只采一次，code='_market'） ──
    try:
        market_tasks = {}
        if sina_client:
            market_tasks["global_index"] = sina_client.get_global_index()
            market_tasks["forex"]        = sina_client.get_forex()
        if tencent_client:
            market_tasks["intl_futures"] = tencent_client.get_intl_futures()
        if em_client:
            if since:
                market_tasks["research"] = em_client.get_research_reports_since(since)
            else:
                market_tasks["research"] = em_client.get_research_reports(page_size=50)

        if market_tasks:
            mkeys = list(market_tasks.keys())
            mresults = await _asyncio.gather(*market_tasks.values(), return_exceptions=True)
            for dim, raw in zip(mkeys, mresults):
                if isinstance(raw, Exception):
                    logger.debug(f"[watchlist:_market] {dim} 失败: {raw}")
                    continue
                data = raw.get("data", raw) if isinstance(raw, dict) else raw
                if not data:
                    continue
                if dim == "research":
                    store_data = {"count": len(data), "reports": data}
                else:
                    store_data = data
                results.append({
                    "code": "_market", "data_type": dim,
                    "trade_date": today, "data": store_data,
                })
    except Exception as e:
        logger.warning(f"[watchlist:_market] QDII 关联采集异常: {e}")

    elapsed_total = _time.time() - t0
    if results:
        logger.info(
            f"[watchlist] 采集完成: {len(enabled)} 只标的, {len(results)} 条数据, "
            f"总耗时 {elapsed_total:.0f}s"
        )

    return results


def _normalize_watchlist_data(raw_items: list) -> list[dict]:
    """watchlist 数据不需要额外 normalize，直接透传"""
    return raw_items if isinstance(raw_items, list) else []


def normalize_dragon_tiger_em(raw) -> list[dict]:
    """东方财富龙虎榜 → 统一格式

    get_dragon_tiger 返回: {data:{tab:"stock", items:[{code, date, name, close, buyAmt, sellAmt,
                           netAmt, reason, explain, changeRate, turnoverRate, ...}]}}
    """
    items_list = []
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items_list = data.get("items") or data.get("data") or []
        elif isinstance(data, list):
            items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        trade_date = (item.get("date") or item.get("TRADE_DATE") or "")[:10] or _today()
        results.append({
            "data_type": "dragon_tiger",
            "trade_date": trade_date,
            "data": {
                "source": "eastmoney",
                "code": item.get("code"),
                "name": item.get("name"),
                "close": item.get("close"),
                "change_rate": item.get("changeRate"),
                "turnover_rate": item.get("turnoverRate"),
                "buy_amt": item.get("buyAmt"),
                "sell_amt": item.get("sellAmt"),
                "net_amt": item.get("netAmt"),
                "reason": item.get("reason"),
                "explain": item.get("explain"),
            },
        })
    return results


def normalize_dragon_tiger_ths(raw) -> list[dict]:
    """同花顺龙虎榜 → 统一格式

    get_ths_dragon_tiger 返回: {data:{tab:"youzi", date:"...", items:[{code, name, chg, days, price,
                               seats:[{dept, side, buy, sell, net, label}]}]}}
    """
    items_list = []
    trade_date = _today()
    if isinstance(raw, dict):
        data = raw.get("data", {})
        if isinstance(data, dict):
            items_list = data.get("items") or data.get("list") or []
            trade_date = (data.get("date") or trade_date)[:10]
        elif isinstance(data, list):
            items_list = data
    elif isinstance(raw, list):
        items_list = raw

    results = []
    for item in items_list:
        if not isinstance(item, dict):
            continue
        results.append({
            "data_type": "dragon_tiger",
            "trade_date": trade_date,
            "data": {
                "source": "ths",
                "code": item.get("code"),
                "name": item.get("name"),
                "price": item.get("price"),
                "chg": item.get("chg"),
                "days": item.get("days"),
                "seats": item.get("seats", []),
            },
        })
    return results


# ==================== 聚合器 ====================


class FundFlowAggregator(BaseAggregator):
    """资金流聚合

    6 个数据源，统一采集到 ft_market_flow。
    """

    data_domain = "fund_flow"
    task_interval = 300  # 5 分钟

    # 源名称 → 调度配置
    # 注意：源名称 ≠ data_type，一个源可能写多种 data_type
    # 完整映射见文件顶部 DATA_TYPE 注册表
    SOURCE_CONFIGS = {
        "northbound":       {"target_days": 60, "page_size": 60, "interval": 600},           # → data_type: northbound
        "sector_flow_sina": {"target_days": 0, "page_size": 50, "interval": 1800, "default_mode": "incremental"},  # → data_type: sector_flow
        "dragon_tiger_em":  {"target_days": 0, "page_size": 50, "interval": 21600, "default_mode": "incremental"}, # → data_type: dragon_tiger
        "dragon_tiger_ths": {"target_days": 0, "page_size": 50, "interval": 21600, "default_mode": "incremental"}, # → data_type: dragon_tiger
        "watchlist_data":   {"target_days": 0, "interval": 1800, "default_mode": "incremental"},                   # → ft_watchlist_data 20种 data_type
    }

    def __init__(self):
        super().__init__()
        self._init_sources()

    def _init_sources(self):
        from src.infrastructure import clients

        # checkpoint 是 dict（来自 ft_collection_state）
        # cp == {} → 首次拉取（全量）
        # cp == {"max_trade_date": "..."} → 增量

        def _northbound_fetch(cp):
            # 首次：拉 60 天历史；增量：拉 7 天即可（防漏掉补录）
            is_first = not cp or not cp.get("max_trade_date")
            page_size = 60 if is_first else 7
            return clients.eastmoney.get_northbound_recent(page_size=page_size)

        self.sources = [
            # 北向资金 — 10 分钟
            SourceDef(
                "northbound",
                _northbound_fetch,
                600,
                normalize_northbound,
            ),
            # 板块资金流（新浪，含超大/大/中/小单分项）— 30 分钟
            # 新浪接口只有当前快照，无历史端点，靠 cron 持续累积
            SourceDef(
                "sector_flow_sina",
                lambda cp: clients.sina.get_sector_money_flow(),
                1800,
                normalize_sector_flow_sina,
            ),
            # 龙虎榜 — 东方财富，盘后（6 小时间隔）
            SourceDef(
                "dragon_tiger_em",
                lambda cp: clients.eastmoney.get_dragon_tiger(),
                21600,
                normalize_dragon_tiger_em,
            ),
            # 龙虎榜 — 同花顺，盘后（6 小时间隔）
            SourceDef(
                "dragon_tiger_ths",
                lambda cp: clients.ths.get_ths_dragon_tiger(),
                21600,
                normalize_dragon_tiger_ths,
            ),
            # 自选标的数据采集 — watchlist 驱动，30 分钟
            SourceDef(
                "watchlist_data",
                lambda cp: _fetch_watchlist_data(
                    clients.ths, clients.tencent, clients.sina, clients.eastmoney, cp,
                ),
                1800,
                _normalize_watchlist_data,
            ),
        ]

    # checkpoint 现在统一由 base.py 的 checkpoint_store 管理
    # 子类无需再实现 _get_checkpoint
    # _compute_checkpoint 用 base.py 的默认实现（取 items 最大 trade_date）即可

    # ==================== 入库 ====================

    def _save(self, items: list[dict]) -> int:
        """路由写入：有 code 字段 → ft_watchlist_data，否则 → ft_market_flow"""
        if not items:
            return 0

        watchlist_items = []
        market_items = []

        for item in items:
            data = item.get("data")
            if data is None or (isinstance(data, (dict, list, str)) and len(data) == 0):
                continue

            if item.get("code"):
                # watchlist 数据 → ft_watchlist_data
                watchlist_items.append({
                    "code": item["code"],
                    "data_type": item["data_type"],
                    "trade_date": item.get("trade_date"),
                    "data": data,
                })
            else:
                # 全市场数据 → ft_market_flow
                if not item.get("data_type") or not item.get("trade_date"):
                    continue
                market_items.append({
                    "data_type": item["data_type"],
                    "trade_date": item["trade_date"],
                    "data": data,
                })

        import time as _time
        saved = 0
        if market_items:
            from src.infrastructure.persistence.repositories import MarketFlowRepositoryImpl
            saved += MarketFlowRepositoryImpl().upsert_batch(market_items)
        if watchlist_items:
            from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import WatchlistDataRepositoryImpl
            t0 = _time.time()
            logger.info(f"[watchlist] 开始入库 {len(watchlist_items)} 条")
            saved += WatchlistDataRepositoryImpl().save_batch(watchlist_items)
            logger.info(f"[watchlist] 入库完成，耗时 {_time.time()-t0:.1f}s")

        return saved

    # ==================== 查询 ====================

    async def query(
        self,
        data_type: str | None = None,
        trade_date: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        conditions = []
        values = []
        if data_type:
            conditions.append("data_type = %s")
            values.append(data_type)
        if trade_date:
            conditions.append("trade_date = %s")
            values.append(trade_date)
        return self._query_table(
            "ft_market_flow",
            conditions=conditions or None,
            values=values or None,
            order_by="created_at DESC",
            limit=limit,
        )

    # ==================== 便捷方法 ====================

    async def get_latest_northbound(self) -> list[dict]:
        """获取最新北向资金数据"""
        return await self.query(data_type="northbound", limit=5)

    async def get_sector_flow(self, trade_date: str | None = None) -> list[dict]:
        """获取板块资金流"""
        return await self.query(data_type="sector_flow", trade_date=trade_date or _today())

    async def get_dragon_tiger(self, trade_date: str | None = None) -> list[dict]:
        """获取龙虎榜"""
        return await self.query(data_type="dragon_tiger", trade_date=trade_date or _today())
