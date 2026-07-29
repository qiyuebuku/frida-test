"""持仓仓储抽象接口"""
from abc import ABC, abstractmethod


class PositionRepository(ABC):
    """ft_positions 仓储"""

    @abstractmethod
    def find_live_positions(self) -> list[dict]:
        """读所有真实持仓 (total_cost > 0)"""
        ...

    @abstractmethod
    def upsert_after_buy(
        self, fund_code: str, fund_name: str, amount: float, shares: float,
    ) -> None:
        """LIVE 模式买入后增量更新持仓"""
        ...
