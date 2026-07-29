"""宏观指标仓储抽象接口"""
from abc import ABC, abstractmethod


class MacroRepository(ABC):
    """宏观指标仓储 (UNIQUE on indicator+period+source)"""

    @abstractmethod
    def upsert_batch(self, items: list[dict]) -> int:
        """批量 UPSERT 宏观指标

        items 每条 = {
            indicator, period, value, unit, prev_value,
            source, published_at, dim_tag, yoy, mom,
        }
        ON CONFLICT (indicator, period, source) DO UPDATE
        """
        ...

    @abstractmethod
    def latest_per_indicator(self) -> list[dict]:
        """每个 indicator 的最新一条"""
        ...

    @abstractmethod
    def latest_by_dim(self, dim_tag: str) -> list[dict]:
        """按维度标签查最新值"""
        ...

    @abstractmethod
    def upsert_regime(self, row: dict) -> None:
        """写入/更新 ft_macro_regime (UPSERT on snapshot_date)"""
        ...

    @abstractmethod
    def get_current_regime(self) -> dict | None:
        """读最新 regime 快照"""
        ...

    @abstractmethod
    def get_regime_history(self, days: int = 30) -> list[dict]:
        """读近 N 天 regime 历史"""
        ...
