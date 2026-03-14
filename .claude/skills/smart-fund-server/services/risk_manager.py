#!/usr/bin/env python3
"""风控硬约束模块 - Claude 不能覆盖其结果"""

import json
import os
import sys
from datetime import date, datetime

from services import fund_db


def load_config():
    return fund_db.get_config()


def _decimal_default(obj):
    """JSON 序列化时处理 Decimal 类型"""
    if hasattr(obj, "__float__"):
        return float(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ==================== snapshot ====================

def snapshot():
    """输出当前持仓/仓位/可用资金/各基金风控状态"""
    config = load_config()
    total_capital = config["total_capital"]
    risk = config["risk"]

    positions = fund_db.get_positions()

    invested = sum(float(p.get("total_cost", 0)) for p in positions)
    cash = total_capital - invested

    pos_details = []
    risk_alerts = []

    for p in positions:
        fund_code = p["fund_code"]
        total_cost = float(p.get("total_cost", 0))
        profit_pct = float(p.get("profit_pct", 0))
        position_pct = total_cost / total_capital * 100 if total_capital > 0 else 0

        is_stop_loss = profit_pct <= risk["stop_loss_pct"]
        is_take_profit = profit_pct >= risk["take_profit_pct"]
        is_over_position = position_pct > risk["max_single_position_pct"]

        hold_days = fund_db.get_hold_days(fund_code)
        add_count = int(p.get("add_count", 0))

        detail = {
            "fund_code": fund_code,
            "fund_name": p.get("fund_name", ""),
            "total_cost": total_cost,
            "shares": float(p.get("shares", 0)),
            "current_nav": float(p.get("current_nav", 0)),
            "market_value": float(p.get("market_value", 0)),
            "profit_pct": profit_pct,
            "position_pct": round(position_pct, 2),
            "hold_days": hold_days,
            "add_count": add_count,
            "is_stop_loss": is_stop_loss,
            "is_take_profit": is_take_profit,
            "is_over_position": is_over_position,
            "can_sell_free": hold_days is not None and hold_days >= risk.get("min_hold_days", 7),
        }
        pos_details.append(detail)

        if is_stop_loss:
            risk_alerts.append(f"{fund_code} 已触及止损线(收益率{profit_pct}% <= {risk['stop_loss_pct']}%)")
        if is_take_profit:
            risk_alerts.append(f"{fund_code} 已触及止盈线(收益率{profit_pct}% >= {risk['take_profit_pct']}%)")
        if is_over_position:
            risk_alerts.append(f"{fund_code} 超过单只仓位上限(仓位{round(position_pct, 2)}% > {risk['max_single_position_pct']}%)")

    result = {
        "total_capital": total_capital,
        "invested": round(invested, 2),
        "cash": round(cash, 2),
        "positions": pos_details,
        "risk_alerts": risk_alerts,
    }
    print(json.dumps(result, ensure_ascii=False, default=_decimal_default, separators=(",",":")))


# ==================== check ====================

def check(decisions_json):
    """校验 LLM 决策是否违反硬约束"""
    config = load_config()
    total_capital = config["total_capital"]
    risk = config["risk"]

    decisions = json.loads(decisions_json) if isinstance(decisions_json, str) else decisions_json
    decision_list = decisions.get("decisions", [])

    positions = fund_db.get_positions()
    pos_map = {p["fund_code"]: p for p in positions}

    invested = sum(float(p.get("total_cost", 0)) for p in positions)
    cash = total_capital - invested

    today_buy_total = fund_db.get_today_buy_total()

    results = []
    passed_count = 0
    blocked_count = 0

    # 累积本次 check 中前面决策对 cash 和 today_buy_total 的影响
    cumulative_buy = 0.0
    new_fund_codes = set()

    for d in decision_list:
        action = d.get("action", "").lower()
        fund_code = d.get("fund_code", "")

        # hold 和 watch 直接通过
        if action in ("hold", "watch"):
            results.append({
                "fund_code": fund_code,
                "action": action,
                "passed": True,
                "blocked_reasons": [],
            })
            passed_count += 1
            continue

        blocked_reasons = []

        if action == "buy":
            amount = float(d.get("amount", 0))

            # a. 最小交易额（风控配置的最低额度）
            if amount < risk["min_trade_amount"]:
                blocked_reasons.append(
                    f"低于最小交易额(买入{amount} < {risk['min_trade_amount']})"
                )

            # a2. 基金起购额（部分基金 minBuy >= 1000，需实际查询确认）
            # 注意：此检查仅为提醒，实际起购额需在执行交易前通过 subscribe/init 查询

            # b. 买入后该基金仓位 <= max_single_position_pct
            existing_cost = float(pos_map[fund_code]["total_cost"]) if fund_code in pos_map else 0
            new_cost = existing_cost + amount
            new_position_pct = new_cost / total_capital * 100 if total_capital > 0 else 0
            if new_position_pct > risk["max_single_position_pct"]:
                blocked_reasons.append(
                    f"超过单只仓位上限(买入后仓位{round(new_position_pct, 2)}% > {risk['max_single_position_pct']}%)"
                )

            # c. 今日已买入总额 + 本次 <= max_daily_buy_amount
            total_buy_after = today_buy_total + cumulative_buy + amount
            if total_buy_after > risk["max_daily_buy_amount"]:
                blocked_reasons.append(
                    f"超过单日买入上限(已买入{today_buy_total + cumulative_buy}+本次{amount}>{risk['max_daily_buy_amount']})"
                )

            # d. 买入后现金 >= min_cash_reserve
            cash_after = cash - cumulative_buy - amount
            if cash_after < risk["min_cash_reserve"]:
                blocked_reasons.append(
                    f"低于最低现金储备(买入后现金{round(cash_after, 2)} < {risk['min_cash_reserve']})"
                )

            # e. 如果是新基金，买入后持仓数 <= max_fund_count
            if fund_code not in pos_map and fund_code not in new_fund_codes:
                current_count = len(positions) + len(new_fund_codes)
                if current_count + 1 > risk["max_fund_count"]:
                    blocked_reasons.append(
                        f"超过最大持仓数(当前{current_count}只+新增1只>{risk['max_fund_count']})"
                    )

            # f. 距上次交易 >= cooldown_days
            last_trade_date = fund_db.get_last_trade_date(fund_code)
            if last_trade_date is not None:
                if isinstance(last_trade_date, str):
                    last_trade_date = datetime.strptime(last_trade_date, "%Y-%m-%d").date()
                days_since = (date.today() - last_trade_date).days
                if days_since < risk["cooldown_days"]:
                    blocked_reasons.append(
                        f"冷却期未满(距上次交易{days_since}天 < {risk['cooldown_days']}天)"
                    )

            # g. 已触及止损线不允许买入
            if fund_code in pos_map:
                profit_pct = float(pos_map[fund_code].get("profit_pct", 0))
                if profit_pct <= risk["stop_loss_pct"]:
                    blocked_reasons.append(
                        f"该基金已触及止损线(收益率{profit_pct}% <= {risk['stop_loss_pct']}%)"
                    )

            # h. 反向操作限制：最近N天内如果做过卖出，不允许买入
            reverse_cd = risk.get("reverse_cooldown_days", 7)
            last_action = fund_db.get_last_trade_action(fund_code)
            if last_action and last_action["action"] in ("sell", "clear"):
                last_date = last_action["trade_date"]
                if isinstance(last_date, str):
                    last_date = datetime.strptime(last_date, "%Y-%m-%d").date()
                days_since_sell = (date.today() - last_date).days
                if days_since_sell < reverse_cd:
                    blocked_reasons.append(
                        f"反向操作冷却期未满({days_since_sell}天前刚卖出，需等{reverse_cd}天)"
                    )

            result_item = {
                "fund_code": fund_code,
                "action": "buy",
                "amount": amount,
                "passed": len(blocked_reasons) == 0,
                "blocked_reasons": blocked_reasons,
            }
            results.append(result_item)

            if not blocked_reasons:
                cumulative_buy += amount
                if fund_code not in pos_map:
                    new_fund_codes.add(fund_code)

        elif action in ("sell", "clear"):
            sell_pct = float(d.get("sell_pct", 100 if action == "clear" else 0))

            # a. 必须有持仓
            if fund_code not in pos_map:
                blocked_reasons.append(f"无持仓({fund_code}不在持仓列表中)")

            # b. sell_pct 在 1-100 之间
            if sell_pct < 1 or sell_pct > 100:
                blocked_reasons.append(
                    f"卖出比例不合法(sell_pct={sell_pct}，应在1-100之间)"
                )

            # c. 持有天数检查（不满7天卖出有惩罚性赎回费1.5%，只警告不拦截）
            min_hold = risk.get("min_hold_days", 7)
            hold_days = fund_db.get_hold_days(fund_code)
            warnings = []
            if hold_days is not None and hold_days < min_hold:
                warnings.append(
                    f"⚠️ 持有仅{hold_days}天（不满{min_hold}天），卖出将收取惩罚性赎回费(通常1.5%)"
                )

            # d. 反向操作限制：最近N天内如果做过买入，不允许卖出
            reverse_cd = risk.get("reverse_cooldown_days", 7)
            last_action_info = fund_db.get_last_trade_action(fund_code)
            if last_action_info and last_action_info["action"] == "buy":
                last_date = last_action_info["trade_date"]
                if isinstance(last_date, str):
                    last_date = datetime.strptime(last_date, "%Y-%m-%d").date()
                days_since_buy = (date.today() - last_date).days
                if days_since_buy < reverse_cd:
                    blocked_reasons.append(
                        f"反向操作冷却期未满({days_since_buy}天前刚买入，需等{reverse_cd}天)"
                    )

            result_item = {
                "fund_code": fund_code,
                "action": action,
                "sell_pct": sell_pct,
                "passed": len(blocked_reasons) == 0,
                "blocked_reasons": blocked_reasons,
                "warnings": warnings,
            }
            results.append(result_item)

        else:
            blocked_reasons.append(f"未知动作类型: {action}")
            results.append({
                "fund_code": fund_code,
                "action": action,
                "passed": False,
                "blocked_reasons": blocked_reasons,
            })

        if blocked_reasons:
            blocked_count += 1
        else:
            passed_count += 1

    total = passed_count + blocked_count
    output = {
        "results": results,
        "summary": f"{total}个决策中{passed_count}个通过，{blocked_count}个被拦截",
    }
    print(json.dumps(output, ensure_ascii=False, default=_decimal_default, separators=(",",":")))


# ==================== preflight ====================

def preflight():
    """交易前置检查：交易时间、交易日、熔断机制"""
    config = load_config()
    risk = config["risk"]
    now = datetime.now()
    alerts = []
    can_trade = True

    # 1. 交易日检查（周一到周五）
    if now.weekday() >= 5:
        alerts.append(f"今天是{'周六' if now.weekday() == 5 else '周日'}，非交易日")
        can_trade = False

    # 2. 交易时间检查（必须在截止时间前）
    cutoff = risk.get("trade_cutoff_time", "14:50")
    cutoff_hour, cutoff_min = map(int, cutoff.split(":"))
    if now.hour > cutoff_hour or (now.hour == cutoff_hour and now.minute > cutoff_min):
        alerts.append(f"已过交易截止时间 {cutoff}（当前 {now.strftime('%H:%M')}），场外基金需 15:00 前下单")
        can_trade = False

    # 3. 熔断检查：最近 N 天连续亏损
    cb_loss_pct = risk.get("circuit_breaker_loss_pct", -10)
    cb_days = risk.get("circuit_breaker_loss_days", 5)
    positions = fund_db.get_positions()
    if positions:
        # 只统计已确认的持仓（shares > 0），排除待确认的买入（market_value=0）
        confirmed = [p for p in positions if float(p.get("shares", 0)) > 0]
        if confirmed:
            total_cost = sum(float(p.get("total_cost", 0)) for p in confirmed)
            total_value = sum(float(p.get("market_value", 0)) for p in confirmed)
            if total_cost > 0:
                overall_pct = (total_value - total_cost) / total_cost * 100
                if overall_pct <= cb_loss_pct:
                    alerts.append(
                        f"⚠️ 熔断警告：组合总亏损 {overall_pct:.1f}% 已达熔断线 {cb_loss_pct}%，建议暂停自动交易并人工复核"
                    )

    result = {
        "can_trade": can_trade,
        "current_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "is_trading_day": now.weekday() < 5,
        "before_cutoff": not (now.hour > cutoff_hour or (now.hour == cutoff_hour and now.minute > cutoff_min)),
        "alerts": alerts,
    }
    print(json.dumps(result, ensure_ascii=False, separators=(",",":")))


# ==================== CLI ====================

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python risk_manager.py snapshot              - 输出风控快照")
        print("  python risk_manager.py check '<json>'        - 校验决策")
        print("  python risk_manager.py check                 - 从 stdin 读取决策 JSON")
        print("  python risk_manager.py preflight             - 交易前置检查")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "snapshot":
        snapshot()
    elif cmd == "check":
        if len(sys.argv) >= 3:
            decisions_json = sys.argv[2]
        else:
            decisions_json = sys.stdin.read().strip()
        check(decisions_json)
    elif cmd == "preflight":
        preflight()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
