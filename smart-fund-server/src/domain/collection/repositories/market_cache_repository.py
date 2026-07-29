"""市场快照缓存仓储抽象接口"""
from abc import ABC, abstractmethod
from datetime import datetime


class MarketCacheRepository(ABC):
    """市场缓存仓储 (覆盖式,unique on data_type)"""

    @abstractmethod
    def upsert(
        self, data_type: str, data: dict, expires_at: datetime,
    ) -> None:
        """覆盖式写入: 同 data_type 只保留最新一行"""
        ...

    @abstractmethod
    def find_by_type(self, data_type: str) -> dict | None:
        """读取某 data_type 的最新快照"""
        ...

    @abstractmethod
    def all_types(self) -> list[str]:
        """列出所有已缓存的 data_type"""
        ...
