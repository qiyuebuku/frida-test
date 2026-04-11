"""情绪舆情仓储抽象接口"""
from abc import ABC, abstractmethod
from datetime import date


class SentimentRepository(ABC):
    """情绪舆情仓储"""

    @abstractmethod
    def upsert_batch(self, items: list[dict]) -> int:
        """批量插入

        items 每条 = {data_type, trade_date, data: dict}
        """
        ...

    @abstractmethod
    def count_by_type(self) -> dict[str, int]:
        ...
