#!/usr/bin/env python3
"""交易执行模块 - 调用同花顺 API 执行买入/卖出，并记录到数据库"""

import argparse
import json
import os
import sys
from decimal import Decimal

import requests

import fund_db

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


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

    config = load_config()
    server_url = config["server_url"]
    url = f"{server_url}/api/trade/buy"

    payload = {"fund_code": fund_code, "amount": amount}
    trade_password = os.environ.get("THS_TRADE_PASSWORD") or config.get("trade_password")
    if trade_password:
        payload["password"] = trade_password

    try:
        resp = requests.post(url, json=payload, timeout=30, proxies={"http": None, "https": None})
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"status": "error", "message": f"API 请求失败: {e}"}

    resp_json = resp.json()
    order_no = resp_json.get("order_no", "")
    fund_name = resp_json.get("fund_name", "")

    # 记录交易
    fund_db.save_trade(
        fund_code=fund_code,
        fund_name=fund_name,
        action="buy",
        amount=amount,
        order_no=order_no,
        reason=reason,
        api_response=resp_json,
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

    config = load_config()
    server_url = config["server_url"]
    url = f"{server_url}/api/trade/sell"

    payload = {"fund_code": fund_code, "share_vol": sell_shares, "sell_all": sell_all}
    trade_password = os.environ.get("THS_TRADE_PASSWORD") or config.get("trade_password")
    if trade_password:
        payload["password"] = trade_password

    try:
        resp = requests.post(url, json=payload, timeout=30, proxies={"http": None, "https": None})
        resp.raise_for_status()
    except requests.RequestException as e:
        return {"status": "error", "message": f"API 请求失败: {e}"}

    resp_json = resp.json()
    order_no = resp_json.get("order_no", "")
    fund_name = resp_json.get("fund_name", "") or position.get("fund_name", "")

    # 记录交易
    fund_db.save_trade(
        fund_code=fund_code,
        fund_name=fund_name,
        action="sell",
        amount=None,
        shares=sell_shares,
        order_no=order_no,
        reason=reason,
        api_response=resp_json,
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

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
