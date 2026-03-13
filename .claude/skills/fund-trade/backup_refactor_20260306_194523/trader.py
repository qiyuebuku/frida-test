#!/usr/bin/env python3
"""交易执行模块 - 直接执行买入/卖出，并记录到数据库（不依赖 server.py）"""

import argparse
import json
import os
import sys
from decimal import Decimal

# 添加 ths/api 到路径以导入 fund_db
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../ths/api')))

import fund_db
from ths_trade_client import THSTradeClient

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

# 全局客户端实例
_client = None


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def get_client():
    """获取或创建交易客户端"""
    global _client
    if _client is None:
        config = load_config()
        _client = THSTradeClient()

        # 设置交易密码
        trade_password = os.environ.get("THS_TRADE_PASSWORD") or config.get("trade_password")
        if trade_password:
            _client.set_password(trade_password)

    return _client


def get_position(fund_code):
    """从 ft_positions 查询指定基金的持仓记录"""
    positions = fund_db.get_positions()
    for p in positions:
        if p["fund_code"] == fund_code:
            return p
    return None


def do_buy(fund_code, amount, reason=None):
    """执行买入操作"""
    if amount <= 0:
        return {"status": "error", "message": f"买入金额无效: {amount}"}

    try:
        client = get_client()
        result = client.buy_fund(fund_code, amount)

        order_no = result.get("app_sheet_serial_no", "")
        fund_name = result.get("fund_name", "")

        # 记录交易
        fund_db.save_trade(
            fund_code=fund_code,
            fund_name=fund_name,
            action="buy",
            amount=amount,
            order_no=order_no,
            reason=reason,
            api_response=result,
        )

        # 更新持仓
        position = get_position(fund_code)
        if position:
            new_total_cost = float(position["total_cost"]) + amount
            fund_db.update_position(fund_code, fund_name=fund_name or position.get("fund_name"), total_cost=new_total_cost)
        else:
            fund_db.update_position(fund_code, fund_name=fund_name, total_cost=amount)

        return {
            "status": "success",
            "action": "buy",
            "fund_code": fund_code,
            "amount": amount,
            "order_no": order_no,
            "message": f"买入 {fund_code} 金额 {amount} 元，订单号 {order_no}",
        }

    except Exception as e:
        return {"status": "error", "message": f"买入失败: {e}"}


def do_sell(fund_code, pct, reason=None):
    """执行卖出操作"""
    if pct <= 0 or pct > 100:
        return {"status": "error", "message": f"卖出百分比无效: {pct}，应为 1-100"}

    position = get_position(fund_code)
    if not position:
        return {"status": "error", "message": f"基金 {fund_code} 无持仓记录"}

    shares = float(position["shares"])
    if shares <= 0:
        return {"status": "error", "message": f"基金 {fund_code} 持仓份额为 0"}

    sell_shares = round(shares * pct / 100, 4)
    sell_all = pct >= 100

    try:
        client = get_client()
        result = client.sell_fund(
            fund_code=fund_code,
            share_vol=sell_shares if not sell_all else None,
            sell_all=sell_all
        )

        order_no = result.get("app_sheet_serial_no", "")
        fund_name = result.get("fund_name", "") or position.get("fund_name", "")

        # 记录交易
        fund_db.save_trade(
            fund_code=fund_code,
            fund_name=fund_name,
            action="sell",
            amount=None,
            shares=sell_shares,
            order_no=order_no,
            reason=reason,
            api_response=result,
        )

        # 更新持仓
        if sell_all:
            fund_db.delete_position(fund_code)
        else:
            remaining_shares = round(shares - sell_shares, 4)
            total_cost = float(position["total_cost"])
            new_total_cost = round(total_cost * (1 - pct / 100), 2)
            fund_db.update_position(fund_code, shares=remaining_shares, total_cost=new_total_cost)

        return {
            "status": "success",
            "action": "sell",
            "fund_code": fund_code,
            "sell_pct": pct,
            "shares_sold": sell_shares,
            "order_no": order_no,
            "message": f"卖出 {fund_code} {pct}%（{sell_shares}份），订单号 {order_no}",
        }

    except Exception as e:
        return {"status": "error", "message": f"卖出失败: {e}"}


def main():
    parser = argparse.ArgumentParser(description="基金交易执行工具")
    subparsers = parser.add_subparsers(dest="command", help="交易命令")

    # buy 子命令
    buy_parser = subparsers.add_parser("buy", help="买入基金")
    buy_parser.add_argument("code", help="基金代码（如 006888）")
    buy_parser.add_argument("amount", type=float, help="买入金额（元）")
    buy_parser.add_argument("--reason", default=None, help="买入理由")

    # sell 子命令
    sell_parser = subparsers.add_parser("sell", help="卖出基金")
    sell_parser.add_argument("code", help="基金代码")
    sell_parser.add_argument("pct", nargs="?", default=None, type=float, help="卖出百分比（1-100）")
    sell_parser.add_argument("--all", dest="sell_all", action="store_true", help="全部卖出")
    sell_parser.add_argument("--reason", default=None, help="卖出理由")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "buy":
        result = do_buy(args.code, args.amount, reason=args.reason)
    elif args.command == "sell":
        if args.sell_all:
            pct = 100
        elif args.pct is not None:
            pct = args.pct
        else:
            print(json.dumps({"status": "error", "message": "卖出必须指定百分比或 --all"}, ensure_ascii=False))
            sys.exit(1)
        result = do_sell(args.code, pct, reason=args.reason)

    print(json.dumps(result, ensure_ascii=False, separators=(",",":")))

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
