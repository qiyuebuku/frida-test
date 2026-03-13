"""同花顺股票数据客户端 - 通过 Hook 代理服务器获取股票持仓/资产数据"""

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

import httpx

# 清除代理环境变量
for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(key, None)


@dataclass
class StockPosition:
    """股票持仓"""
    stock_code: str           # 证券代码
    stock_name: str           # 证券名称
    stock_avl: float          # 可用余额
    stock_remain: float       # 证券余额(总数)
    stock_freeze: float       # 冻结数量
    price_buy_av: float       # 买入均价
    price_curr: float         # 最新价格
    stock_market_value: float # 股票市值
    stock_profit: float       # 浮动盈亏
    profit_loss_ratio: float  # 盈亏比例
    market: str               # 市场代码
    stock_account: str        # 股东账户


@dataclass
class StockAsset:
    """股票资产"""
    total_asset: float        # 总资产
    money_remain: float       # 资金余额
    money_avl: float          # 可用资金
    money_freeze: float       # 冻结资金
    can_draw: float           # 可取资金
    money_type: int           # 币种 (0=人民币)


@dataclass
class StockOrder:
    """委托单"""
    entrust_date: str         # 委托日期
    entrust_time: str         # 委托时间
    stock_code: str           # 证券代码
    stock_name: str           # 证券名称
    op: str                   # 操作类型 (4001=卖出, 4002=买入)
    entrust_count: int        # 委托数量
    entrust_price: float      # 委托价格
    trans_count: int          # 成交数量
    trans_price: float        # 成交价格
    contract_no: str          # 合同编号


@dataclass
class StockTrade:
    """成交记录"""
    trans_date: str           # 成交日期
    trans_time: str           # 成交时间
    stock_code: str           # 证券代码
    stock_name: str           # 证券名称
    op: str                   # 操作类型
    op_name: str              # 操作名称
    trans_count: int          # 成交数量
    price_trans: float        # 成交价格
    stock_remain: int         # 剩余股数
    market: str               # 市场代码


class THSStockClient:
    """同花顺股票数据客户端

    通过 Hook 代理服务器（端口 18900）获取股票持仓和资产数据。
    数据来源于本地 SQLite 数据库，通过 JSBridge executeSql 接口查询。
    """

    # 业务代码映射
    OP_CODES = {
        "4001": "证券卖出",
        "4002": "证券买入",
        "4018": "股息入账",
        "4015": "红股入账",
    }

    def __init__(
        self,
        phone_ip: str = "192.168.111.58",
        proxy_port: int = 18900,
        timeout: float = 10.0,
    ):
        self.base_url = f"http://{phone_ip}:{proxy_port}"
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self):
        await self._client.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _get(self, endpoint: str) -> dict:
        """发送 GET 请求到代理服务器"""
        url = f"{self.base_url}{endpoint}"
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    async def get_status(self) -> dict:
        """获取数据库状态"""
        return await self._get("/stock/status")

    async def get_positions(self) -> List[StockPosition]:
        """获取持仓列表"""
        data = await self._get("/stock/positions")

        if "error" in data:
            raise RuntimeError(data["error"])

        positions = []
        for row in data.get("data", []):
            try:
                pos = StockPosition(
                    stock_code=str(row.get("2102", row.get("stock_code", ""))),
                    stock_name=str(row.get("2103", row.get("stock_name", ""))),
                    stock_avl=float(row.get("2121", row.get("stock_avl", 0))),
                    stock_remain=float(row.get("2117", row.get("stock_remain", 0))),
                    stock_freeze=float(row.get("2118", row.get("stock_freeze", 0))),
                    price_buy_av=float(row.get("2122", row.get("price_buy_av", 0))),
                    price_curr=float(row.get("2124", row.get("price_curr", 0))),
                    stock_market_value=float(row.get("2125", row.get("stock_market_value", 0))),
                    stock_profit=float(row.get("2147", row.get("stock_profit", 0))),
                    profit_loss_ratio=float(row.get("3616", row.get("profit_loss_ratio", 0))),
                    market=str(row.get("2108", row.get("market", ""))),
                    stock_account=str(row.get("2106", row.get("stock_account", ""))),
                )
                positions.append(pos)
            except (ValueError, TypeError) as e:
                # 跳过解析失败的行
                continue

        return positions

    async def get_assets(self) -> Optional[StockAsset]:
        """获取资产信息"""
        data = await self._get("/stock/assets")

        if "error" in data:
            raise RuntimeError(data["error"])

        rows = data.get("data", [])
        if not rows:
            return None

        row = rows[0]
        return StockAsset(
            total_asset=float(row.get("asset_total", 0)),
            money_remain=float(row.get("money_remain", 0)),
            money_avl=float(row.get("money_avl", 0)),
            money_freeze=float(row.get("money_freeze", 0)),
            can_draw=float(row.get("can_draw", 0)),
            money_type=int(row.get("money_type", 0)),
        )

    async def get_orders(self) -> List[StockOrder]:
        """获取委托单列表"""
        data = await self._get("/stock/orders")

        if "error" in data:
            raise RuntimeError(data["error"])

        orders = []
        for row in data.get("data", []):
            try:
                order = StockOrder(
                    entrust_date=str(row.get("entrust_date", "")),
                    entrust_time=str(row.get("entrust_time", "")),
                    stock_code=str(row.get("2102", row.get("stock_code", ""))),
                    stock_name=str(row.get("2103", row.get("stock_name", ""))),
                    op=str(row.get("2109", row.get("op", ""))),
                    entrust_count=int(float(row.get("2126", row.get("entrust_count", 0)))),
                    entrust_price=float(row.get("2127", row.get("entrust_price", 0))),
                    trans_count=int(float(row.get("2128", row.get("trans_count", 0)))),
                    trans_price=float(row.get("2129", row.get("trans_price", 0))),
                    contract_no=str(row.get("2135", row.get("contract_NO", ""))),
                )
                orders.append(order)
            except (ValueError, TypeError):
                continue

        return orders

    async def get_history(self, limit: int = 100) -> List[StockTrade]:
        """获取历史成交记录"""
        data = await self._get("/stock/history")

        if "error" in data:
            raise RuntimeError(data["error"])

        trades = []
        for row in data.get("data", [])[:limit]:
            try:
                op = str(row.get("2109", row.get("op", "")))
                trade = StockTrade(
                    trans_date=str(row.get("trans_date", "")),
                    trans_time=str(row.get("trans_time", "")),
                    stock_code=str(row.get("2102", row.get("stock_code", ""))),
                    stock_name=str(row.get("2103", row.get("stock_name", ""))),
                    op=op,
                    op_name=self.OP_CODES.get(op, row.get("op_name", "未知")),
                    trans_count=int(float(row.get("trans_count", 0))),
                    price_trans=float(row.get("price_trans", 0)),
                    stock_remain=int(float(row.get("stock_remain", 0))),
                    market=str(row.get("2108", row.get("market", ""))),
                )
                trades.append(trade)
            except (ValueError, TypeError):
                continue

        return trades

    async def get_daily_trades(self) -> List[StockTrade]:
        """获取当日成交记录"""
        data = await self._get("/stock/daily")

        if "error" in data:
            raise RuntimeError(data["error"])

        trades = []
        for row in data.get("data", []):
            try:
                op = str(row.get("2109", row.get("op", "")))
                trade = StockTrade(
                    trans_date=str(row.get("trans_date", datetime.now().strftime("%Y%m%d"))),
                    trans_time=str(row.get("trans_time", "")),
                    stock_code=str(row.get("2102", row.get("stock_code", ""))),
                    stock_name=str(row.get("2103", row.get("stock_name", ""))),
                    op=op,
                    op_name=self.OP_CODES.get(op, row.get("op_name", "未知")),
                    trans_count=int(float(row.get("trans_count", 0))),
                    price_trans=float(row.get("price_trans", 0)),
                    stock_remain=int(float(row.get("stock_remain", 0))),
                    market=str(row.get("2108", row.get("market", ""))),
                )
                trades.append(trade)
            except (ValueError, TypeError):
                continue

        return trades

    # ========== 便捷方法 ==========

    async def get_portfolio_summary(self) -> dict:
        """获取投资组合摘要"""
        assets = await self.get_assets()
        positions = await self.get_positions()

        # 计算股票市值
        stock_market_value = sum(p.stock_market_value for p in positions)
        stock_profit = sum(p.stock_profit for p in positions)

        return {
            "total_asset": assets.total_asset if assets else 0,
            "money_avl": assets.money_avl if assets else 0,
            "money_remain": assets.money_remain if assets else 0,
            "stock_market_value": stock_market_value,
            "stock_profit": stock_profit,
            "position_count": len(positions),
            "positions": [
                {
                    "code": p.stock_code,
                    "name": p.stock_name,
                    "quantity": p.stock_remain,
                    "available": p.stock_avl,
                    "cost": p.price_buy_av,
                    "current": p.price_curr,
                    "market_value": p.stock_market_value,
                    "profit": p.stock_profit,
                    "profit_ratio": p.profit_loss_ratio,
                }
                for p in positions
            ],
        }


# 同步客户端包装器
class THSStockClientSync:
    """同步版本的客户端（用于非异步环境）"""

    def __init__(
        self,
        phone_ip: str = "192.168.111.58",
        proxy_port: int = 18900,
        timeout: float = 10.0,
    ):
        self.base_url = f"http://{phone_ip}:{proxy_port}"
        self._client = httpx.Client(timeout=timeout)

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _get(self, endpoint: str) -> dict:
        url = f"{self.base_url}{endpoint}"
        resp = self._client.get(url)
        resp.raise_for_status()
        return resp.json()

    def get_status(self) -> dict:
        return self._get("/stock/status")

    def get_positions_raw(self) -> dict:
        return self._get("/stock/positions")

    def get_assets_raw(self) -> dict:
        return self._get("/stock/assets")

    def get_orders_raw(self) -> dict:
        return self._get("/stock/orders")

    def get_history_raw(self) -> dict:
        return self._get("/stock/history")

    def get_daily_raw(self) -> dict:
        return self._get("/stock/daily")


# CLI 测试
if __name__ == "__main__":
    import asyncio
    import sys

    async def main():
        async with THSStockClient() as client:
            print("=== 数据库状态 ===")
            try:
                status = await client.get_status()
                print(json.dumps(status, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"错误: {e}")
                return

            print("\n=== 持仓查询 ===")
            try:
                positions = await client.get_positions()
                if positions:
                    for p in positions:
                        print(f"  {p.stock_code} {p.stock_name}: {p.stock_remain}股 "
                              f"成本{p.price_buy_av:.2f} 现价{p.price_curr:.2f} "
                              f"盈亏{p.stock_profit:.2f}({p.profit_loss_ratio:.2f}%)")
                else:
                    print("  暂无持仓")
            except Exception as e:
                print(f"  错误: {e}")

            print("\n=== 资产查询 ===")
            try:
                assets = await client.get_assets()
                if assets:
                    print(f"  总资产: {assets.total_asset:.2f}")
                    print(f"  资金余额: {assets.money_remain:.2f}")
                    print(f"  可用资金: {assets.money_avl:.2f}")
                    print(f"  冻结资金: {assets.money_freeze:.2f}")
                    print(f"  可取资金: {assets.can_draw:.2f}")
                else:
                    print("  暂无资产数据")
            except Exception as e:
                print(f"  错误: {e}")

    asyncio.run(main())
