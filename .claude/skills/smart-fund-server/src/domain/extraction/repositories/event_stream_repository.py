"""事件流仓储抽象接口"""
from abc import ABC, abstractmethod


class EventStreamRepository(ABC):
    """事件流仓储 (ft_event_streams 表)"""

    @abstractmethod
    def delete_active_streams(self) -> int:
        """删除所有非 closed 的 stream (每次 tick 重建)

        closed 的归档保留,active 的 (emerging/fermenting/peak/decaying) 删除。
        """
        ...

    @abstractmethod
    def insert_stream(self, stream_data: dict) -> bool:
        """插入一条 stream

        stream_data: {
            industry, state, momentum, event_ids (list[int]), event_count,
            avg_strength, max_strength, avg_sentiment,
            first_event_at, last_event_at,
        }
        """
        ...

    @abstractmethod
    def find_active(self) -> list[dict]:
        """读所有 state in (emerging, fermenting, peak) 的 stream"""
        ...

    @abstractmethod
    def find_latest_by_industries(self, industries: list[str]) -> dict | None:
        """根据行业列表取最新一条 stream (持仓监控反查用)"""
        ...
