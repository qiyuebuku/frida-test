"""宏观指标仓储抽象接口"""
from abc import ABC, abstractmethod
from datetime import date


class MacroRepository(ABC):
    """宏观指标仓储 (UNIQUE on indicator+period+source)"""

    @abstractmethod
    def upsert_batch(self, items: list[dict]) -> int:
        """批量插入

        items 每条 = {
            indicator: str,
            period: str,
            value: float,
            unit: str,
            prev_value: float | None,
            source: str,
            published_at: date | None,
        }
        ON CONFLICT (indicator, period, source) DO NOTHING
        """
        ...

    @abstractmethod
    def latest_per_indicator(self) -> list[dict]:
        """每个 indicator 的最新一条(监控/查询用)"""
        ...
