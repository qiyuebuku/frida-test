#!/usr/bin/env python3
"""
基金 API 客户端 - 通过 JSBridge Proxy 调用同花顺 fund API
不依赖 WebView，纯 HTTP 接口
"""

import requests
import json
from typing import Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class FundPosition:
    """基金持仓信息"""
    fund_code: str
    fund_name: str
    shares: float       # 持有份额
    nav: float          # 净值
    amount: float       # 市值
    profit: float       # 盈亏
    profit_rate: float  # 盈亏比例


@dataclass
class AccountSummary:
    """账户汇总信息"""
    total_assets: float      # 总资产
    total_profit: float      # 总盈亏
    today_profit: float      # 今日收益
    positions: list          # 持仓列表


class FundAPIClient:
    """基金 API 客户端"""

    def __init__(self, proxy_url: str = "http://localhost:18900",
                 cust_id: str = "100113970166"):
        """
        初始化 API 客户端

        Args:
            proxy_url: JSBridge Proxy 地址
            cust_id: 客户ID（从持仓API中获取）
        """
        self.proxy_url = proxy_url
        self.cust_id = cust_id
        self.jsbridge_endpoint = f"{proxy_url}/jsbridge"

    def _call_jsbridge(self, method: str, url: str,
                       params: Optional[Dict] = None,
                       headers: Optional[Dict] = None,
                       need_token: bool = False,
                       k5type: str = "normal",
                       timeout: int = 35) -> Dict[str, Any]:
        """
        调用 JSBridge API

        Args:
            method: HTTP 方法 (GET/POST)
            url: 目标 URL
            params: 请求参数
            headers: 请求头
            need_token: 是否需要 token
            k5type: K5 类型
            timeout: 超时时间（秒）

        Returns:
            API 响应数据
        """
        payload = {
            "handler": "clientRequestHX",
            "data": {
                "method": method,
                "url": url,
                "params": params or {},
                "K5type": k5type
            }
        }

        if headers:
            payload["data"]["Header"] = headers

        if need_token:
            payload["data"]["needToken"] = True

        try:
            response = requests.post(
                self.jsbridge_endpoint,
                json=payload,
                timeout=timeout
            )
            response.raise_for_status()
            result = response.json()

            if result.get("success"):
                return result.get("data", {})
            else:
                error = result.get("error", "Unknown error")
                raise RuntimeError(f"JSBridge call failed: {error}")

        except Exception as e:
            raise RuntimeError(f"API call failed: {e}")

    def get_positions(self) -> AccountSummary:
        """
        查询持仓

        Returns:
            AccountSummary 对象
        """
        url = f"https://trade.5ifund.com/rs/incomequery/queryzcsharemobilehomenine/{self.cust_id}"

        response = self._call_jsbridge("GET", url)

        # 解析响应
        result = response.get("result", {})
        data = result.get("data", {})

        # 提取汇总信息
        summary = data.get("summary", {})
        total_assets = float(summary.get("totalAssets", "0"))
        total_profit = float(summary.get("totalProfit", "0"))
        today_profit = float(summary.get("todayProfit", "0"))

        # 提取持仓列表
        positions = []
        funds = data.get("funds", [])
        for fund in funds:
            positions.append(FundPosition(
                fund_code=fund.get("fundCode", ""),
                fund_name=fund.get("fundName", ""),
                shares=float(fund.get("shares", "0")),
                nav=float(fund.get("nav", "0")),
                amount=float(fund.get("amount", "0")),
                profit=float(fund.get("profit", "0")),
                profit_rate=float(fund.get("profitRate", "0"))
            ))

        return AccountSummary(
            total_assets=total_assets,
            total_profit=total_profit,
            today_profit=today_profit,
            positions=positions
        )

    def get_wallet_info(self) -> Dict[str, Any]:
        """
        查询钱包信息

        Returns:
            钱包数据字典
        """
        url = "https://trade.5ifund.com/rz/wallet/dubbo/v1/queryWalletHomePage"

        headers = {
            "Accept": "application/json",
            "custId": self.cust_id,
            "source": "SDK"
        }

        params = {
            "custId": self.cust_id
        }

        response = self._call_jsbridge(
            "POST", url,
            params=params,
            headers=headers,
            need_token=True
        )

        return response.get("result", {}).get("data", {})

    def get_fund_detail(self, fund_code: str) -> Dict[str, Any]:
        """
        查询基金详情

        Args:
            fund_code: 基金代码

        Returns:
            基金详情数据
        """
        # TODO: 需要抓包获取基金详情 API
        raise NotImplementedError("基金详情 API 尚未实现")

    def buy_fund(self, fund_code: str, amount: float) -> Dict[str, Any]:
        """
        买入基金

        Args:
            fund_code: 基金代码
            amount: 金额

        Returns:
            交易结果
        """
        # TODO: 需要抓包获取买入 API
        raise NotImplementedError("买入 API 尚未实现")

    def sell_fund(self, fund_code: str, shares: float) -> Dict[str, Any]:
        """
        卖出基金

        Args:
            fund_code: 基金代码
            shares: 份额

        Returns:
            交易结果
        """
        # TODO: 需要抓包获取卖出 API
        raise NotImplementedError("卖出 API 尚未实现")


def main():
    """示例用法"""
    # 初始化客户端
    client = FundAPIClient()

    print("\n=== 查询持仓 ===")
    summary = client.get_positions()

    print(f"总资产: {summary.total_assets:.2f} 元")
    print(f"总盈亏: {summary.total_profit:.2f} 元")
    print(f"今日收益: {summary.today_profit:.2f} 元")
    print(f"\n持仓列表 ({len(summary.positions)} 只基金):")

    for pos in summary.positions:
        print(f"\n{pos.fund_name} ({pos.fund_code})")
        print(f"  持有: {pos.shares:.2f} 份")
        print(f"  净值: {pos.nav:.4f}")
        print(f"  市值: {pos.amount:.2f} 元")
        print(f"  盈亏: {pos.profit:.2f} 元 ({pos.profit_rate:.2f}%)")

    print("\n=== 查询钱包 ===")
    wallet = client.get_wallet_info()
    print(json.dumps(wallet, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
