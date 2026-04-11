"""事件抽取层 ORM 模型(对应 schema/02_extraction.sql)

2 张表:
- Event: AI 抽取出的结构化事件(28 列含 embedding bytea)
- EventStream: 事件流聚合(13 列含 ARRAY[Integer] event_ids)
"""
from datetime import datetime

from sqlalchemy import (
    DateTime, Float, Integer, LargeBinary, String, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


# ==================== ft_events ====================


class Event(Base):
    """AI 抽取出的结构化事件(28 列)

    上游: ft_news.event_extracted=False 的新闻 → claude CLI 抽取
    下游: ft_event_streams 按 industry 聚类
    embedding: Qwen3-Embedding-4B 1024 维 fp32 → 4096 字节 bytea
    fingerprint: SHA256(title|event_type) 防重复抽取
    """
    __tablename__ = "ft_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="事件标题")
    summary: Mapped[str] = mapped_column(Text, default="", comment="事件摘要")
    event_type: Mapped[str] = mapped_column(
        String, nullable=False, comment="policy/macro/industry/company/capital"
    )
    event_subtype: Mapped[str] = mapped_column(String, default="")

    # 实体
    industries: Mapped[list] = mapped_column(JSONB, default=list, comment="涉及行业")
    companies: Mapped[list] = mapped_column(JSONB, default=list, comment="涉及公司")
    organizations: Mapped[list] = mapped_column(JSONB, default=list, comment="涉及机构")
    regions: Mapped[list] = mapped_column(JSONB, default=list, comment="涉及地区")

    # 影响
    impact_direction: Mapped[str | None] = mapped_column(
        String, comment="positive/negative/neutral"
    )
    impact_strength: Mapped[float | None] = mapped_column(
        Float, default=0, comment="影响强度 0-1"
    )
    impact_scope: Mapped[str | None] = mapped_column(String, comment="影响范围")
    impact_duration: Mapped[str | None] = mapped_column(String, comment="影响持续时间")

    # AI 评估
    sentiment: Mapped[float | None] = mapped_column(
        Float, default=0.5, comment="情绪 0-1 (0.5=中性)"
    )
    novelty: Mapped[float | None] = mapped_column(Float, default=0.5, comment="新颖度")
    certainty: Mapped[float | None] = mapped_column(Float, default=0.5, comment="确定性")

    # 来源
    source_news_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), default=list, comment="来源 ft_news.id 列表"
    )

    # Embedding
    embedding: Mapped[bytes | None] = mapped_column(
        LargeBinary, comment="向量(1024 维 float32 → 4096 字节)"
    )
    embedding_model: Mapped[str | None] = mapped_column(
        String, default="", comment="向量模型标识 qwen3-emb-4b"
    )

    # 市场反应回填(agg_event_feedback 写入)
    sector_change_1d: Mapped[float | None] = mapped_column(
        Float, comment="T+1 行业涨跌幅"
    )
    sector_change_3d: Mapped[float | None] = mapped_column(Float, comment="T+3 行业涨跌幅")
    sector_volume_change: Mapped[float | None] = mapped_column(
        Float, comment="行业成交量变化(代理: sector_flow.net_amount)"
    )
    north_flow_1d: Mapped[float | None] = mapped_column(Float, comment="T+1 北向净流入")
    reaction_delay_minutes: Mapped[int | None] = mapped_column(
        Integer, comment="市场反应延迟(分钟)"
    )
    feedback_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="回填时间戳"
    )

    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="事件发生时间(取自 source_news.published_at)"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    fingerprint: Mapped[str] = mapped_column(
        String, nullable=False, comment="SHA256(title|event_type) 去重"
    )

    def __repr__(self) -> str:
        return f"<Event id={self.id} type={self.event_type} title={self.title[:24]!r}>"


# ==================== ft_event_streams ====================


class EventStream(Base):
    """事件流(按 industry 聚类的事件集合,13 列)

    每个 stream = 一个 industry 在最近 24h 的事件聚类(贪心余弦相似度,阈值 0.78)
    state 状态机: emerging → fermenting → peak → decaying → closed
    momentum: rising / stable / falling
    """
    __tablename__ = "ft_event_streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry: Mapped[str] = mapped_column(String, nullable=False, comment="行业名(取自事件 industries[0])")
    state: Mapped[str] = mapped_column(
        String, nullable=False, default="emerging",
        comment="emerging/fermenting/peak/decaying/closed"
    )
    momentum: Mapped[str | None] = mapped_column(
        String, default="stable", comment="rising/stable/falling"
    )

    event_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), default=list, comment="聚类内 ft_events.id 列表"
    )
    event_count: Mapped[int | None] = mapped_column(Integer, default=0)

    avg_strength: Mapped[float | None] = mapped_column(Float, default=0, comment="平均影响强度")
    max_strength: Mapped[float | None] = mapped_column(Float, default=0)
    avg_sentiment: Mapped[float | None] = mapped_column(Float, default=0.5)

    first_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="聚类内最早事件时间"
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="聚类内最晚事件时间(state 判断依赖)"
    )

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<EventStream id={self.id} {self.industry}/{self.state}/{self.momentum} "
            f"events={self.event_count}>"
        )
