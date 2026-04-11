"""新闻仓储抽象接口"""
from abc import ABC, abstractmethod
from datetime import date
from typing import Any


class NewsRepository(ABC):
    """新闻仓储

    domain 层只看到这个接口,不看到 SQLAlchemy。
    实现见 infrastructure/persistence/repositories/news_repository_impl.py
    """

    @abstractmethod
    def upsert_batch(self, items: list[dict]) -> int:
        """批量插入新闻,fingerprint 冲突跳过

        Args:
            items: list of dict,字段对应 ft_news 列名
        Returns:
            实际新增条数
        """
        ...

    @abstractmethod
    def find_today_titles(self, today: date | None = None) -> list[str]:
        """跨源相似度去重: 取今日已入库的所有标题"""
        ...

    @abstractmethod
    def find_unextracted(self, limit: int = 30) -> list[dict]:
        """事件抽取读取: 未被 AI 抽取过的新闻

        Returns:
            list of dict,每个 dict 包含 id/title/summary/content/source/source_name/
            source_reliability/category/url/published_at
        """
        ...

    @abstractmethod
    def mark_extracted(self, ids: list[int]) -> int:
        """事件抽取完成后标记 event_extracted=True"""
        ...

    @abstractmethod
    def count(self) -> int:
        """全表行数(用于监控)"""
        ...
