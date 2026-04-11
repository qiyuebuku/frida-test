"""复盘引擎 (Phase C.5)

职责：
1. 扫描 T-1 / T-2 / T-3 的交易（dry_run + live 都复盘）
2. 通过 ETF 代码调 eastmoney get_stock_kline 拿 T+1 / T+2 收盘价
3. 计算 change_t1_pct / change_t2_pct
4. 判断 outcome (correct / wrong / neutral)
5. 写 ft_reviews（已存在的更新）
6. 更新胜率统计供 trade_decision 用（写入 ft_config 或独立表）

注意：
- ETF 代码就是股票代码（如 159995/515030），可直接调 get_stock_kline
- 沪市 ETF 5 开头 → secid 1.{code}, 深市 1 开头 → secid 0.{code}
- get_stock_kline 默认 period='101'(日K)，limit=60
"""

import logging
import os
from datetime import date, datetime, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


# ==================== 配置 ====================

# 复盘几天前的交易
REVIEW_LOOKBACK_DAYS = int(os.environ.get("REVIEW_LOOKBACK_DAYS", "5"))
# T+N 天后做最终判断
EVAL_WINDOW_DAYS = 2
# 判断 buy 是 correct 的最低收益（%）
BUY_CORRECT_THRESHOLD = float(os.environ.get("BUY_CORRECT_THRESHOLD", "0.5"))
BUY_WRONG_THRESHOLD = float(os.environ.get("BUY_WRONG_THRESHOLD", "-1.0"))


# ==================== 数据查询 ====================


def _query_unreviewed_trades(lookback_days: int = REVIEW_LOOKBACK_DAYS) -> list[dict]:
    """读 lookback 天内、未复盘的交易 — R2.9 走 ReviewRepository"""
    from src.infrastructure.persistence.repositories import ReviewRepositoryImpl
    return ReviewRepositoryImpl().find_unreviewed_trades(lookback_days=lookback_days)


# ==================== 净值查询 ====================


def _etf_sina_symbol(fund_code: str) -> str:
    """ETF 代码 → sina symbol（sh / sz 前缀）

    沪市 ETF: 5xxxxx → sh
    深市 ETF: 1xxxxx → sz（包括 159xxx, 11xxxx）
    沪股: 6/9 → sh
    深股: 0/3 → sz
    """
    code = fund_code.strip()
    if code.startswith(("5", "6", "9")):
        return f"sh{code}"
    return f"sz{code}"


async def _get_etf_close_prices(fund_code: str, days_back: int = 10) -> dict[str, float]:
    """通过 sina get_kline 拿 ETF/股票 历史收盘价

    sina 接口稳定，国内访问无障碍。
    Returns: {date_str: close_price}
    """
    try:
        from src.infrastructure import clients
        sina = getattr(clients, "sina", None)
        if not sina:
            return {}
        symbol = _etf_sina_symbol(fund_code)
        # scale=240 (日线), datalen=days_back
        result = await sina.get_kline(symbol, scale=240, datalen=days_back)
        bars = (result.get("data") or {}).get("bars") or []
        out: dict[str, float] = {}
        for bar in bars:
            d = (bar.get("date") or "")[:10]
            close = bar.get("close")
            if d and close is not None:
                try:
                    out[d] = float(close)
                except (ValueError, TypeError):
                    pass
        return out
    except Exception as e:
        logger.debug(f"取 {fund_code} K 线失败: {e}")
        return {}


# ==================== outcome 判断 ====================


def _judge_outcome(action: str, change_t1: float | None, change_t2: float | None) -> str:
    """根据 T+1/T+2 涨跌判断决策对错

    Args:
        action: buy / sell / hold
        change_t1: T+1 收益率(%)
        change_t2: T+2 收益率(%)

    Returns: correct / wrong / neutral / pending
    """
    # 取 T+2 优先，没有则 T+1
    final_change = change_t2 if change_t2 is not None else change_t1
    if final_change is None:
        return "pending"

    if action == "buy":
        if final_change >= BUY_CORRECT_THRESHOLD:
            return "correct"
        if final_change <= BUY_WRONG_THRESHOLD:
            return "wrong"
        return "neutral"
    if action == "sell":
        # 卖出：跌了算对（避开了下跌）
        if final_change <= -BUY_CORRECT_THRESHOLD:
            return "correct"
        if final_change >= -BUY_WRONG_THRESHOLD:
            return "wrong"
        return "neutral"
    return "neutral"


# ==================== 复盘写入 ====================


def _save_review(review: dict) -> bool:
    """R2.9 走 ReviewRepository"""
    from src.infrastructure.persistence.repositories import ReviewRepositoryImpl
    return ReviewRepositoryImpl().upsert_review(review)


# ==================== 胜率统计 ====================


def _refresh_winrate_stats() -> dict:
    """近 30 天 event_driven 胜率统计 + 写 ft_config — R2.9 走 ReviewRepository"""
    from src.infrastructure.persistence.repositories import ReviewRepositoryImpl
    stats = ReviewRepositoryImpl().winrate_stats_30d()
    # 写入 ft_config
    try:
        from src.infrastructure.db import fund_db
        fund_db.set_config("event_driven_winrate", stats)
    except Exception as e:
        logger.debug(f"写胜率到 ft_config 失败: {e}")
    return stats


# ==================== 主流程 ====================


class ReviewEngine:
    """复盘引擎"""

    async def review_all(self) -> dict:
        trades = _query_unreviewed_trades()
        if not trades:
            logger.info("[review_decision] 无待复盘交易")
            stats = _refresh_winrate_stats()
            return {"trades": 0, "winrate_stats": stats}

        logger.info(f"[review_decision] 待复盘 {len(trades)} 笔交易")

        # 按 fund_code 分组减少 K 线请求
        by_fund: dict[str, list[dict]] = {}
        for t in trades:
            by_fund.setdefault(t["fund_code"], []).append(t)

        reviewed = 0
        skipped = 0
        for fund_code, fund_trades in by_fund.items():
            klines = await _get_etf_close_prices(fund_code, days_back=10)
            if not klines:
                logger.debug(f"[review_decision] {fund_code} 无 K 线数据，跳过")
                skipped += len(fund_trades)
                continue

            for t in fund_trades:
                trade_date = t["trade_date"].isoformat() if hasattr(t["trade_date"], "isoformat") else str(t["trade_date"])
                # 取 trade_date 当日及之后的价格
                later_dates = sorted(d for d in klines if d > trade_date)
                if not later_dates:
                    skipped += 1
                    continue

                nav_at = klines.get(trade_date)
                # 找最近的"当天或更早"价格作为 nav_at
                if nav_at is None:
                    earlier = sorted(d for d in klines if d <= trade_date)
                    if earlier:
                        nav_at = klines[earlier[-1]]

                if nav_at is None:
                    skipped += 1
                    continue

                t1_date = later_dates[0] if later_dates else None
                t2_date = later_dates[1] if len(later_dates) > 1 else None
                nav_t1 = klines.get(t1_date) if t1_date else None
                nav_t2 = klines.get(t2_date) if t2_date else None

                change_t1 = ((nav_t1 - nav_at) / nav_at * 100) if nav_t1 else None
                change_t2 = ((nav_t2 - nav_at) / nav_at * 100) if nav_t2 else None
                outcome = _judge_outcome(t["action"], change_t1, change_t2)

                review = {
                    "decision_id": t["id"],  # 这里把 trade.id 当作 decision_id 写入
                    "fund_code": fund_code,
                    "decision_date": t["trade_date"],
                    "decision_action": t["action"],
                    "decision_reason": (t.get("reason") or "")[:500],
                    "nav_at_decision": round(nav_at, 4),
                    "nav_t1": round(nav_t1, 4) if nav_t1 else None,
                    "nav_t2": round(nav_t2, 4) if nav_t2 else None,
                    "change_t1_pct": round(change_t1, 4) if change_t1 is not None else None,
                    "change_t2_pct": round(change_t2, 4) if change_t2 is not None else None,
                    "outcome": outcome,
                    "review_notes": f"自动复盘 trade#{t['id']} ({'dry_run' if t['dry_run'] else 'live'})",
                    "decision_source": "event_driven",
                    "dry_run": t["dry_run"],
                }
                if _save_review(review):
                    reviewed += 1
                else:
                    skipped += 1

        # 刷新胜率
        stats = _refresh_winrate_stats()

        logger.info(
            f"[review_decision] 完成: 复盘 {reviewed}, 跳过 {skipped}, "
            f"胜率 {stats['winrate']:.1%} ({stats['correct']}/{stats['total']})"
        )
        return {"trades": len(trades), "reviewed": reviewed, "skipped": skipped, "winrate_stats": stats}
