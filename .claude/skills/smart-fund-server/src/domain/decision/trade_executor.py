"""交易执行器 (Phase C.3)

⚠️⚠️⚠️ 安全说明 ⚠️⚠️⚠️

本模块是事件驱动决策的执行端，会调用天天基金 buy_fund/sell_fund 真实下单。

**多重安全保护**：
1. EXEC_DRY_RUN 默认 True（环境变量 EXEC_DRY_RUN=false 才能开启 live）
2. 单个 decision 的 dry_run 字段也必须是 false 才会真实执行
3. 单笔金额硬上限 MAX_LIVE_AMOUNT
4. 单日总额硬上限 MAX_DAILY_LIVE_AMOUNT
5. 必须在白名单 LIVE_FUND_WHITELIST 中（防止误买非熟悉基金）

任何一层条件不满足 → 强制走 dry_run 模拟。

dry_run 模式：
- 不调 buy_fund / sell_fund
- 写 ft_trades(dry_run=True, order_no='DRY_RUN_xxx')
- 不更新 ft_positions
- 标记 ft_pending_decisions.status='executed'

live 模式（真实下单）：
- 调 buy_fund / sell_fund
- 写 ft_trades(dry_run=False, order_no=真实订单号)
- 更新 ft_positions
- 标记 ft_pending_decisions.status='executed' + executed_at
"""

import json
import logging
import os
from datetime import date, datetime
from decimal import Decimal

import psycopg2
import psycopg2.extras

from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)


# ==================== 安全配置 ====================

# 默认 True，必须显式 EXEC_DRY_RUN=false 才能开启 live
EXEC_DRY_RUN = os.environ.get("EXEC_DRY_RUN", "true").lower() != "false"

# Live 模式下的硬上限
MAX_LIVE_AMOUNT = float(os.environ.get("MAX_LIVE_AMOUNT", "1000"))    # 单笔最大 1000 元
MAX_DAILY_LIVE_AMOUNT = float(os.environ.get("MAX_DAILY_LIVE_AMOUNT", "3000"))  # 单日 3000

# Live 模式基金白名单（用 , 分隔），空 = 全部禁止 live
LIVE_FUND_WHITELIST = set(
    f.strip() for f in os.environ.get("LIVE_FUND_WHITELIST", "").split(",") if f.strip()
)

# 单次 tick 处理多少决策
MAX_DECISIONS_PER_TICK = 20


# ==================== 数据库 ====================


def _query_pending_decisions(limit: int = MAX_DECISIONS_PER_TICK) -> list[dict]:
    """读 pending 状态的事件驱动决策"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, fund_code, fund_name, action, amount, sell_pct,
                       reason, score, dry_run, event_stream_id, source_event_ids
                FROM ft_pending_decisions
                WHERE status = 'pending'
                  AND decision_source = 'event_driven'
                  AND action IN ('buy', 'sell')
                  AND decision_date = CURRENT_DATE
                ORDER BY score DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]


def _today_live_buy_total() -> float:
    """今日已实际买入总额（dry_run=false）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(amount), 0)
                FROM ft_trades
                WHERE trade_date = CURRENT_DATE
                  AND action = 'buy'
                  AND dry_run = FALSE
                """
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0


def _save_trade(
    decision_id: int,
    fund_code: str,
    fund_name: str,
    action: str,
    amount: float,
    shares: float | None,
    order_no: str,
    reason: str,
    api_response: dict | None,
    dry_run: bool,
) -> int | None:
    """写 ft_trades 并返回 trade_id"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ft_trades (
                        fund_code, fund_name, action, amount, shares,
                        order_no, reason, api_response, dry_run, decision_id
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    ) RETURNING id
                    """,
                    (
                        fund_code, fund_name, action, amount, shares,
                        order_no, reason,
                        json.dumps(api_response or {}, ensure_ascii=False, default=str),
                        dry_run, decision_id,
                    ),
                )
                trade_id = cur.fetchone()[0]
                conn.commit()
                return trade_id
    except Exception as e:
        logger.warning(f"写 ft_trades 失败: {e}")
        return None


def _mark_decision_executed(decision_id: int, order_no: str | None = None):
    """标记决策已执行"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ft_pending_decisions
                    SET status = 'executed',
                        executed_at = NOW()
                    WHERE id = %s
                    """,
                    (decision_id,),
                )
                conn.commit()
    except Exception as e:
        logger.warning(f"更新决策状态失败: {e}")


def _mark_decision_cancelled(decision_id: int, reason: str):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ft_pending_decisions
                    SET status = 'cancelled',
                        cancelled_at = NOW(),
                        cancel_reason = %s
                    WHERE id = %s
                    """,
                    (reason[:200], decision_id),
                )
                conn.commit()
    except Exception as e:
        logger.warning(f"取消决策失败: {e}")


def _update_position_after_buy(fund_code: str, fund_name: str, amount: float, shares: float):
    """实盘买入后更新 ft_positions（仅 live 模式调用）"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # 用 ON CONFLICT 增量更新（持仓累加）
                cur.execute(
                    """
                    INSERT INTO ft_positions (
                        fund_code, fund_name, total_cost, shares, avg_cost,
                        first_buy_date, add_count, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        CURRENT_DATE, 1, NOW()
                    )
                    ON CONFLICT (fund_code) DO UPDATE SET
                        total_cost = ft_positions.total_cost + EXCLUDED.total_cost,
                        shares = ft_positions.shares + EXCLUDED.shares,
                        avg_cost = (ft_positions.total_cost + EXCLUDED.total_cost) /
                                   NULLIF(ft_positions.shares + EXCLUDED.shares, 0),
                        add_count = ft_positions.add_count + 1,
                        updated_at = NOW()
                    """,
                    (
                        fund_code, fund_name, amount, shares,
                        amount / shares if shares > 0 else 0,
                    ),
                )
                conn.commit()
    except Exception as e:
        logger.warning(f"更新持仓失败: {e}")


# ==================== 安全闸 ====================


def _decide_dry_run(decision: dict) -> tuple[bool, str]:
    """判断这个决策是否应该走 dry_run

    Returns: (use_dry_run, 原因)
    """
    # 1. 全局开关
    if EXEC_DRY_RUN:
        return True, "EXEC_DRY_RUN=true (全局)"

    # 2. 决策本身要求 dry_run
    if decision.get("dry_run", True):
        return True, "decision.dry_run=true"

    # 3. 白名单
    fund_code = decision["fund_code"]
    if fund_code not in LIVE_FUND_WHITELIST:
        return True, f"{fund_code} 不在 LIVE_FUND_WHITELIST"

    # 4. 单笔上限
    amount = float(decision.get("amount") or 0)
    if amount > MAX_LIVE_AMOUNT:
        return True, f"amount {amount} > MAX_LIVE_AMOUNT {MAX_LIVE_AMOUNT}"

    # 5. 单日上限
    today_total = _today_live_buy_total()
    if today_total + amount > MAX_DAILY_LIVE_AMOUNT:
        return True, f"今日已下 {today_total}, 加本笔超 MAX_DAILY_LIVE_AMOUNT {MAX_DAILY_LIVE_AMOUNT}"

    return False, ""


# ==================== 执行 ====================


async def _execute_buy(decision: dict) -> dict:
    """执行买入

    根据 _decide_dry_run 走 dry_run 或真实下单。
    返回 {trade_id, dry_run, success, msg}
    """
    fund_code = decision["fund_code"]
    fund_name = decision["fund_name"] or ""
    amount = float(decision["amount"] or 0)
    decision_id = decision["id"]

    use_dry_run, dry_reason = _decide_dry_run(decision)

    if use_dry_run:
        # ── DRY_RUN 模拟 ──
        order_no = f"DRY_{decision_id}_{int(datetime.now().timestamp())}"
        trade_id = _save_trade(
            decision_id=decision_id,
            fund_code=fund_code,
            fund_name=fund_name,
            action="buy",
            amount=amount,
            shares=None,
            order_no=order_no,
            reason=f"[DRY_RUN: {dry_reason}] {decision.get('reason', '')}",
            api_response={"dry_run": True, "reason": dry_reason},
            dry_run=True,
        )
        _mark_decision_executed(decision_id, order_no)
        logger.info(
            f"[trade_execution] 💧 DRY_RUN buy {fund_code} {amount:.0f} (decision#{decision_id}, {dry_reason})"
        )
        return {"trade_id": trade_id, "dry_run": True, "success": True}

    # ── LIVE 真实下单 ──
    logger.warning(
        f"[trade_execution] 🔴 LIVE buy {fund_code} {amount:.0f} (decision#{decision_id})"
    )

    # 这里实际调用 client.buy_fund
    try:
        from src.infrastructure import clients
        if not getattr(clients, "tiantianjijin", None):
            raise RuntimeError("tiantianjijin client 未初始化")
        result = await clients.tiantianjijin.buy_fund(fund_code, amount)
        order_no = (
            result.get("appSheetSerialNo")
            or result.get("data", {}).get("appSheetSerialNo")
            or f"LIVE_{decision_id}"
        )
        shares = float(result.get("vol", 0)) or None
    except Exception as e:
        logger.error(f"[trade_execution] LIVE 下单失败 {fund_code}: {e}")
        _mark_decision_cancelled(decision_id, f"LIVE 下单异常: {e}")
        return {"dry_run": False, "success": False, "msg": str(e)}

    trade_id = _save_trade(
        decision_id=decision_id,
        fund_code=fund_code,
        fund_name=fund_name,
        action="buy",
        amount=amount,
        shares=shares,
        order_no=order_no,
        reason=decision.get("reason") or "",
        api_response=result,
        dry_run=False,
    )
    if shares:
        _update_position_after_buy(fund_code, fund_name, amount, shares)
    _mark_decision_executed(decision_id, order_no)
    return {"trade_id": trade_id, "dry_run": False, "success": True, "order_no": order_no}


async def _execute_sell(decision: dict) -> dict:
    """执行卖出（暂未实现 LIVE，全部走 dry_run）"""
    decision_id = decision["id"]
    fund_code = decision["fund_code"]
    sell_pct = float(decision.get("sell_pct") or 0)
    use_dry_run = True  # 卖出 LIVE 待 C.4 监控开通后再启用
    order_no = f"DRY_SELL_{decision_id}_{int(datetime.now().timestamp())}"
    trade_id = _save_trade(
        decision_id=decision_id,
        fund_code=fund_code,
        fund_name=decision.get("fund_name") or "",
        action="sell",
        amount=0,
        shares=None,
        order_no=order_no,
        reason=f"[DRY_RUN sell {sell_pct}%] {decision.get('reason', '')}",
        api_response={"dry_run": True, "sell_pct": sell_pct},
        dry_run=True,
    )
    _mark_decision_executed(decision_id, order_no)
    logger.info(f"[trade_execution] 💧 DRY_RUN sell {fund_code} {sell_pct}% (decision#{decision_id})")
    return {"trade_id": trade_id, "dry_run": True, "success": True}


# ==================== 主流程 ====================


class TradeExecutor:
    """交易执行器"""

    async def execute_pending(self) -> dict:
        """扫描 pending 决策并执行"""
        decisions = _query_pending_decisions()
        if not decisions:
            logger.info("[trade_execution] 无 pending 决策")
            return {"total": 0, "executed": 0, "dry_run": 0, "live": 0, "failed": 0}

        logger.info(f"[trade_execution] 处理 {len(decisions)} 条 pending 决策 (EXEC_DRY_RUN={EXEC_DRY_RUN})")

        executed = 0
        dry_count = 0
        live_count = 0
        failed = 0

        for d in decisions:
            try:
                if d["action"] == "buy":
                    result = await _execute_buy(d)
                elif d["action"] == "sell":
                    result = await _execute_sell(d)
                else:
                    _mark_decision_cancelled(d["id"], f"未知 action: {d['action']}")
                    continue

                if result.get("success"):
                    executed += 1
                    if result.get("dry_run"):
                        dry_count += 1
                    else:
                        live_count += 1
                else:
                    failed += 1
            except Exception as e:
                logger.warning(f"[trade_execution] 决策 #{d['id']} 异常: {e}")
                _mark_decision_cancelled(d["id"], f"执行异常: {e}")
                failed += 1

        logger.info(
            f"[trade_execution] 完成: 处理 {len(decisions)}, 成功 {executed} "
            f"(dry_run {dry_count}, live {live_count}), 失败 {failed}"
        )
        return {
            "total": len(decisions), "executed": executed,
            "dry_run": dry_count, "live": live_count, "failed": failed,
        }
