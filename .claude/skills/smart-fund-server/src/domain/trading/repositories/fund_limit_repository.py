"""基金限购仓储抽象接口"""
from abc import ABC, abstractmethod


class FundLimitRepository(ABC):
    """ft_fund_limits 仓储"""

    @abstractmethod
    def get(self, fund_code: str) -> dict | None:
        """读基金的限购信息

        Returns: {min_buy, max_buy, daily_limit, is_suspended} 或 None
        """
        ...
