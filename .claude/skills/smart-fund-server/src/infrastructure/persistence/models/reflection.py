"""复盘与反馈 ORM 模型(对应 schema/04_reflection.sql)

2 张表:
- Review: 决策复盘(T+1/T+2 收益回填 + outcome 判断)
- Lesson: 经验教训(从 review 提炼的可复用规则)
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, Integer, Numeric, String, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


# ==================== ft_reviews ====================


class Review(Base):
    """决策复盘(17 列)

    流程: ft_trades(T-N) → 拿 ETF 净值 → 计算 T+1/T+2 收益 → 判断 outcome
    outcome: correct / wrong / neutral / pending
    胜率统计: 写入 ft_config.event_driven_winrate,反哺动态仓位
    """
    __tablename__ = "ft_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int | None] = mapped_column(
        Integer, comment="关联的 ft_pending_decisions.id 或 ft_trades.id"
    )
    fund_code: Mapped[str] = mapped_column(String, nullable=False)
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)

    decision_action: Mapped[str | None] = mapped_column(String, comment="buy/sell/hold")
    decision_reason: Mapped[str | None] = mapped_column(Text)

    nav_at_decision: Mapped[Decimal | None] = mapped_column(
        Numeric, comment="决策当天的净值"
    )
    nav_t1: Mapped[Decimal | None] = mapped_column(Numeric, comment="T+1 净值")
    nav_t2: Mapped[Decimal | None] = mapped_column(Numeric, comment="T+2 净值")
    change_t1_pct: Mapped[Decimal | None] = mapped_column(
        Numeric, comment="T+1 涨跌幅 %"
    )
    change_t2_pct: Mapped[Decimal | None] = mapped_column(
        Numeric, comment="T+2 涨跌幅 %"
    )

    outcome: Mapped[str | None] = mapped_column(
        String, default="pending", comment="correct/wrong/neutral/pending"
    )
    review_notes: Mapped[str | None] = mapped_column(Text, comment="复盘备注")
    lesson_extracted: Mapped[bool | None] = mapped_column(
        Boolean, default=False, comment="是否已提炼为 lesson"
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )

    decision_source: Mapped[str | None] = mapped_column(
        String, default="llm", comment="llm/event_driven"
    )
    dry_run: Mapped[bool | None] = mapped_column(Boolean, default=False)

    def __repr__(self) -> str:
        return (
            f"<Review id={self.id} {self.fund_code} {self.decision_date} "
            f"outcome={self.outcome}>"
        )


# ==================== ft_lessons ====================


class Lesson(Base):
    """经验教训(16 列)

    从 ft_reviews 提炼的可复用规则,例:
    "周五尾盘加仓军工 → 大概率亏损"
    "外资连续买入 + 板块过热 → 三天内见顶"

    status:
    - active: 仍在用
    - superseded: 被新规则替代(superseded_by 指向新 lesson)
    - inactive: 失效
    """
    __tablename__ = "ft_lessons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(
        String, nullable=False, comment="lesson 分类,如 timing/sector/risk"
    )
    trigger_pattern: Mapped[str | None] = mapped_column(
        Text, comment="触发条件描述"
    )
    expected_outcome: Mapped[str | None] = mapped_column(Text, comment="预期结果")
    actual_outcome: Mapped[str | None] = mapped_column(Text, comment="实际结果")
    lesson_text: Mapped[str] = mapped_column(
        Text, nullable=False, comment="lesson 主体描述"
    )
    confidence: Mapped[str | None] = mapped_column(
        String, default="low", comment="high/medium/low"
    )
    status: Mapped[str | None] = mapped_column(
        String, default="active", comment="active/superseded/inactive"
    )
    verify_count: Mapped[int | None] = mapped_column(
        Integer, default=1, comment="被验证次数"
    )
    success_count: Mapped[int | None] = mapped_column(
        Integer, default=0, comment="验证成功次数"
    )
    related_sectors: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), comment="相关行业列表"
    )
    tags: Mapped[dict | None] = mapped_column(JSONB, comment="自由标签")
    source_review_ids: Mapped[list[int] | None] = mapped_column(
        ARRAY(Integer), comment="提炼来源的 review ids"
    )
    superseded_by: Mapped[int | None] = mapped_column(
        Integer, comment="被哪个新 lesson 替代"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Lesson id={self.id} {self.category}/{self.status}>"
