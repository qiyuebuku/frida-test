"""复盘仓储抽象接口"""
from abc import ABC, abstractmethod


class ReviewRepository(ABC):
    """ft_reviews 仓储"""

    @abstractmethod
    def find_unreviewed_trades(self, lookback_days: int = 5) -> list[dict]:
        """读 lookback 天内未复盘的交易 (区分 dry_run + live)

        LEFT JOIN ft_reviews WHERE decision_source='event_driven',
        避免与 LLM 决策的 ID 命名空间冲突。
        """
        ...

    @abstractmethod
    def upsert_review(self, review: dict) -> bool:
        """ON CONFLICT (decision_id, fund_code) WHERE decision_id IS NOT NULL DO UPDATE"""
        ...

    @abstractmethod
    def winrate_stats_30d(self) -> dict:
        """近 30 天 event_driven 的胜率统计

        Returns: {total, correct, wrong, neutral, winrate}
        """
        ...
