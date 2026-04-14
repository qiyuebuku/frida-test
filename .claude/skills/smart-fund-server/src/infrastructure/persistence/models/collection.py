"""数据采集层 ORM 模型(对应 schema/01_collection.sql)

6 张表:
- News: 新闻原始记录
- MarketFlow: 资金流(北向/板块/个股/龙虎榜)
- MarketCache: 市场快照(覆盖式)
- Sentiment: 情绪舆情
- MacroIndicator: 宏观指标(EM + PBOC)
- CollectionState: 采集 checkpoint + 调度元数据
"""
from datetime import date, datetime

from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from src.infrastructure.persistence.models.base import Base


# ==================== ft_news ====================


class News(Base):
    """新闻原始记录(15 列)

    多源采集 → 跨源相似度去重(difflib.SequenceMatcher)
    fingerprint = SHA256(title + source) 防同源重复
    event_extracted = True 表示已被 AI 事件抽取处理过
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
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    event_extracted: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否已被 AI 事件抽取处理"
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


# ==================== ft_macro_indicators ====================


class MacroIndicator(Base):
    """宏观指标(EM 9 个 + PBOC shibor/lpr/usdcny)"""
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
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<MacroIndicator {self.indicator} {self.period}={self.value}{self.unit}>"


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

    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<CollectionState {self.aggregator}:{self.source_name} mode={self.mode}>"


# ==================== ft_watchlist_data ====================


class WatchlistData(Base):
    """自选标的采集数据（基金净值/持仓/个股资金流/行情等）

    所有维度用 data_type 区分，按 (code, data_type, trade_date) 去重。
    """
    __tablename__ = "ft_watchlist_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="股票/基金代码"
    )
    data_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="数据维度: nav/holdings/stock_flow/..."
    )
    trade_date: Mapped[date | None] = mapped_column(
        Date, comment="数据日期"
    )
    data: Mapped[dict] = mapped_column(
        JSONB, nullable=False, comment="数据内容"
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<WatchlistData {self.code}:{self.data_type} {self.trade_date}>"
