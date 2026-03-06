#!/usr/bin/env python3
"""Fund Trade Client - 轻量级 HTTP 客户端"""

import json
import os
import sys
import requests

# 配置
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

def load_config():
    """加载客户端配置"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

class FundTradeClient:
    """轻量级 HTTP 客户端（仅负责请求转发）"""

    def __init__(self, server_url=None):
        config = load_config()
        self.server_url = server_url or config.get("server_url", "http://localhost:8900")
        self.timeout = config.get("timeout", 60)

    def request(self, method: str, endpoint: str, **kwargs):
        """通用 HTTP 请求"""
        url = f"{self.server_url}{endpoint}"

        try:
            if method == "GET":
                resp = requests.get(url, params=kwargs.get("params"), timeout=self.timeout)
            elif method == "POST":
                resp = requests.post(url, json=kwargs.get("json"), timeout=self.timeout)
            else:
                return {"status": "error", "message": f"不支持的方法: {method}"}

            resp.raise_for_status()
            return resp.json()

        except requests.exceptions.RequestException as e:
            return {"status": "error", "message": f"请求失败: {e}"}

    # ========== 交易相关 ==========

    def buy(self, fund_code: str, amount: float, reason: str = None):
        """买入基金"""
        return self.request("POST", "/api/trade/buy", json={
            "fund_code": fund_code,
            "amount": amount,
            "reason": reason
        })

    def sell(self, fund_code: str, pct: float, reason: str = None):
        """卖出基金"""
        return self.request("POST", "/api/trade/sell", json={
            "fund_code": fund_code,
            "pct": pct,
            "reason": reason
        })

    # ========== 持仓查询 ==========

    def get_positions(self):
        """查询所有持仓"""
        return self.request("GET", "/api/position")

    def get_position(self, fund_code: str):
        """查询指定基金持仓"""
        return self.request("GET", f"/api/position/{fund_code}")

    # ========== 风控相关 ==========

    def snapshot(self):
        """风控快照"""
        return self.request("GET", "/api/risk/snapshot")

    def check_decisions(self, decisions: dict):
        """检查决策是否违反风控"""
        return self.request("POST", "/api/risk/check", json=decisions)

    def preflight(self):
        """交易前置检查"""
        return self.request("GET", "/api/risk/preflight")

    # ========== 量化信号 ==========

    def evaluate_signals(self):
        """计算量化信号"""
        return self.request("POST", "/api/indicators/evaluate")

    # ========== 决策复盘 ==========

    def review_decisions(self, limit: int = 30, days_back: int = 7):
        """执行决策复盘"""
        return self.request("POST", "/api/review/execute", json={
            "limit": limit,
            "days_back": days_back
        })

    # ========== 交易订单 ==========

    def get_orders(self, days: int = 30, limit: int = 20):
        """查询同花顺在线订单（实时查询，需要登录态）"""
        return self.request("GET", "/api/trade/orders", params={
            "days": days,
            "op_type": "all",
            "limit": limit,
            "offset": 1
        })

    def get_local_trades(self, days: int = 30, limit: int = 100):
        """查询本地交易记录（数据库，仅包含通过本系统执行的交易）"""
        return self.request("GET", "/api/trades", params={
            "days": days,
            "limit": limit
        })

    # ========== 基金数据 ==========

    def get_fund_detail(self, fund_code: str):
        """基金详情"""
        return self.request("GET", f"/api/fund/{fund_code}/detail")

    def get_fund_ranking(self, sort_type: str = "year", page: int = 1):
        """基金排名"""
        return self.request("GET", "/api/fund/ranking", params={
            "sort_type": sort_type,
            "page": page
        })


# ========== CLI 入口 ==========

def main():
    """命令行入口（用于测试）"""
    if len(sys.argv) < 2:
        print("用法: python client.py <method> [args...]")
        print("示例:")
        print("  python client.py buy 008087 100 '测试'")
        print("  python client.py sell 008087 50")
        print("  python client.py snapshot")
        print("  python client.py preflight")
        print("  python client.py evaluate")
        print("  python client.py orders [days] [limit]")
        print("  python client.py position [fund_code]")
        sys.exit(1)

    client = FundTradeClient()
    command = sys.argv[1]

    if command == "buy":
        if len(sys.argv) < 4:
            print(json.dumps({"status": "error", "message": "用法: buy <code> <amount> [reason]"}, ensure_ascii=False))
            sys.exit(1)
        fund_code = sys.argv[2]
        amount = float(sys.argv[3])
        reason = sys.argv[4] if len(sys.argv) > 4 else None
        result = client.buy(fund_code, amount, reason)

    elif command == "sell":
        if len(sys.argv) < 4:
            print(json.dumps({"status": "error", "message": "用法: sell <code> <pct> [reason]"}, ensure_ascii=False))
            sys.exit(1)
        fund_code = sys.argv[2]
        pct = float(sys.argv[3])
        reason = sys.argv[4] if len(sys.argv) > 4 else None
        result = client.sell(fund_code, pct, reason)

    elif command == "snapshot":
        result = client.snapshot()

    elif command == "preflight":
        result = client.preflight()

    elif command == "evaluate":
        result = client.evaluate_signals()

    elif command == "review":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        days_back = int(sys.argv[3]) if len(sys.argv) > 3 else 7
        result = client.review_decisions(limit, days_back)

    elif command == "position":
        if len(sys.argv) > 2:
            result = client.get_position(sys.argv[2])
        else:
            result = client.get_positions()

    elif command == "detail":
        if len(sys.argv) < 3:
            print(json.dumps({"status": "error", "message": "用法: detail <code>"}, ensure_ascii=False))
            sys.exit(1)
        result = client.get_fund_detail(sys.argv[2])

    elif command == "ranking":
        sort_type = sys.argv[2] if len(sys.argv) > 2 else "year"
        page = int(sys.argv[3]) if len(sys.argv) > 3 else 1
        result = client.get_fund_ranking(sort_type, page)

    elif command == "orders":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20
        result = client.get_orders(days=days, limit=limit)

    else:
        result = {"status": "error", "message": f"未知命令: {command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
