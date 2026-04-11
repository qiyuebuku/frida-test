"""交易决策与执行 ORM 模型(对应 schema/03_trading.sql)

8 张表:
- PendingDecision: 待执行决策(event_driven 决策的中间状态)
- Decision: LLM 决策(旧表,保留兼容)
- Trade: 交易记录(dry_run + live)
- Position: 持仓
- IndustryMapping: 行业 → keywords / aliases
- IndustryIndex: industry_id → index_code
- IndexFund: index_code → fund_code
- FundLimit: 基金限购信息
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, Float, Integer, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


# ==================== ft_pending_decisions ====================


class PendingDecision(Base):
    """待执行决策 - 决策与执行之间的中间状态(23 列)

    decision_source 区分两类:
    - llm: 旧的 LLM 决策(由 Claude 整体打分)
    - event_driven: 事件驱动决策(由 EventDrivenDecider 打分)

    生命周期:
    pending → executed (TradeExecutor 处理后)
            → cancelled (硬约束失败)
    """
    __tablename__ = "ft_pending_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String, nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, nullable=False, comment="buy/sell/watch/hold")
    amount: Mapped[Decimal | None] = mapped_column(Numeric, comment="买入金额")
    sell_pct: Mapped[Decimal | None] = mapped_column(Numeric, comment="卖出百分比")
    reason: Mapped[str | None] = mapped_column(Text, comment="决策理由")
    confidence: Mapped[str | None] = mapped_column(String, comment="high/medium/low")
    market_view: Mapped[str | None] = mapped_column(Text, comment="市场判断")
    market_phase: Mapped[str | None] = mapped_column(String, comment="市场阶段")
    risk_notes: Mapped[str | None] = mapped_column(Text)
    decision_date: Mapped[date | None] = mapped_column(Date, default=date.today)
    status: Mapped[str | None] = mapped_column(
        String, default="pending", comment="pending/executed/cancelled"
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )

    # event_driven 决策专属
    event_stream_id: Mapped[int | None] = mapped_column(
        Integer, comment="关联的 ft_event_streams.id"
    )
    source_event_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), comment="参与打分的事件 id"
    )
    score: Mapped[float | None] = mapped_column(Float, comment="综合打分")
    score_breakdown: Mapped[dict | None] = mapped_column(
        JSONB, default=dict, comment="分项细节"
    )
    decision_source: Mapped[str | None] = mapped_column(
        String, default="llm", comment="llm/event_driven"
    )
    dry_run: Mapped[bool | None] = mapped_column(
        Boolean, default=True, comment="是否模拟(决策本身的 dry_run 标记)"
    )

    def __repr__(self) -> str:
        return f"<PendingDecision id={self.id} {self.action} {self.fund_code} status={self.status}>"


# ==================== ft_decisions ====================


class Decision(Base):
    """LLM 决策(旧表,13 列)

    保留向后兼容,新流程用 PendingDecision。
    """
    __tablename__ = "ft_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String, nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, nullable=False)
    amount: Mapped[Decimal | None] = mapped_column(Numeric)
    sell_pct: Mapped[Decimal | None] = mapped_column(Numeric)
    reason: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[str | None] = mapped_column(String)
    market_view: Mapped[str | None] = mapped_column(Text)
    risk_notes: Mapped[str | None] = mapped_column(Text)
    referenced_lesson_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), comment="参考的 ft_lessons.id"
    )
    decision_date: Mapped[date | None] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Decision id={self.id} {self.action} {self.fund_code}>"


# ==================== ft_trades ====================


class Trade(Base):
    """交易记录(13 列)

    dry_run=True: 模拟交易,order_no 形如 DRY_xxx
    dry_run=False: 真实下单,order_no 来自天天基金 API
    """
    __tablename__ = "ft_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String, nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String)
    action: Mapped[str] = mapped_column(String, nullable=False, comment="buy/sell")
    amount: Mapped[Decimal | None] = mapped_column(Numeric, comment="金额")
    shares: Mapped[Decimal | None] = mapped_column(Numeric, comment="份额")
    order_no: Mapped[str | None] = mapped_column(String, comment="订单号")
    reason: Mapped[str | None] = mapped_column(Text)
    api_response: Mapped[dict | None] = mapped_column(JSONB, comment="API 原始返回")
    trade_date: Mapped[date | None] = mapped_column(Date, default=date.today)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    dry_run: Mapped[bool | None] = mapped_column(
        Boolean, default=True, comment="是否模拟"
    )
    decision_id: Mapped[int | None] = mapped_column(
        Integer, comment="关联的 ft_pending_decisions.id"
    )

    def __repr__(self) -> str:
        return f"<Trade id={self.id} {self.action} {self.fund_code} dry={self.dry_run}>"


# ==================== ft_positions ====================


class Position(Base):
    """持仓(12 列)

    LIVE 模式下由 TradeExecutor 实时维护
    DRY_RUN 模式下从 ft_trades 聚合(虚拟 paper position)
    """
    __tablename__ = "ft_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String, nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String)
    total_cost: Mapped[Decimal | None] = mapped_column(Numeric, default=0)
    shares: Mapped[Decimal | None] = mapped_column(Numeric, default=0)
    avg_cost: Mapped[Decimal | None] = mapped_column(Numeric, default=0)
    current_nav: Mapped[Decimal | None] = mapped_column(Numeric, default=0)
    market_value: Mapped[Decimal | None] = mapped_column(Numeric, default=0)
    profit_pct: Mapped[Decimal | None] = mapped_column(
        Numeric, default=0, comment="盈亏百分比"
    )
    first_buy_date: Mapped[date | None] = mapped_column(Date, default=date.today)
    add_count: Mapped[int | None] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Position {self.fund_code} cost={self.total_cost} pnl={self.profit_pct}%>"


# ==================== ft_industry_mapping ====================


class IndustryMapping(Base):
    """行业映射(7 列)

    industry_id 是稳定的 slug,industry_name 是中文名
    keywords + aliases 用于事件 industry 字符串到 industry_id 的模糊匹配
    """
    __tablename__ = "ft_industry_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_id: Mapped[str] = mapped_column(
        String, nullable=False, comment="稳定 slug,如 semiconductor"
    )
    industry_name: Mapped[str] = mapped_column(
        String, nullable=False, comment="中文名,如 半导体"
    )
    keywords: Mapped[list] = mapped_column(
        JSONB, default=list, comment="关键词数组(用于模糊匹配)"
    )
    aliases: Mapped[list] = mapped_column(JSONB, default=list, comment="别名数组")
    priority: Mapped[float | None] = mapped_column(
        Float, default=0.5, comment="行业重要性(决策打分加权)"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<IndustryMapping {self.industry_id}({self.industry_name})>"


# ==================== ft_industry_index ====================


class IndustryIndex(Base):
    """行业 → 指数映射(6 列)"""
    __tablename__ = "ft_industry_index"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    industry_id: Mapped[str] = mapped_column(String, nullable=False)
    index_code: Mapped[str] = mapped_column(
        String, nullable=False, comment="指数代码,如 980017"
    )
    index_name: Mapped[str] = mapped_column(String, nullable=False)
    weight: Mapped[float | None] = mapped_column(
        Float, default=1.0, comment="同 industry 多个指数时的权重"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<IndustryIndex {self.industry_id} → {self.index_code}>"


# ==================== ft_index_fund ====================


class IndexFund(Base):
    """指数 → 跟踪基金映射(9 列)"""
    __tablename__ = "ft_index_fund"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    index_code: Mapped[str] = mapped_column(String, nullable=False)
    fund_code: Mapped[str] = mapped_column(String, nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String)
    fund_type: Mapped[str | None] = mapped_column(String, default="ETF")
    fee_rate: Mapped[float | None] = mapped_column(Float, default=0, comment="管理费率")
    tracking_error: Mapped[float | None] = mapped_column(
        Float, default=0, comment="跟踪误差"
    )
    liquidity_score: Mapped[float | None] = mapped_column(
        Float, default=0.5, comment="流动性评分(0-1,选基金优先级)"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<IndexFund {self.index_code} → {self.fund_code} {self.fund_name}>"


# ==================== ft_fund_limits ====================


class FundLimit(Base):
    """基金限购信息(11 列)

    决策执行前必须查 is_suspended,暂停申购的基金不能买入
    """
    __tablename__ = "ft_fund_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_code: Mapped[str] = mapped_column(String, nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String)
    min_buy: Mapped[Decimal | None] = mapped_column(Numeric, default=0, comment="起购金额")
    max_buy: Mapped[Decimal | None] = mapped_column(Numeric, default=0, comment="单笔限额")
    daily_limit: Mapped[Decimal | None] = mapped_column(Numeric, default=0, comment="日限额")
    is_suspended: Mapped[bool | None] = mapped_column(
        Boolean, default=False, comment="是否暂停申购"
    )
    suspend_reason: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<FundLimit {self.fund_code} suspended={self.is_suspended}>"
