"""数据采集层 ORM 模型(对应 schema/01_collection.sql)

7 张表:
- News: 新闻原始记录
- MarketFlow: 资金流(北向/板块/个股/龙虎榜)
- MarketCache: 市场快照(覆盖式)
- Sentiment: 情绪舆情
- SentimentSignal: L2 情绪信号日度快照
- MacroIndicator: 宏观指标(EM + PBOC)
- CollectionState: 采集 checkpoint + 调度元数据
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


# ==================== ft_news ====================


class News(Base):
    """新闻原始记录。

    多源采集先执行同源指纹、业务日标题和完整正文三层去重；
    event_extracted=True 表示已被知识抽取流程处理。
    """
    __tablename__ = "ft_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="新闻标题")
    content: Mapped[str] = mapped_column(Text, default="", comment="正文(详情页抓取)")
    summary: Mapped[str] = mapped_column(Text, default="", comment="列表页摘要")
    source: Mapped[str] = mapped_column(String, nullable=False, comment="数据源标识 cls/ths/sina/...")
    source_name: Mapped[str] = mapped_column(String, default="", comment="数据源中文名")
    source_reliability: Mapped[float] = mapped_column(Float, default=0.5, comment="数据源可信度 0-1")
    category: Mapped[str] = mapped_column(String, default="", comment="分类 macro/policy/company")
    url: Mapped[str] = mapped_column(Text, default="", comment="原文 URL")
    tags: Mapped[list] = mapped_column(JSONB, default=list, comment="标签数组")
    related_stocks: Mapped[list] = mapped_column(JSONB, default=list, comment="关联股票代码")
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="原文发布时间"
    )
    fingerprint: Mapped[str] = mapped_column(
        String, nullable=False, comment="SHA256(title+source) 去重指纹"
    )
    news_kind: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="news",
        comment="稳定内容类型: news/market_recap/market_preview/research_report",
    )
    dedup_key: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="业务日+归一化标题的跨来源去重键",
    )
    content_fingerprint: Mapped[str | None] = mapped_column(
        String(64),
        comment="非空完整正文归一化后的 SHA256",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_extracted: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已被 AI 事件抽取处理"
    )
    l1_classified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="L1a 分类器处理时间"
    )

    def __repr__(self) -> str:
        return f"<News id={self.id} src={self.source} title={self.title[:24]!r}>"


# ==================== ft_market_flow ====================


class MarketFlow(Base):
    """资金流(北向/板块/个股主力/龙虎榜,用 data_type 区分)

    data_type 取值:
    - northbound: 北向陆股通
    - sector_flow: 板块资金流
    - stock_flow: 个股主力资金(已展开 history 为每天一行)
    - dragon_tiger: 龙虎榜
    """
    __tablename__ = "ft_market_flow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_type: Mapped[str] = mapped_column(
        String, nullable=False, comment="northbound/sector_flow/stock_flow/dragon_tiger"
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="原始数据 JSONB")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    def __repr__(self) -> str:
        return f"<MarketFlow id={self.id} type={self.data_type} date={self.trade_date}>"


# ==================== ft_market_cache ====================


class MarketCache(Base):
    """市场数据快照缓存(覆盖式,unique on data_type)

    与 ft_market_flow 不同:
    - market_flow 是历史表,按 trade_date 累积
    - market_cache 是覆盖式,每个 data_type 只保留最新一条
    """
    __tablename__ = "ft_market_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_type: Mapped[str] = mapped_column(
        String, nullable=False, comment="sector_ranking/index_quote/global_market..."
    )
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False, comment="缓存过期时间"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<MarketCache id={self.id} type={self.data_type}>"


# ==================== ft_sentiment ====================


class Sentiment(Base):
    """情绪舆情(股吧人气/雪球/涨停板/热股,用 data_type 区分)"""
    __tablename__ = "ft_sentiment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_type: Mapped[str] = mapped_column(
        String, nullable=False, comment="guba_popularity/guba_posts/xueqiu/zhangting/..."
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<Sentiment id={self.id} type={self.data_type} date={self.trade_date}>"


# ==================== ft_sentiment_signal ====================


class SentimentSignal(Base):
    """L2 情绪派生信号日度快照（每日一行）"""
    __tablename__ = "ft_sentiment_signal"

    snapshot_date: Mapped[date] = mapped_column(Date, primary_key=True, comment="快照日期（PK）")
    market_temperature: Mapped[int] = mapped_column(Integer, nullable=False, comment="市场温度 0-100")
    market_level: Mapped[str] = mapped_column(String(8), nullable=False, comment="cold/cool/warm/hot/extreme")
    market_trend: Mapped[str | None] = mapped_column(String(16), comment="rising/falling/stable")
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="get_market_temperature() 完整返回")
    overheat_codes: Mapped[dict | None] = mapped_column(JSONB, comment="{codes: {code: penalty}}")
    leading_theme: Mapped[dict | None] = mapped_column(JSONB, comment="{theme, confidence}")
    sentiment_agg: Mapped[dict | None] = mapped_column(JSONB, comment="{overall_score, industry_scores}")
    contributors: Mapped[dict | None] = mapped_column(JSONB, comment="解释性数据")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<SentimentSignal {self.snapshot_date} temp={self.market_temperature}>"


# ==================== ft_macro_indicators ====================


class MacroIndicator(Base):
    """宏观指标(EM 14+ 个 + PBOC shibor/lpr/usdcny/omo)"""
    __tablename__ = "ft_macro_indicators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    indicator: Mapped[str] = mapped_column(
        String, nullable=False, comment="cpi/ppi/pmi/gdp/m2/.../shibor_on/lpr_1y/usdcny"
    )
    period: Mapped[str] = mapped_column(
        String, nullable=False, comment="期间 YYYY-MM 或 YYYY-MM-DD"
    )
    value: Mapped[float] = mapped_column(Float, nullable=False, comment="数值")
    unit: Mapped[str] = mapped_column(String, default="", comment="单位 % / 亿元 / 亿美元")
    prev_value: Mapped[float | None] = mapped_column(Float, comment="上期值(用于环比)")
    source: Mapped[str] = mapped_column(String, default="", comment="数据源 eastmoney/pboc")
    published_at: Mapped[date | None] = mapped_column(Date, comment="发布日期")
    dim_tag: Mapped[str] = mapped_column(
        String(16), default="", comment="维度标签 liquidity/growth/inflation/external/policy"
    )
    yoy: Mapped[float | None] = mapped_column(Float, comment="同比 %")
    mom: Mapped[float | None] = mapped_column(Float, comment="环比 %")
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<MacroIndicator {self.indicator} {self.period}={self.value}{self.unit}>"


# ==================== ft_macro_regime ====================


class MacroRegime(Base):
    """宏观 regime 信号(五维度加权打分 → regime + multiplier)"""
    __tablename__ = "ft_macro_regime"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="计算时间"
    )
    snapshot_date: Mapped[date] = mapped_column(
        Date, nullable=False, unique=True, comment="业务日"
    )
    regime: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="risk_off / neutral / risk_on"
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False, comment="[-1, 1]")
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, comment="[0.6, 1.2]")
    liquidity_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    growth_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    inflation_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    external_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    policy_score: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    contributors: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False, comment="指标级贡献明细"
    )

    def __repr__(self) -> str:
        return f"<MacroRegime {self.snapshot_date} {self.regime} mul={self.multiplier}>"


# ==================== ft_collection_state ====================


class CollectionState(Base):
    """采集 checkpoint + 调度元数据(每个 (aggregator, source_name) 一行)

    替代旧的"目标表反查 MAX(trade_date)"方案,统一管理:
    - checkpoint: 增量游标(JSONB,结构由 source 自定义)
    - last_run_at: 调度判断的真正 source of truth
    - enabled / interval_override: 运行时控制
    - consecutive_failures / last_error: 监控告警
    """
    __tablename__ = "ft_collection_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aggregator: Mapped[str] = mapped_column(
        String, nullable=False, comment="news/fund_flow/macro/sentiment/market"
    )
    source_name: Mapped[str] = mapped_column(
        String, nullable=False, comment="northbound/sector_flow_sina/cls/em_news/..."
    )

    # 采集进度（原 checkpoint JSONB 提升为独立列）
    mode: Mapped[str | None] = mapped_column(
        String(16), default="incremental", comment="backfill / incremental"
    )
    target_time: Mapped[str | None] = mapped_column(
        String(32), comment="回填目标时间"
    )
    newest_time: Mapped[str | None] = mapped_column(
        String(32), comment="已采集最新时间"
    )
    oldest_time: Mapped[str | None] = mapped_column(
        String(32), comment="已采集最早时间"
    )
    backfill_status: Mapped[str | None] = mapped_column(
        String(16), comment="done / ceiling"
    )
    cursor: Mapped[dict | None] = mapped_column(
        JSONB, comment="翻页游标（类型因源而异）"
    )

    config: Mapped[dict] = mapped_column(
        JSONB, default=dict, comment="采集参数 {target_days,page_size,...}"
    )

    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="上次开跑时间(调度判断的权威)"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="上次成功时间"
    )
    last_error: Mapped[str | None] = mapped_column(
        Text, default="", comment="最近一次错误信息"
    )
    consecutive_failures: Mapped[int | None] = mapped_column(
        Integer, default=0, comment="连续失败次数"
    )

    enabled: Mapped[bool | None] = mapped_column(
        Boolean, default=True, comment="是否启用(运行时禁用某 source 不需停 worker)"
    )
    interval_override: Mapped[int | None] = mapped_column(
        Integer, comment="覆盖代码默认 interval(秒)"
    )

    total_runs: Mapped[int | None] = mapped_column(BigInteger, default=0, comment="累计运行次数")
    total_saved: Mapped[int | None] = mapped_column(BigInteger, default=0, comment="累计入库行数")

    # 统一任务状态投影。checkpoint/backfill 字段继续服务业务恢复；以下字段只
    # 描述任务当前运行状态，拉取、回调和服务端推送使用同一套语义。
    task_id: Mapped[str | None] = mapped_column(
        String(192), index=True, comment="对应 scheduled_tasks.scheduler_id 或推送任务 ID"
    )
    task_type: Mapped[str | None] = mapped_column(
        String(24), comment="pull/callback/push/internal/backfill"
    )
    status: Mapped[str | None] = mapped_column(
        String(24), default="pending", comment="pending/running/success/skipped/delayed/failed"
    )
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_source_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_persisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    last_fetched_count: Mapped[int | None] = mapped_column(BigInteger, default=0)
    last_saved_count: Mapped[int | None] = mapped_column(BigInteger, default=0)
    total_received: Mapped[int | None] = mapped_column(BigInteger, default=0)
    runtime_details: Mapped[dict] = mapped_column(JSONB, default=dict)

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<CollectionState {self.aggregator}:{self.source_name} mode={self.mode}>"


class InstrumentProfile(Base):
    """Current descriptive attributes for a tracked instrument."""

    __tablename__ = "ft_instrument_profiles"
    __table_args__ = (
        UniqueConstraint(
            "code", "data_type", "provider",
            name="uq_ft_instrument_profiles_identity",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InstrumentDisclosure(Base):
    """Periodic holdings, scale and ownership disclosures."""

    __tablename__ = "ft_instrument_disclosures"
    __table_args__ = (
        UniqueConstraint(
            "code", "data_type", "provider", "report_date",
            name="uq_ft_instrument_disclosures_identity",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class InstrumentObservation(Base):
    """Date-stamped non-market facts and derived instrument metrics."""

    __tablename__ = "ft_instrument_observations"
    __table_args__ = (
        UniqueConstraint(
            "code", "data_type", "provider", "observation_date",
            name="uq_ft_instrument_observations_identity",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    observation_date: Mapped[date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ==================== ft_market_snapshots ====================


class MarketSnapshot(Base):
    """盘中市场与标的历史快照。

    每个时间桶只保存一个来源口径的观测；同一交易日的不同时间桶不会互相覆盖。
    """

    __tablename__ = "ft_market_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "data_type",
            "subject_id",
            "provider",
            "bucket_at",
            name="uq_ft_market_snapshots_bucket",
        ),
        Index(
            "ix_ft_market_snapshots_subject_time",
            "subject_id",
            "data_type",
            "bucket_at",
        ),
        Index(
            "ix_ft_market_snapshots_trade_type",
            "trade_date",
            "data_type",
        ),
        Index(
            "ix_ft_market_snapshots_freshness",
            "freshness_status",
            "bucket_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    data_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    market: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    bucket_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    freshness_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unknown"
    )
    source_latency_seconds: Mapped[float | None] = mapped_column(Float)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


# ==================== ft_etf_daily_shares ====================


class EtfDailyShare(Base):
    """沪深交易所官方 ETF 日级份额。"""

    __tablename__ = "ft_etf_daily_shares"
    __table_args__ = (
        UniqueConstraint(
            "exchange",
            "code",
            "trade_date",
            name="uq_ft_etf_daily_shares_identity",
        ),
        Index(
            "ix_ft_etf_daily_shares_code_date",
            "code",
            "trade_date",
        ),
        Index(
            "ix_ft_etf_daily_shares_trade_date",
            "trade_date",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    exchange: Mapped[str] = mapped_column(String(8), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    shares: Mapped[Decimal] = mapped_column(Numeric(30, 4), nullable=False)
    share_unit: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


# ==================== ft_collection_runs ====================


class CollectionRun(Base):
    """每次采集执行的审计记录。"""

    __tablename__ = "ft_collection_runs"
    __table_args__ = (
        Index(
            "ix_ft_collection_runs_task_started",
            "task_name",
            "started_at",
        ),
        Index(
            "ix_ft_collection_runs_source_started",
            "source_name",
            "started_at",
        ),
        Index(
            "ix_ft_collection_runs_status_started",
            "status",
            "started_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=True
    )
    task_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="running"
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    saved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_time_min: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    source_time_max: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    checkpoint_before: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    checkpoint_after: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
