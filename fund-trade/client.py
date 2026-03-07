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
        config = load_config()
        data = {
            "fund_code": fund_code,
            "amount": amount,
            "password": config.get("trade_password"),
        }
        if reason:
            data["reason"] = reason
        return self.request("POST", "/api/trade/buy", json=data)

    def sell(self, fund_code: str, pct: float, reason: str = None):
        """卖出基金"""
        config = load_config()
        data = {
            "fund_code": fund_code,
            "pct": pct,
            "password": config.get("trade_password"),
        }
        if reason:
            data["reason"] = reason
        return self.request("POST", "/api/trade/sell", json=data)

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

    def create_reviews(self, decision_date: str = None):
        """创建待复盘记录"""
        params = {"decision_date": decision_date} if decision_date else {}
        return self.request("POST", "/api/review/create", params=params)

    def get_pending_reviews(self, days_back: int = 3):
        """获取待复盘决策"""
        return self.request("GET", "/api/review/pending", params={"days_back": days_back})

    def get_review_stats(self, days: int = 30):
        """获取复盘统计"""
        return self.request("GET", "/api/review/stats", params={"days": days})

    def get_lessons(self, category: str = None, min_confidence: str = None,
                    include_deprecated: bool = False, limit: int = 20):
        """获取经验知识库"""
        params = {"limit": limit, "include_deprecated": include_deprecated}
        if category:
            params["category"] = category
        if min_confidence:
            params["min_confidence"] = min_confidence
        return self.request("GET", "/api/lessons", params=params)

    def save_lesson(self, lesson: dict):
        """保存经验教训"""
        return self.request("POST", "/api/lessons/save", json=lesson)

    def update_lesson_confidence(self, lesson_id: int, success: bool):
        """更新经验可信度"""
        return self.request("POST", f"/api/lessons/update-confidence/{lesson_id}", params={"success": success})

    def update_review(self, review_id: int, review_data: dict):
        """更新复盘结果"""
        return self.request("POST", f"/api/review/update/{review_id}", json=review_data)

    def mark_lesson_extracted(self, review_id: int):
        """标记经验已提取"""
        return self.request("POST", f"/api/review/mark-extracted/{review_id}")

    # ========== 决策管理 ==========

    def save_decision(self, decision: dict):
        """保存决策"""
        return self.request("POST", "/api/decisions/save", json=decision)

    def save_pending_decision(self, decision: dict):
        """保存待确认决策"""
        return self.request("POST", "/api/decisions/save-pending", json=decision)

    def execute_pending_decision(self, pending_id: int):
        """执行待确认决策"""
        return self.request("POST", f"/api/decisions/execute-pending/{pending_id}")

    def get_today_decisions(self):
        """获取今日决策"""
        return self.request("GET", "/api/decisions/today")

    def get_recent_decisions(self, days: int = 5, exclude_today: bool = False):
        """获取最近决策"""
        return self.request("GET", "/api/decisions/recent", params={
            "days": days,
            "exclude_today": exclude_today
        })

    def get_watch_streaks(self):
        """获取连续观望天数"""
        return self.request("GET", "/api/decisions/watch-streaks")

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

    def scan_funds(self):
        """基金扫描（完整版）"""
        return self.request("GET", "/api/funds/scan")

    def scan_funds_summary(self):
        """基金扫描（精简版）"""
        return self.request("GET", "/api/funds/scan-summary")

    # ========== 数据同步 ==========

    def sync_positions(self):
        """同步持仓"""
        return self.request("POST", "/api/sync/positions")

    # ========== 账户信息 ==========

    def get_account_overview(self):
        """账户总览"""
        return self.request("GET", "/api/account/overview")

    def get_wallet_info(self):
        """钱包信息"""
        return self.request("GET", "/api/wallet/info")

    def get_wallet_home(self):
        """钱包首页"""
        return self.request("GET", "/api/wallet/home")


# ========== CLI 入口 ==========

def main():
    """命令行入口（用于测试）"""
    if len(sys.argv) < 2:
        print("用法: python client.py <method> [args...]")
        print("\n交易相关:")
        print("  python client.py buy 008087 100 '测试'")
        print("  python client.py sell 008087 50")
        print("  python client.py position [fund_code]")
        print("  python client.py orders [days] [limit]")
        print("\n风控相关:")
        print("  python client.py snapshot")
        print("  python client.py preflight")
        print("\n量化相关:")
        print("  python client.py evaluate")
        print("\n决策复盘:")
        print("  python client.py review [limit] [days_back]")
        print("  python client.py create-reviews [decision_date]")
        print("  python client.py pending-reviews [days_back]")
        print("  python client.py review-stats [days]")
        print("  python client.py lessons")
        print("\n决策管理:")
        print("  python client.py today-decisions")
        print("  python client.py recent-decisions [days] [exclude_today]")
        print("  python client.py watch-streaks")
        print("\n数据采集:")
        print("  python client.py scan              # 基金扫描（完整版）")
        print("  python client.py scan-summary      # 基金扫描（精简版）")
        print("  python client.py sync              # 同步持仓")
        print("\n账户信息:")
        print("  python client.py account-overview  # 账户总览")
        print("  python client.py wallet-info       # 钱包信息")
        print("  python client.py wallet-home       # 钱包首页")
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

    elif command == "create-reviews":
        decision_date = sys.argv[2] if len(sys.argv) > 2 else None
        result = client.create_reviews(decision_date)

    elif command == "pending-reviews":
        days_back = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        result = client.get_pending_reviews(days_back)

    elif command == "review-stats":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        result = client.get_review_stats(days)

    elif command == "lessons":
        result = client.get_lessons()

    elif command == "today-decisions":
        result = client.get_today_decisions()

    elif command == "recent-decisions":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        exclude_today = sys.argv[3].lower() == "true" if len(sys.argv) > 3 else False
        result = client.get_recent_decisions(days, exclude_today)

    elif command == "watch-streaks":
        result = client.get_watch_streaks()

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

    elif command == "scan":
        result = client.scan_funds()

    elif command == "scan-summary":
        result = client.scan_funds_summary()

    elif command == "sync":
        result = client.sync_positions()

    elif command == "account-overview":
        result = client.get_account_overview()

    elif command == "wallet-info":
        result = client.get_wallet_info()

    elif command == "wallet-home":
        result = client.get_wallet_home()

    else:
        result = {"status": "error", "message": f"未知命令: {command}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result.get("status") == "success" else 1)


if __name__ == "__main__":
    main()
