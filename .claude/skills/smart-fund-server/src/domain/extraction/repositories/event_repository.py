"""事件仓储抽象接口"""
from abc import ABC, abstractmethod
from datetime import datetime


class EventRepository(ABC):
    """事件仓储 (ft_events 表)"""

    @abstractmethod
    def upsert_event(
        self,
        event_data: dict,
        embedding_blob: bytes | None = None,
        embedding_model: str = "",
    ) -> bool:
        """单条事件入库 (fingerprint 冲突跳过)

        event_data 字段:
            title, summary, event_type, event_subtype,
            industries/companies/organizations/regions (list),
            impact_direction/impact_strength/impact_scope/impact_duration,
            sentiment/novelty/certainty,
            source_news_ids (list[int]),
            event_time (datetime),
            fingerprint (SHA256(title|event_type)),

        Returns: True 实际入库, False 冲突跳过 / 写入失败
        """
        ...

    @abstractmethod
    def upsert_l1_event(self, event_data: dict) -> bool:
        """L1 事件入库 (dedup_key 冲突时合并 evidence_refs)

        event_data 需包含 dedup_key 字段。
        冲突时 UPDATE evidence_refs = existing || new，不插入新行。

        Returns: True 实际插入, False 合并更新 / 写入失败
        """
        ...

    @abstractmethod
    def find_by_dedup_key(self, dedup_key: str) -> dict | None:
        """按 dedup_key 查找已存在的事件"""
        ...

    @abstractmethod
    def find_recent_with_embedding(self, hours: int = 24, limit: int = 500) -> list[dict]:
        """读最近 N 小时有 embedding 的事件 (供 EventStream 聚类用)

        Returns:
            list of dict, 每个含 id/title/summary/event_type/industries/
            impact_strength/sentiment/event_time/embedding (bytes)
        """
        ...

    @abstractmethod
    def find_unfilled_market_reaction(self, days: int = 3) -> list[dict]:
        """读 N 天前 ~ T-1 之间未被回填市场反应的事件"""
        ...

    @abstractmethod
    def update_market_reaction(self, event_id: int, reaction: dict) -> bool:
        """回填事件的市场反应字段 (sector_change_1d / north_flow_1d / ...)"""
        ...

    @abstractmethod
    def count_with_embedding(self) -> int:
        ...
