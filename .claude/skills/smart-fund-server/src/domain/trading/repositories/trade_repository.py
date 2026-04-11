"""交易记录仓储抽象接口"""
from abc import ABC, abstractmethod


class TradeRepository(ABC):
    """ft_trades 仓储"""

    @abstractmethod
    def insert(
        self,
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
        """写一条 trade,返回 trade_id"""
        ...

    @abstractmethod
    def paper_positions(self) -> list[dict]:
        """从 dry_run=True 的 trades 聚合 paper position

        Returns: [{fund_code, fund_name, total_cost, shares, profit_pct=0, ...}]
        """
        ...

    @abstractmethod
    def today_live_buy_total(self) -> float:
        """今日实际买入(dry_run=false)累计金额(用于 executor 的 daily limit)"""
        ...
