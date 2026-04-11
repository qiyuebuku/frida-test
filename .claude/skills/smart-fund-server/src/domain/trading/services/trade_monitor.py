"""持仓监控 (Phase C.4)

职责：
1. 硬止损：profit_pct <= -3% → 写卖出决策
2. 事件流衰退监控：持仓基金对应的 event_stream 进入 decaying/closed → 写卖出决策
3. 加仓信号：profit > 2% AND 对应 stream momentum=rising → 写加仓决策

⚠️ 本模块**不直接执行交易**，只生成 ft_pending_decisions（由 trade_execution 处理）。

持仓数据源：
- LIVE 模式：从 ft_positions 读
- DRY_RUN 模式：从 ft_trades(dry_run=true) 聚合虚拟持仓
"""

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)


# ==================== 配置 ====================

STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "-3.0"))
TAKE_PROFIT_ADD_PCT = float(os.environ.get("TAKE_PROFIT_ADD_PCT", "2.0"))
ADD_POSITION_AMOUNT = float(os.environ.get("ADD_POSITION_AMOUNT", "300"))
SELL_PCT_ON_DECAY = float(os.environ.get("SELL_PCT_ON_DECAY", "50"))   # 衰退时卖一半
SELL_PCT_ON_STOP_LOSS = float(os.environ.get("SELL_PCT_ON_STOP_LOSS", "100"))  # 止损全平


# ==================== 持仓查询 ====================


def _get_paper_positions() -> list[dict]:
    """从 ft_trades dry_run 聚合虚拟持仓 — R2.8 走 TradeRepository"""
    from src.infrastructure.persistence.repositories import TradeRepositoryImpl
    return TradeRepositoryImpl().paper_positions()


def _get_live_positions() -> list[dict]:
    """从 ft_positions 读真实持仓 — R2.8 走 PositionRepository"""
    from src.infrastructure.persistence.repositories import PositionRepositoryImpl
    return PositionRepositoryImpl().find_live_positions()


def _get_all_positions() -> list[dict]:
    """合并 live + paper 持仓（live 优先）"""
    live = _get_live_positions()
    live_codes = {p["fund_code"] for p in live}
    paper = [p for p in _get_paper_positions() if p["fund_code"] not in live_codes]
    return live + paper


# ==================== 反查事件流 ====================


def _fund_to_industries(fund_code: str) -> list[str]:
    """fund_code 反查所有行业关键词 — R2.8 走 IndustryMappingRepository"""
    from src.infrastructure.persistence.repositories import (
        IndustryMappingRepositoryImpl,
    )
    return IndustryMappingRepositoryImpl().fund_to_industry_keywords(fund_code)


def _latest_stream_state(industries: list[str]) -> dict | None:
    """取这些 industry 中最新的 stream — R2.8 走 EventStreamRepository"""
    from src.infrastructure.persistence.repositories import EventStreamRepositoryImpl
    return EventStreamRepositoryImpl().find_latest_by_industries(industries)


# ==================== 决策写入 ====================


def _create_monitor_decision(
    fund_code: str,
    fund_name: str,
    action: str,
    amount: float,
    sell_pct: float | None,
    reason: str,
    related_stream_id: int | None,
    risk_notes: str = "",
) -> bool:
    """监控触发的决策 — R2.8 走 PendingDecisionRepository.insert_monitor_decision"""
    from src.infrastructure.persistence.repositories import (
        PendingDecisionRepositoryImpl,
    )
    return PendingDecisionRepositoryImpl().insert_monitor_decision({
        "fund_code": fund_code,
        "fund_name": fund_name,
        "action": action,
        "amount": amount,
        "sell_pct": sell_pct,
        "reason": reason,
        "risk_notes": risk_notes,
        "related_stream_id": related_stream_id,
    })


# ==================== 主流程 ====================


class TradeMonitor:
    """持仓监控器"""

    async def monitor_all(self) -> dict:
        positions = _get_all_positions()
        if not positions:
            logger.info("[trade_monitor] 无持仓可监控")
            return {"positions": 0, "stop_loss": 0, "decay_sell": 0, "add_position": 0}

        logger.info(f"[trade_monitor] 监控 {len(positions)} 个持仓")

        stop_loss_count = 0
        decay_sell_count = 0
        add_count = 0

        for p in positions:
            fund_code = p["fund_code"]
            fund_name = p.get("fund_name") or ""
            profit_pct = float(p.get("profit_pct") or 0)

            # ── 1. 硬止损 ──
            if profit_pct <= STOP_LOSS_PCT:
                ok = _create_monitor_decision(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    action="sell",
                    amount=0,
                    sell_pct=SELL_PCT_ON_STOP_LOSS,
                    reason=f"硬止损触发: profit_pct={profit_pct:.2f}% <= {STOP_LOSS_PCT}%",
                    related_stream_id=None,
                    risk_notes=f"损失 {profit_pct:.2f}%，硬约束清仓",
                )
                if ok:
                    stop_loss_count += 1
                    logger.warning(f"[trade_monitor] 🛑 STOP_LOSS {fund_code} ({profit_pct:.2f}%)")
                continue  # 止损后不再判断其他

            # ── 2. 反查事件流 ──
            industries = _fund_to_industries(fund_code)
            stream = _latest_stream_state(industries) if industries else None

            # ── 3. 事件流衰退 → 卖出 ──
            if stream and stream["state"] in ("decaying", "closed"):
                ok = _create_monitor_decision(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    action="sell",
                    amount=0,
                    sell_pct=SELL_PCT_ON_DECAY,
                    reason=(
                        f"事件流衰退: stream#{stream['id']} ({stream['industry']}) "
                        f"state={stream['state']} momentum={stream['momentum']}"
                    ),
                    related_stream_id=stream["id"],
                    risk_notes=f"行业 {stream['industry']} 已 {stream['state']}",
                )
                if ok:
                    decay_sell_count += 1
                    logger.info(
                        f"[trade_monitor] 📉 DECAY_SELL {fund_code} "
                        f"({stream['industry']} {stream['state']})"
                    )
                continue

            # ── 4. 浮盈加仓 ──
            if (
                profit_pct >= TAKE_PROFIT_ADD_PCT
                and stream
                and stream["state"] in ("fermenting", "peak")
                and stream["momentum"] == "rising"
            ):
                ok = _create_monitor_decision(
                    fund_code=fund_code,
                    fund_name=fund_name,
                    action="buy",
                    amount=ADD_POSITION_AMOUNT,
                    sell_pct=None,
                    reason=(
                        f"浮盈加仓: profit={profit_pct:.2f}% + "
                        f"stream#{stream['id']} {stream['state']}/{stream['momentum']}"
                    ),
                    related_stream_id=stream["id"],
                    risk_notes=f"事件流仍在发酵, momentum=rising",
                )
                if ok:
                    add_count += 1
                    logger.info(
                        f"[trade_monitor] ➕ ADD {fund_code} +{ADD_POSITION_AMOUNT} "
                        f"({profit_pct:.2f}%)"
                    )

        logger.info(
            f"[trade_monitor] 完成: 持仓 {len(positions)}, 止损 {stop_loss_count}, "
            f"衰退卖出 {decay_sell_count}, 加仓 {add_count}"
        )
        return {
            "positions": len(positions),
            "stop_loss": stop_loss_count,
            "decay_sell": decay_sell_count,
            "add_position": add_count,
        }
