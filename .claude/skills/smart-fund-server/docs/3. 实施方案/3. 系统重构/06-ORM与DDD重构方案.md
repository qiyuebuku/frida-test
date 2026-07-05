# Step 6：ORM 与 DDD 架构重构方案

**目的**：把当前散落各处的 raw SQL 统一到 SQLAlchemy ORM，把按业务功能分目录的代码按 DDD 聚合根重组，参考 `jettask_v2` 的成熟实践。
**状态**：方案设计阶段，未实施。
**适用对象**：执行重构的开发者。
**前置文档**：
- [01-项目初始化.md](./01-项目初始化.md)
- [02-代码平移.md](./02-代码平移.md)
- [03-task_executor拆分.md](./03-task_executor拆分.md)
- [04-应用层与接口层重构.md](./04-应用层与接口层重构.md)
- [05-补齐与优化分析.md](./05-补齐与优化分析.md)

---

## 一、现状与痛点

### 1.1 数据库操作全是 raw SQL

当前 16 张 `ft_*` 表的所有读写都是手写 `psycopg2 + 字符串 SQL`，散落在 ~30 个文件里：

```python
# src/domain/aggregation/event_extraction.py
cur.execute("""
    INSERT INTO ft_events (
        title, summary, event_type, event_subtype,
        industries, companies, organizations, regions,
        impact_direction, impact_strength, impact_scope, impact_duration,
        sentiment, novelty, certainty,
        source_news_ids, event_time, fingerprint,
        embedding, embedding_model
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (fingerprint) DO NOTHING
""", (title, summary, ...))  # ← 20 个占位符顺序对不上就崩
```

**直接后果**：
1. **字段含义全靠记** —— 没有类型注解，新人接手要逐个 `\d table` 查
2. **改 schema 牵一发动全身** —— 加一列要找 N 个 INSERT 语句改占位符
3. **类型错误运行时才暴露** —— `Decimal` vs `float`、`datetime` vs `str`、`list[int]` vs `int[]`
4. **JSONB 字段反序列化散落** —— `json.loads(row['data']) if isinstance(row['data'], str) else row['data']` 这种代码出现 20+ 次
5. **没有事务边界** —— `_save` 一处一个 `get_conn() with conn.cursor()`，跨表更新无法原子
6. **测试困难** —— 没有 ORM 的 in-memory SQLite 替代，单测必须连真实 PG

### 1.2 目录结构按业务模块分，不按聚合根分

```
src/domain/
├── aggregation/      # 5 个采集 + event_extraction + event_stream + event_feedback
├── decision/         # event_driven_decider + trade_executor + trade_monitor + review_engine
├── trading/          # risk_manager + indicators + review_decision_executor
└── task/             # 各种 handler
```

问题：
- **跨界严重**：`event_extraction` 同时操作 `ft_news`（read）和 `ft_events`（write），不属于纯领域逻辑
- **重复抽象**：`trading/` 和 `decision/` 都做交易相关，职责模糊
- **没有聚合根**：实体散落在不同模块，缺少明确的"业务边界"
- **服务和实体没分**：`*Aggregator` 类既是实体又是服务，违反 SRP

### 1.3 application 层几乎为空

```
src/application/
├── orchestrators/task_orchestrator.py   # 唯一一个文件
└── services/__init__.py                  # 空
```

直接结果：
- `interfaces/tasks/collection_tasks.py` 直接 import domain 类调 `.tick()`
- 没有"用例"概念，事务边界、统一日志埋点、跨聚合协调没地方放

---

## 二、目标架构

参考 `jettask_v2` 的 DDD 四层 + ORM，但**不照搬全部**（jettask_v2 是框架，我们是业务应用，部分模块不需要）。

### 2.1 顶层目录调整

```
smart-fund-server/
├── config/                    # 【新增】多环境 YAML 配置
│   ├── default.yaml
│   ├── development.yaml
│   └── production.yaml
├── schema/                    # 【新增】DDL 文件单独维护
│   ├── 01_collection.sql      # ft_news / ft_market_flow / ft_market_cache / ft_sentiment / ft_macro_indicators / ft_collection_state
│   ├── 02_events.sql          # ft_events / ft_event_streams / ft_industry_mapping / ft_industry_index / ft_index_fund
│   ├── 03_trading.sql         # ft_pending_decisions / ft_decisions / ft_trades / ft_positions / ft_fund_limits / ft_reviews / ft_lessons
│   └── 04_indexes.sql         # 所有索引 + partial unique 集中维护
├── scripts/
│   ├── init_db.py             # 【新增】执行 schema/*.sql 建库
│   ├── migrate/               # 【新增】alembic 迁移目录
│   ├── seed_mappings.py       # 已有
│   ├── trigger_tasks.py       # 已有
│   └── test_embedding.py      # 已有
├── src/                       # 四层架构（重构后）
└── tests/                     # 【新增】unit / integration / e2e 三层
```

### 2.2 src/ 四层（重构后）

```
src/
├── common/                    # 跨层公共
│   ├── exceptions.py
│   ├── types.py
│   └── utils.py
│
├── domain/                    # 领域层（按聚合根组织）
│   ├── base.py                # Entity / ValueObject / DomainEvent 基类
│   │
│   ├── collection/            # 【聚合根：数据采集】
│   │   ├── models/
│   │   │   ├── news.py                    # NewsItem 实体
│   │   │   ├── market_flow.py             # MarketFlow 实体（4 子类型）
│   │   │   ├── market_cache.py            # MarketCacheSnapshot
│   │   │   ├── sentiment.py               # SentimentRecord
│   │   │   ├── macro.py                   # MacroIndicator
│   │   │   ├── collection_state.py        # CollectionState（checkpoint）
│   │   │   └── value_objects.py           # CheckpointDict / SourceName / Fingerprint
│   │   ├── services/
│   │   │   ├── news_collector.py          # NewsCollectionService
│   │   │   ├── fund_flow_collector.py
│   │   │   ├── market_collector.py
│   │   │   ├── sentiment_collector.py
│   │   │   ├── macro_collector.py
│   │   │   └── checkpoint_service.py      # 增量/全量判断 + lock
│   │   └── repositories/
│   │       ├── news_repository.py         # 抽象接口（ABC）
│   │       ├── market_flow_repository.py
│   │       ├── market_cache_repository.py
│   │       ├── sentiment_repository.py
│   │       ├── macro_repository.py
│   │       └── collection_state_repository.py
│   │
│   ├── extraction/            # 【聚合根：事件抽取】
│   │   ├── models/
│   │   │   ├── event.py                   # Event 实体（含 embedding）
│   │   │   ├── event_stream.py            # EventStream 聚合
│   │   │   └── value_objects.py
│   │   ├── services/
│   │   │   ├── event_extractor.py         # 调 claude
│   │   │   ├── embedding_generator.py     # 调 embedding HTTP
│   │   │   ├── stream_clusterer.py        # 贪心聚类
│   │   │   └── feedback_filler.py         # 市场反应回填
│   │   └── repositories/
│   │       ├── event_repository.py
│   │       └── event_stream_repository.py
│   │
│   ├── trading/               # 【聚合根：交易决策与执行】
│   │   ├── models/
│   │   │   ├── pending_decision.py        # PendingDecision 实体
│   │   │   ├── trade.py                   # Trade 实体
│   │   │   ├── position.py                # Position 实体
│   │   │   ├── industry_mapping.py        # IndustryMapping / IndustryIndex / IndexFund
│   │   │   ├── fund_limit.py              # FundLimit
│   │   │   └── value_objects.py           # Score / DryRunPolicy
│   │   ├── services/
│   │   │   ├── decision_scoring.py        # 打分（替代 EventDrivenDecider）
│   │   │   ├── industry_router.py         # industry → fund 映射
│   │   │   ├── risk_guard.py              # 硬约束（沿用 risk_manager）
│   │   │   ├── trade_dispatcher.py        # 调 client.buy_fund，多重 dry_run 闸
│   │   │   └── position_monitor.py        # 持仓监控
│   │   └── repositories/
│   │       ├── decision_repository.py
│   │       ├── trade_repository.py
│   │       ├── position_repository.py
│   │       ├── industry_mapping_repository.py
│   │       └── fund_limit_repository.py
│   │
│   └── reflection/            # 【聚合根：复盘与反馈】
│       ├── models/
│       │   ├── review.py                  # Review 实体
│       │   ├── lesson.py                  # Lesson 实体
│       │   └── value_objects.py
│       ├── services/
│       │   ├── review_engine.py
│       │   └── winrate_calculator.py
│       └── repositories/
│           ├── review_repository.py
│           └── lesson_repository.py
│
├── application/               # 应用层（用例编排）
│   ├── services/
│   │   ├── collection_app_service.py      # 5 个采集 use case
│   │   ├── extraction_app_service.py
│   │   ├── trading_app_service.py
│   │   └── reflection_app_service.py
│   ├── dto/
│   │   ├── collection_dto.py
│   │   ├── extraction_dto.py
│   │   ├── trading_dto.py
│   │   └── reflection_dto.py
│   └── orchestrators/
│       └── task_orchestrator.py           # 已有，保留
│
├── infrastructure/            # 基础设施
│   ├── config/
│   │   ├── loader.py                      # 【新增】YAML 加载器
│   │   └── settings.py                    # 已有
│   ├── connections/                       # 【新增】统一连接管理
│   │   ├── database.py                    # SQLAlchemy engine + session factory
│   │   ├── redis.py                       # redis client 单例
│   │   └── manager.py                     # 健康检查 / 关闭
│   ├── persistence/                       # 【新增】ORM 实现
│   │   ├── models/                        # SQLAlchemy ORM 模型（与 schema/ 一一对应）
│   │   │   ├── base.py                    # Declarative Base
│   │   │   ├── collection.py              # News/MarketFlow/MarketCache/Sentiment/Macro/CollectionState
│   │   │   ├── extraction.py              # Event/EventStream
│   │   │   ├── trading.py                 # PendingDecision/Trade/Position/IndustryMapping/FundLimit
│   │   │   └── reflection.py              # Review/Lesson
│   │   └── repositories/                  # 仓储实现
│   │       ├── news_repository_impl.py
│   │       ├── ...                        # 一一对应 domain/*/repositories/
│   ├── observability/                     # 【新增】
│   │   ├── logging.py                     # 统一 logger
│   │   └── metrics.py                     # Prometheus stub
│   ├── clients/                           # 已有，不动
│   ├── db/                                # 旧 raw SQL 模块，重构后保留兼容层 / 逐步删除
│   │   ├── checkpoint_store.py            # 改为 thin wrapper 调 repository
│   │   ├── redis_lock.py                  # 保留
│   │   ├── raw_data.py                    # 保留（独立的归档表）
│   │   └── fund_db.py                     # 拆解到 connections/repositories
│   └── tools/                             # 已有
│
└── interfaces/                # 接口层
    ├── api/                               # 已有
    ├── cli/                               # 改为 click 命令组（可选）
    │   └── main.py                        # python -m smart_fund {worker|scheduler|persist|trigger|init-db}
    └── tasks/                             # jettask task 入口
        └── collection_tasks.py            # 改为 thin wrapper 调 application services
```

---

## 三、ORM 模型清单

下表是**全部 16 张需要 ORM 化的表**及对应模型名。后面会列出代表性模型的完整代码。

| ORM 模型 | 表名 | 列数 | 所在文件 | 复杂度 |
|---|---|---|---|---|
| `News` | `ft_news` | 15 | `persistence/models/collection.py` | 低 |
| `MarketFlow` | `ft_market_flow` | 5 | `persistence/models/collection.py` | 低（JSONB） |
| `MarketCache` | `ft_market_cache` | 5 | `persistence/models/collection.py` | 低（JSONB + UPSERT） |
| `Sentiment` | `ft_sentiment` | 5 | `persistence/models/collection.py` | 低（JSONB） |
| `MacroIndicator` | `ft_macro_indicators` | 9 | `persistence/models/collection.py` | 中 |
| `CollectionState` | `ft_collection_state` | 14 | `persistence/models/collection.py` | 中 |
| `Event` | `ft_events` | 28 | `persistence/models/extraction.py` | 高（embedding bytea） |
| `EventStream` | `ft_event_streams` | 13 | `persistence/models/extraction.py` | 中（int[]） |
| `PendingDecision` | `ft_pending_decisions` | 23 | `persistence/models/trading.py` | 高 |
| `Decision` | `ft_decisions` | 13 | `persistence/models/trading.py` | 中 |
| `Trade` | `ft_trades` | 13 | `persistence/models/trading.py` | 中 |
| `Position` | `ft_positions` | 12 | `persistence/models/trading.py` | 低 |
| `IndustryMapping` | `ft_industry_mapping` | 7 | `persistence/models/trading.py` | 低 |
| `IndustryIndex` | `ft_industry_index` | 6 | `persistence/models/trading.py` | 低 |
| `IndexFund` | `ft_index_fund` | 9 | `persistence/models/trading.py` | 低 |
| `FundLimit` | `ft_fund_limits` | 11 | `persistence/models/trading.py` | 低 |
| `Review` | `ft_reviews` | 17 | `persistence/models/reflection.py` | 中 |
| `Lesson` | `ft_lessons` | 16 | `persistence/models/reflection.py` | 中 |

> **17 + 1**：实际是 18 个 ORM 模型（其中 `Decision` 是 LLM 决策的旧表，event_driven 流程用的是 `PendingDecision`）。

### 3.1 代表性模型示例（SQLAlchemy 2.0 风格）

```python
# src/infrastructure/persistence/models/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """所有 ORM 模型的统一基类"""
    pass
```

```python
# src/infrastructure/persistence/models/collection.py
from datetime import date, datetime
from sqlalchemy import (
    BigInteger, Boolean, Date, DateTime, Float, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from .base import Base


class News(Base):
    """新闻原始记录（多源采集 → 跨源去重）"""
    __tablename__ = "ft_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="新闻标题")
    content: Mapped[str] = mapped_column(Text, default="", comment="正文（详情页抓取）")
    summary: Mapped[str] = mapped_column(Text, default="", comment="列表页摘要")
    source: Mapped[str] = mapped_column(String(32), nullable=False, comment="数据源标识，如 cls/ths/sina")
    source_name: Mapped[str] = mapped_column(String(32), default="", comment="数据源中文名")
    source_reliability: Mapped[float] = mapped_column(Float, default=0.5, comment="数据源可信度 0-1")
    category: Mapped[str] = mapped_column(String(32), default="", comment="分类，如 macro/policy/company")
    url: Mapped[str] = mapped_column(Text, default="", comment="原文 URL")
    tags: Mapped[list] = mapped_column(JSONB, default=list, comment="标签数组")
    related_stocks: Mapped[list] = mapped_column(JSONB, default=list, comment="关联股票代码列表")
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="原文发布时间")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, comment="SHA256(title+source) 去重指纹")
    event_extracted: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已被 AI 事件抽取处理")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"<News id={self.id} source={self.source} title={self.title[:20]!r}>"


class MarketFlow(Base):
    """资金流（北向/板块/个股/龙虎榜，用 data_type 区分）"""
    __tablename__ = "ft_market_flow"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    data_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="northbound|sector_flow|stock_flow|dragon_tiger")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日")
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, comment="原始数据 JSONB")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectionState(Base):
    """采集 checkpoint（每个 (aggregator, source_name) 一行）"""
    __tablename__ = "ft_collection_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aggregator: Mapped[str] = mapped_column(String(32), nullable=False, comment="news / fund_flow / macro / ...")
    source_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="northbound / sector_flow_sina / ...")

    checkpoint: Mapped[dict] = mapped_column(JSONB, default=dict, comment="增量游标，结构由 source 自定义")

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="上次开跑时间")
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="上次成功时间")
    last_error: Mapped[str] = mapped_column(Text, default="", comment="最近一次错误信息")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, comment="连续失败次数")

    enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用（运行时控制）")
    interval_override: Mapped[int | None] = mapped_column(Integer, comment="覆盖代码默认 interval（秒）")

    total_runs: Mapped[int] = mapped_column(BigInteger, default=0)
    total_saved: Mapped[int] = mapped_column(BigInteger, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
```

```python
# src/infrastructure/persistence/models/extraction.py
class Event(Base):
    """AI 抽取出的结构化事件（含 embedding）"""
    __tablename__ = "ft_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="事件标题")
    summary: Mapped[str] = mapped_column(Text, default="")
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="policy/macro/industry/company/capital")
    event_subtype: Mapped[str] = mapped_column(String(32), default="")

    industries: Mapped[list] = mapped_column(JSONB, default=list, comment="涉及行业")
    companies: Mapped[list] = mapped_column(JSONB, default=list)
    organizations: Mapped[list] = mapped_column(JSONB, default=list)
    regions: Mapped[list] = mapped_column(JSONB, default=list)

    impact_direction: Mapped[str | None] = mapped_column(String(16), comment="positive/negative/neutral")
    impact_strength: Mapped[float] = mapped_column(Float, default=0, comment="影响强度 0-1")
    impact_scope: Mapped[str | None] = mapped_column(String(16))
    impact_duration: Mapped[str | None] = mapped_column(String(16))

    sentiment: Mapped[float] = mapped_column(Float, default=0.5, comment="情绪 0-1（0.5=中性）")
    novelty: Mapped[float] = mapped_column(Float, default=0.5, comment="新颖度")
    certainty: Mapped[float] = mapped_column(Float, default=0.5, comment="确定性")

    source_news_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, comment="来源 ft_news.id 列表")

    embedding: Mapped[bytes | None] = mapped_column(comment="向量（1024 dim float32 → 4096 bytes）")
    embedding_model: Mapped[str] = mapped_column(String(32), default="", comment="向量模型标识")

    # 市场反应回填字段（agg_event_feedback 写入）
    sector_change_1d: Mapped[float | None] = mapped_column(Float, comment="T+1 行业涨跌")
    sector_change_3d: Mapped[float | None] = mapped_column(Float)
    sector_volume_change: Mapped[float | None] = mapped_column(Float)
    north_flow_1d: Mapped[float | None] = mapped_column(Float)
    reaction_delay_minutes: Mapped[int | None] = mapped_column(Integer)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, comment="SHA256(title|event_type)")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

> 完整 18 个模型在实施阶段一次性写出。上面 3 个是模板。

### 3.2 Repository 抽象示例

```python
# src/domain/collection/repositories/news_repository.py
from abc import ABC, abstractmethod
from datetime import date

from src.domain.collection.models.news import NewsItem  # domain 实体


class NewsRepository(ABC):
    """新闻仓储接口（domain 层只看到接口，不看到 SQLAlchemy）"""

    @abstractmethod
    def upsert_batch(self, items: list[NewsItem]) -> int:
        """批量插入，跨源 fingerprint 冲突跳过；返回成功条数"""

    @abstractmethod
    def find_by_fingerprint(self, fingerprint: str) -> NewsItem | None: ...

    @abstractmethod
    def find_today_titles(self, today: date) -> list[str]:
        """跨源相似度去重用"""

    @abstractmethod
    def find_unextracted(self, limit: int = 30) -> list[NewsItem]:
        """事件抽取任务读取"""

    @abstractmethod
    def mark_extracted(self, ids: list[int]) -> int: ...
```

```python
# src/infrastructure/persistence/repositories/news_repository_impl.py
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.domain.collection.models.news import NewsItem
from src.domain.collection.repositories.news_repository import NewsRepository
from src.infrastructure.persistence.models.collection import News


class NewsRepositoryImpl(NewsRepository):
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def upsert_batch(self, items: list[NewsItem]) -> int:
        if not items:
            return 0
        rows = [item.to_orm_dict() for item in items]
        with self._session_factory() as session:
            stmt = pg_insert(News).values(rows).on_conflict_do_nothing(
                index_elements=["fingerprint"]
            )
            result = session.execute(stmt)
            session.commit()
            return result.rowcount or 0

    def find_unextracted(self, limit: int = 30) -> list[NewsItem]:
        with self._session_factory() as session:
            stmt = (
                select(News)
                .where(News.event_extracted.is_(False))
                .order_by(News.published_at.desc())
                .limit(limit)
            )
            return [NewsItem.from_orm(r) for r in session.scalars(stmt)]
```

---

## 四、Domain 实体 vs ORM 模型的关系

**重要原则**：
- `infrastructure/persistence/models/` 的 SQLAlchemy 类是**纯持久化结构**，不带业务方法
- `domain/<aggregate>/models/` 的实体是**业务对象**，可以有 `compute_xxx()` `validate()` 之类的方法
- Repository 实现里做 ORM ↔ 实体的相互转换

```
┌─────────────────────────┐  to_orm_dict() / from_orm()  ┌──────────────────────────────┐
│ domain.collection.      │ ────────────────────────────► │ infrastructure.persistence.   │
│   models.NewsItem       │                              │   models.News (SQLAlchemy)   │
│ （业务对象 + 方法）      │ ◄──────────────────────────── │ （持久化结构）                │
└─────────────────────────┘                              └──────────────────────────────┘
                                                                      │
                                                                      ▼
                                                              ┌────────────────┐
                                                              │ PostgreSQL     │
                                                              │   ft_news      │
                                                              └────────────────┘
```

> **简化版本**：如果实体几乎没有业务方法，可以直接把 SQLAlchemy 模型当实体用（jettask_v2 的某些聚合就是这么做的）。这是 trade-off：纯度 vs 工程量。**我们建议起步阶段简化**：除了少数有业务逻辑的实体（Event/EventStream/Position/Trade）外，其他直接用 ORM 模型当实体。

---

## 五、改造范围与对照

下面列出**每个现有文件**的改造对照表，你可以用它做迁移 checklist。

### 5.1 直接连 PG 写 SQL 的文件清单（待迁移）

| 现有文件 | 涉及的表 | 改造后调用 |
|---|---|---|
| `domain/aggregation/news.py` | ft_news | `NewsRepository.upsert_batch / find_today_titles` |
| `domain/aggregation/fund_flow.py` | ft_market_flow | `MarketFlowRepository.upsert_batch_with_partial_unique` |
| `domain/aggregation/market.py` | ft_market_cache | `MarketCacheRepository.upsert` |
| `domain/aggregation/sentiment.py` | ft_sentiment | `SentimentRepository.upsert_batch` |
| `domain/aggregation/macro.py` | ft_macro_indicators | `MacroRepository.upsert_batch` |
| `domain/aggregation/event_extraction.py` | ft_news (read), ft_events (write) | `NewsRepository.find_unextracted + mark_extracted` + `EventRepository.upsert_with_embedding` |
| `domain/aggregation/event_stream.py` | ft_events (read), ft_event_streams (write) | `EventRepository.find_recent_with_embedding` + `EventStreamRepository.replace_active_streams` |
| `domain/aggregation/event_feedback.py` | ft_events (update) | `EventRepository.update_market_reaction` |
| `domain/aggregation/base.py` | ft_collection_state | `CollectionStateRepository.get_state / update_success / update_failure` |
| `domain/decision/event_driven_decider.py` | ft_event_streams (read), ft_pending_decisions (write), ft_events (read), 3 张映射表 | `EventStreamRepository.find_active` + `IndustryMappingRepository.resolve_to_funds` + `PendingDecisionRepository.upsert` |
| `domain/decision/trade_executor.py` | ft_pending_decisions (read/update), ft_trades (write), ft_positions (upsert), ft_fund_limits (read) | 一一对应 repository |
| `domain/decision/trade_monitor.py` | ft_positions (read), ft_trades (read for paper), ft_event_streams (read), ft_pending_decisions (insert) | 一一对应 |
| `domain/decision/review_engine.py` | ft_trades (read), ft_reviews (upsert), ft_config (write winrate) | `ReviewRepository` + `ConfigRepository` |
| `domain/trading/risk_manager.py` | ft_positions, ft_decisions, ft_fund_limits | 一一对应 |
| `infrastructure/db/checkpoint_store.py` | ft_collection_state | 改为 thin wrapper 调 `CollectionStateRepository` |
| `infrastructure/db/fund_db.py` | ft_config / ft_positions / ft_decisions / ft_trades / ft_run_log / ft_cache | 拆解到对应 repository |
| `infrastructure/db/raw_data.py` | ft_raw_data（分区表） | 单独保留，是技术性归档表 |
| `scripts/seed_mappings.py` | ft_industry_mapping / ft_industry_index / ft_index_fund | 改用 ORM bulk_save |

总计 **~18 个文件** 需要改造。

### 5.2 直接 import jettask 类的入口（不动）

- `interfaces/tasks/collection_tasks.py` —— 内部调 application services
- `interfaces/cli/start_*.py` —— 不动
- `interfaces/api/routes/*.py` —— 已经是 thin layer，按需调 application services

---

## 六、实施步骤（分阶段）

### 6.0 测试策略总则

**核心原则**：每个 Phase 的每个子步骤都必须有可执行的测试，**做完即测**，不要做完一整个 Phase 才回头补测试。发现问题立即矫正，防止在错误方向上累积工作量。

**三层测试架构**：

| 层 | 路径 | 依赖 | 触发时机 | 速度 |
|---|---|---|---|---|
| **单元测试** | `tests/unit/` | mock，不连任何外部 | 每改 1 个文件后 | < 1s |
| **集成测试** | `tests/integration/` | 真实 PG + Redis（独立测试库） | 每完成 1 个子步骤后 | < 30s |
| **端到端回归** | `tests/e2e/` | 完整 worker + scheduler + persist | 每完成 1 个 Phase 后 | < 5min |

**测试基础设施约定**（R1.0 新建）：
- 独立测试库 `jettask_test`（不要污染生产）
- `tests/conftest.py` 提供 fixtures：`db_session` / `clean_db` / `sample_news` / `sample_event`
- `pytest -m unit` / `pytest -m integration` / `pytest -m e2e` 三种 marker
- 每次跑测试前 `TRUNCATE` 测试库的相关表
- E2E 测试用真实远程 embedding 服务 + claude CLI（不 mock）

**通过/失败判定**：
- 单元测试 100% 通过 → 可以进入集成测试
- 集成测试 100% 通过 → 可以提交并进入下一子步骤
- E2E 测试任意失败 → **立即停止当前 Phase，回滚到上一个 git tag**

**回归基线**（R1.0 锁定）：
- 跑一次完整 worker 5 分钟，记录每张表的 row count + 各 source 的 last_run_at
- 这是"重构前后等价性"的对比基准
- 所有 Phase 完成后,这些指标必须等价或更好

---

### Phase R1：基础设施补齐（1-2 天，低风险）

**目标**：建立 ORM/连接管理/DDL 的基础，但**不替换业务代码**。新旧并存。

| 步骤 | 工作 | 输出 |
|---|---|---|
| R1.1 | 新建 `schema/` 目录，提取所有 DDL 到 `.sql` 文件 | `01_collection.sql` ~ `04_indexes.sql` |
| R1.2 | 写 `scripts/init_db.py` 串联执行 sql 文件 | 一个命令重建数据库 |
| R1.3 | 新建 `infrastructure/connections/database.py` —— SQLAlchemy engine + sessionmaker | `get_session()` 上下文管理器 |
| R1.4 | 新建 `infrastructure/persistence/models/base.py` —— Declarative Base | 模型基类 |
| R1.5 | 新建 `infrastructure/persistence/models/collection.py` —— 6 个采集模型 | `News/MarketFlow/MarketCache/Sentiment/MacroIndicator/CollectionState` |
| R1.6 | 写一个**只读** smoke test：`session.scalars(select(News).limit(5))` 验证 ORM 能读出现有数据 | 单测 |
| R1.7 | 新建 `infrastructure/persistence/models/extraction.py` —— Event/EventStream | 2 个模型 |
| R1.8 | 新建 `infrastructure/persistence/models/trading.py` —— 8 个交易模型 | 8 个模型 |
| R1.9 | 新建 `infrastructure/persistence/models/reflection.py` —— Review/Lesson | 2 个模型 |

**完成标志**：
- 18 个 ORM 模型定义完成
- 能用 ORM 只读查询所有现有表
- 业务代码完全不变，仍然正常运行

#### R1 验证测试

| 测试 ID | 关联步骤 | 类型 | 测试内容 | 通过标准 |
|---|---|---|---|---|
| **T-R1.0-1** | R1.0 | 基础 | 创建 `jettask_test` 数据库，写 `tests/conftest.py` 提供 `db_session` fixture，跑一个空的 `pytest tests/` | pytest 启动成功，至少 1 个 fixture 被收集 |
| **T-R1.0-2** | R1.0 | 基础 | 跑当前生产 worker 5 分钟，记录基线：`SELECT data_type, COUNT(*) FROM ft_market_flow GROUP BY data_type` 等所有 18 张表 row 数 | 所有 row count 落档到 `tests/baselines/r1_pre.json` |
| **T-R1.1-1** | R1.1 | 集成 | 在干净的 `jettask_test` 库执行 `psql -f schema/01_collection.sql ...` | `\dt ft_*` 列出 6 张采集相关表 |
| **T-R1.1-2** | R1.1 | 集成 | DDL 提取后与生产库 schema 比对：`information_schema.columns` JOIN 比对列名/类型/nullable/default | 0 差异 |
| **T-R1.2-1** | R1.2 | 集成 | 跑 `python scripts/init_db.py --target=test` 重建 `jettask_test` | 18 张 ft_* 表全部建出，索引/UNIQUE/partial index 全部存在 |
| **T-R1.2-2** | R1.2 | 集成 | 重复跑 init_db.py 三次 | 不报错（幂等） |
| **T-R1.3-1** | R1.3 | 单元 | `from src.infrastructure.connections.database import get_session; with get_session() as s: s.execute(text('SELECT 1'))` | 返回 1 |
| **T-R1.3-2** | R1.3 | 单元 | 同时拿两个 session，验证不互相干扰 | 两个 session 独立 |
| **T-R1.4-1** | R1.4 | 单元 | `from src.infrastructure.persistence.models.base import Base; print(len(Base.registry.mappers))` | == 0（空 Base，无副作用） |
| **T-R1.5-1** | R1.5 | 集成 | 对 6 个采集模型每个跑 `select(M).limit(1)` 拿到 ORM 对象（连真实生产库只读） | 每个模型至少能取到 1 行（无字段类型异常） |
| **T-R1.5-2** | R1.5 | 集成 | `News.tags` / `MarketFlow.data` / `CollectionState.checkpoint` 直接当 list/dict 用 | 能 `for tag in news.tags: ...`，不需要 `json.loads` |
| **T-R1.5-3** | R1.5 | 集成 | `News.published_at` 是 `datetime`，`Sentiment.trade_date` 是 `date`，`MacroIndicator.value` 是 `float` | 类型与 PG schema 对应 |
| **T-R1.6-1** | R1.6 | smoke | 18 模型逐个 `select(M).limit(5)`，每行都能 `repr()` | 0 异常 |
| **T-R1.6-2** | R1.6 | smoke | **schema 反射比对**：用 SQLAlchemy `inspect(engine)` 反射出实际 PG 列定义，与 ORM `__table__.columns` 比对 | 列名 100% 一致；类型有 1-2 处可接受偏差（如 `TIMESTAMPTZ` vs `DateTime(tz=True)`） |
| **T-R1.7-1** | R1.7 | 集成 | 对 extraction 2 个模型跑 only-read smoke | OK |
| **T-R1.8-1** | R1.8 | 集成 | 对 trading 8 个模型跑 only-read smoke | OK |
| **T-R1.8-2** | R1.8 | 集成 | `select(PendingDecision).where(PendingDecision.dry_run.is_(True))` | 能查到 dry_run 决策 |
| **T-R1.9-1** | R1.9 | 集成 | 对 reflection 2 个模型跑 only-read smoke | OK |
| **T-R1-E2E** | R1 完成 | E2E | 跑完整 worker 5 分钟，再次记录所有表 row 数 | 与 T-R1.0-2 基线 **完全一致**（业务代码没动） |

**R1 通过 → 立即打 git tag `refactor-r1-done`**。失败任意一项 → 不要继续 R2，先 fix。

---

### Phase R2：repository 接口 + 实现（2-3 天，中风险）

**目标**：把"数据访问"从 domain 推到 infrastructure，但 domain 类的对外 API 保持不变。

| 步骤 | 工作 |
|---|---|
| R2.1 | 在 `domain/collection/repositories/` 写所有抽象接口 |
| R2.2 | 在 `infrastructure/persistence/repositories/` 写实现 |
| R2.3 | 改 `domain/aggregation/news.py` 调用 `NewsRepository`（构造函数注入） |
| R2.4 | **同步跑端到端测试**，确认 news 流程 OK |
| R2.5 | 重复 R2.3 + R2.4，依次改造 fund_flow/market/sentiment/macro |
| R2.6 | 改造 base.py 的 `_save` 抽象，让子类传入 repository |
| R2.7 | 改造 event_extraction/event_stream/event_feedback |
| R2.8 | 改造 decision 4 个模块 |
| R2.9 | 改造 review_engine |
| R2.10 | 改造 checkpoint_store（变成 thin wrapper） |

**完成标志**：
- domain 层不再直接 `import psycopg2`
- 所有读写都走 repository 接口
- 业务功能完全等价

#### R2 验证测试

**R2 的测试模式**：每改造完一个 repository 就跑"3 件套"——单元 + 集成 + 端到端 smoke。

**3 件套模板**（对应每个 repository）：

```
# 1. 单元测试: mock session 验证 SQL 语义
tests/unit/repositories/test_<name>_repository.py::test_upsert_batch_calls_pg_insert

# 2. 集成测试: 真实 PG 验证 CRUD
tests/integration/repositories/test_<name>_repository.py::test_upsert_then_find_returns_same

# 3. E2E smoke: 改造完的 aggregator/decider 跑通
跑 trigger_tasks.py <task>，验证表 row 数与 R1 基线一致
```

| 测试 ID | 关联步骤 | 类型 | 测试内容 | 通过标准 |
|---|---|---|---|---|
| **T-R2.1-1** | R2.1 | 单元 | `from src.domain.collection.repositories.news_repository import NewsRepository; NewsRepository.__abstractmethods__` | 包含 `upsert_batch / find_today_titles / find_unextracted / mark_extracted` |
| **T-R2.1-2** | R2.1 | 静态 | `grep "from src.infrastructure.db" src/domain/collection/repositories/` | 0 行（domain 不能 import infra） |
| **T-R2.2-1** | R2.2 | 单元 | `NewsRepositoryImpl(mock_factory).upsert_batch([news_item])` 用 mock session 验证 SQL 是 `INSERT ... ON CONFLICT (fingerprint) DO NOTHING` | mock 被调用一次，参数包含 fingerprint |
| **T-R2.2-2** | R2.2 | 集成 | 真实 PG 跑 upsert_batch([3 条新闻]) → find_by_fingerprint → 拿到对象 → 类型字段都对 | 3 条入库，对象字段值与输入一致 |
| **T-R2.2-3** | R2.2 | 集成 | upsert_batch 同样 fingerprint 跑两次 | 第二次返回 0（ON CONFLICT 跳过） |
| **T-R2.3-1** | R2.3 | 集成 | 改造后的 `NewsAggregator.tick()` 跑 1 次，对比基线 row 数 | 等价 |
| **T-R2.3-2** | R2.3 | 静态 | `grep "psycopg2\|get_conn\|cursor" src/domain/aggregation/news.py` | 0 行 |
| **T-R2.4** | R2.4 | E2E | `trigger_tasks.py news` → 等 worker 完成 → `SELECT COUNT(*) FROM ft_news` | 与 baseline 等价 ±10% |
| **T-R2.5-fund_flow-1** | R2.5 | 集成 | 改造 fund_flow 后跑 4 个 source（northbound/sector/stock/dragon_tiger）的 upsert | 5 个 partial unique index 全部生效，重复跑 0 入库 |
| **T-R2.5-fund_flow-2** | R2.5 | 集成 | normalize_stock_flow 展开 history 后入库 | 同 code 不同日期能入 11 行 |
| **T-R2.5-market** | R2.5 | 集成 | MarketCacheRepository.upsert（覆盖式）跑两次 | 同一 data_type 只保留最新一行 |
| **T-R2.5-sentiment** | R2.5 | 集成 | SentimentRepository.upsert_batch | 数据等价 |
| **T-R2.5-macro** | R2.5 | 集成 | MacroRepository.upsert_batch | EM 9 + PBOC 7 = 16 个 indicator 全部入库 |
| **T-R2.6-1** | R2.6 | 单元 | mock_repo 测试 base.py 的 tick() 调用流程：`should_fetch → enabled → lock → fetch → save → update_cp` | 5 个调用按顺序发生 |
| **T-R2.6-2** | R2.6 | 集成 | base.py 改造后跑 fund_flow.tick() | 行为等价 |
| **T-R2.7-1** | R2.7 | 集成 | EventRepository.upsert_with_embedding 写入 1 条带 embedding 的事件 | bytea 字段长度 == 4096 |
| **T-R2.7-2** | R2.7 | 集成 | EventRepository.find_recent_with_embedding(hours=24) | 返回 list[Event]，每条都有非空 embedding |
| **T-R2.7-3** | R2.7 | 集成 | EventStreamRepository.replace_active_streams([cluster]) | DELETE active + INSERT 新的，原子事务 |
| **T-R2.7-4** | R2.7 | 集成 | EventRepository.update_market_reaction(event_id, {...}) | feedback_at 被更新为 NOW |
| **T-R2.7-E2E** | R2.7 | E2E | trigger_tasks.py event_extraction → event_stream → event_feedback 三连，完成后查 ft_events 行数 + 有 embedding 的比例 | embedding 比例 == 入库数（100%） |
| **T-R2.8-1** | R2.8 | 集成 | PendingDecisionRepository.upsert 同 stream_id+fund_code+date 跑两次 | 第二次 0 入库（partial unique 生效） |
| **T-R2.8-2** | R2.8 | 集成 | TradeRepository.insert_dry_run 一笔，PositionRepository 不动 | ft_trades +1, ft_positions 不变 |
| **T-R2.8-3** | R2.8 | 集成 | IndustryMappingRepository.resolve_to_funds("AI") | 返回 [515980 华夏中证人工智能 ETF, ...] |
| **T-R2.8-E2E** | R2.8 | E2E | trigger_tasks.py trade_decision → trade_execution 完整跑 | 决策数 + dry_run 交易数与基线相符 |
| **T-R2.9-1** | R2.9 | 集成 | ReviewRepository.upsert_review(trade_id, t1, t2, outcome) 同 trade_id 写两次 | 第二次 UPDATE，不报 unique error |
| **T-R2.9-2** | R2.9 | 集成 | ReviewRepository.calculate_winrate(days=30) | 返回 dict {total, correct, wrong, neutral, winrate} |
| **T-R2.10-1** | R2.10 | 集成 | checkpoint_store.get_checkpoint 改成 thin wrapper 后行为等价 | 现有 fund_flow tick() 不报错 |
| **T-R2-final** | R2 完成 | E2E | 跑完整 worker 5 分钟 + 全部 trigger_tasks 一遍 | 18 张表 row 数与 R1 基线 ±10% |
| **T-R2-grep** | R2 完成 | 静态 | `grep -rn "import psycopg2\|get_conn\|cursor.execute" src/domain/` | **0 行**（除了过渡期保留的 `infrastructure/db/raw_data.py`） |

**R2 通过 → 打 tag `refactor-r2-done`**。

---

### Phase R3：聚合根重组 + application services（2-3 天，中高风险）

**目标**：按聚合根重新组织 domain 目录，新建 application services。

| 步骤 | 工作 |
|---|---|
| R3.1 | 创建新的 domain 目录结构（collection/extraction/trading/reflection） |
| R3.2 | `git mv` 现有文件到新目录 |
| R3.3 | 拆分 `aggregation/event_extraction.py` → `extraction/services/event_extractor.py` + `embedding_generator.py` |
| R3.4 | 拆分 `decision/event_driven_decider.py` → `trading/services/decision_scoring.py` + `industry_router.py` |
| R3.5 | 新建 `application/services/collection_app_service.py` —— 一个 use case 一个方法 |
| R3.6 | 新建 `application/dto/*` |
| R3.7 | 改 `interfaces/tasks/collection_tasks.py` 调 application services |
| R3.8 | 删除/合并旧的 `domain/aggregation/` `domain/decision/` `domain/trading/` 目录 |

**完成标志**：
- domain 按聚合根组织，目录边界清晰
- application 层有明确的 use case 入口
- task 层是 thin wrapper，没有业务逻辑

#### R3 验证测试

R3 主要是文件搬迁 + 接口调整,**重点测 import 不破和 task → application → domain 的调用链不破**。

| 测试 ID | 关联步骤 | 类型 | 测试内容 | 通过标准 |
|---|---|---|---|---|
| **T-R3.1-1** | R3.1 | 静态 | 创建新目录后 `tree src/domain` | 4 个聚合根目录都在 |
| **T-R3.2-1** | R3.2 | 静态 | `git mv` 完成后 `python -c "from src.interfaces.tasks.collection_tasks import *"` | import 0 错误 |
| **T-R3.2-2** | R3.2 | 静态 | `grep -rn "from src.domain.aggregation\|from src.domain.decision\|from src.domain.trading" src/` | **0 行**（旧路径已全部替换） |
| **T-R3.3-1** | R3.3 | 单元 | `EventExtractor.extract_one(news)` 用 mock claude 跑 | 返回 Event 对象 |
| **T-R3.3-2** | R3.3 | 单元 | `EmbeddingGenerator.generate([text])` 用 mock httpx 跑 | 返回 1024 维 list |
| **T-R3.3-3** | R3.3 | 集成 | EventExtractor 真实跑一条新闻 | 入库 1 条事件 |
| **T-R3.4-1** | R3.4 | 单元 | `DecisionScoring.score(stream, events)` 用 fixture | 返回 (score, breakdown) |
| **T-R3.4-2** | R3.4 | 单元 | `IndustryRouter.resolve("半导体")` mock repository | 返回 [{fund_code, ...}] |
| **T-R3.5-1** | R3.5 | 单元 | `CollectionAppService.run_news_collection()` mock domain service | 调用顺序正确 |
| **T-R3.5-2** | R3.5 | 单元 | `ExtractionAppService.extract_events_from_news()` 5 条 mock 输入 | 返回 ExtractionResult dto |
| **T-R3.5-3** | R3.5 | 单元 | `TradingAppService.execute_pending_dry_run()` mock domain | 不应触发真实下单 |
| **T-R3.5-4** | R3.5 | 单元 | `ReflectionAppService.run_review()` mock | 调用 review_engine 一次 |
| **T-R3.6-1** | R3.6 | 静态 | `from src.application.dto.collection_dto import NewsCollectionResult` | 能 import |
| **T-R3.7-1** | R3.7 | 集成 | `collect_news task` 内部应该只调 `CollectionAppService.run_news_collection()`，不再 import domain | grep 验证 |
| **T-R3.7-2** | R3.7 | 集成 | trigger_tasks.py news → 完整跑通 | 等价 |
| **T-R3.7-3** | R3.7 | 集成 | trigger_tasks.py 全部 12 个 task | 全部成功 |
| **T-R3.8-1** | R3.8 | 静态 | 旧目录 `src/domain/aggregation/` 应该被删除 | `ls` 不存在 |
| **T-R3.8-2** | R3.8 | 静态 | `find src -name "*.py" \| xargs grep -l "domain.aggregation\|domain.decision\|domain.trading"` | 0 文件 |
| **T-R3-E2E-1** | R3 完成 | E2E | 跑完整 worker 10 分钟 | 12 个 task 全部正常执行,无异常 |
| **T-R3-E2E-2** | R3 完成 | E2E | 跟 R1 baseline 比较 18 张表 row 数 | 总变化 ±15%（允许窗口期波动） |
| **T-R3-E2E-3** | R3 完成 | E2E | scheduler / persist / worker 三进程同时跑 1 小时 | 0 cascade 失败、0 锁泄漏 |

**R3 通过 → 打 tag `refactor-r3-done`**。

---

### Phase R4（可选）：observability + click CLI

| 步骤 | 工作 |
|---|---|
| R4.1 | 新建 `infrastructure/observability/logging.py` 统一 logger |
| R4.2 | 新建 `infrastructure/observability/metrics.py` Prometheus stub |
| R4.3 | 改 `interfaces/cli/` 用 click 命令组 |
| R4.4 | 新建 `tests/unit/` 单元测试目录（domain 服务可以用 mock repository） |

#### R4 验证测试

| 测试 ID | 关联步骤 | 类型 | 测试内容 | 通过标准 |
|---|---|---|---|---|
| **T-R4.1-1** | R4.1 | 单元 | `from src.infrastructure.observability.logging import get_logger; log = get_logger('test'); log.info('hi')` | 输出格式包含 timestamp + level + name + message |
| **T-R4.1-2** | R4.1 | 集成 | 跑一次 worker，所有 logger 都用 `get_logger()` 替代 `logging.getLogger()` | grep `logging.getLogger` 在 src/ 中 ≤ 0 行 |
| **T-R4.2-1** | R4.2 | 单元 | `from src.infrastructure.observability.metrics import COLLECTION_DURATION; COLLECTION_DURATION.labels('news').observe(1.5)` | 不报错 |
| **T-R4.2-2** | R4.2 | 集成 | 起一个 prometheus endpoint（FastAPI route），跑 collection，curl `/metrics` | 能看到 `collection_duration_seconds_bucket{aggregator="news"}` |
| **T-R4.3-1** | R4.3 | 单元 | `python -m smart_fund --help` | 列出 worker / scheduler / persist / trigger / init-db 子命令 |
| **T-R4.3-2** | R4.3 | 集成 | `python -m smart_fund worker -c 1` 启动 worker | 等价于现在的 `start_worker.py` |
| **T-R4.3-3** | R4.3 | 集成 | `python -m smart_fund trigger fund_flow` 替代 `trigger_tasks.py fund_flow` | 等价 |
| **T-R4.4-1** | R4.4 | 基础 | `pytest tests/unit -v` | 至少 50 个测试通过（覆盖 4 个 application service + 18 个 repository） |
| **T-R4.4-2** | R4.4 | 基础 | `pytest tests/integration -v` | 至少 20 个集成测试通过 |
| **T-R4.4-3** | R4.4 | CI 友好 | 设计成 `pytest -m "not e2e"` 可以在没有真实 PG 时只跑 unit | 单元测试 0 网络依赖 |

**R4 通过 → 打 tag `refactor-r4-done`**。

---

### 6.x 测试用例索引

为方便实施时按 ID 查找，下面是所有测试 ID 的总览（按 Phase 排序）。

| Phase | 测试 ID 范围 | 总数 | 重点 |
|---|---|---|---|
| R1 | T-R1.0-1 ~ T-R1-E2E | ~17 | ORM 模型只读 + 字段类型对账 + 基线锁定 |
| R2 | T-R2.1-1 ~ T-R2-grep | ~30 | 每个 repository 三件套（unit + integration + E2E smoke） |
| R3 | T-R3.1-1 ~ T-R3-E2E-3 | ~17 | import 不破 + application service 单元 + 完整 worker 1 小时 |
| R4 | T-R4.1-1 ~ T-R4.4-3 | ~9 | logger / metrics / cli / 测试覆盖率 |
| **合计** | | **~73 个测试用例** | |

**实施纪律**：
1. **每完成一个子步骤**（R1.1 / R1.2 ...）→ 跑该步骤对应的测试 → 全绿才进入下一步骤
2. **每完成一个 Phase** → 跑该 Phase 全部测试 + E2E 回归 → 打 git tag
3. **任意测试失败** → 立即停止，先 fix 再继续；fix 不动就回滚到上一个 tag
4. **不允许跳过测试**进入下一阶段（哪怕"看起来没问题"）
5. **测试代码本身也要 commit**，和实施代码一起进 PR

---

## 七、风险与回滚

### 7.1 主要风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| ORM 字段类型与原 PG 类型不符 | 入库失败 / 数据丢失 | R1.6 只读 smoke test 提前发现 |
| Repository 接口设计不符合所有 use case | 后期反复改 | R2 阶段每改完一个模块就跑端到端测试 |
| 聚合根边界划分不准 | 跨聚合调用混乱 | R3 拆分前先在白板上画清楚 |
| 大批量改动导致 git 历史变乱 | 回滚困难 | 每个 step 一个 commit，commit message 清晰 |
| ON CONFLICT 等 PG 特性 SQLAlchemy 写法不熟 | 写入语义变化 | 用 `sqlalchemy.dialects.postgresql.insert` 显式写 |
| 模型字段顺序与 schema 不一致 | INSERT 报错 | 用 `Mapped` + 命名参数，不用位置参数 |

### 7.2 回滚策略

- **每个 Phase 一个 git tag**：`refactor-r1-done` / `refactor-r2-done` / `refactor-r3-done`
- **每个 Phase 完成后做完整端到端跑**：worker 跑 1 小时，确认所有 task 正常
- **Phase 之间可独立回滚**：R1 完成不强制走 R2；R2 完成不强制走 R3
- **数据库变动谨慎**：本次重构**不改 schema**，只是用 ORM 来操作。SQL DDL 只是从代码字符串移到 .sql 文件，结构等价

### 7.3 不动的部分（明确范围）

- `infrastructure/clients/` —— 完全不动
- `infrastructure/db/raw_data.py` —— 不动（独立的归档表，分区结构特殊）
- `infrastructure/db/redis_lock.py` —— 不动
- `interfaces/api/routes/` —— R3 完成后再看是否需要小改
- `embedding-service/` —— 不动
- `event-extract/SKILL.md` —— 不动
- 数据库 DDL —— **不动**（只是把 SQL 字符串移到 .sql 文件）

---

## 八、工作量评估

代码 + 测试一起算,因为"测试是实施的一部分"。

| Phase | 实施文件 | 测试文件 | 实施代码 | 测试代码 | 测试用例数 | 风险 | 估时 |
|---|---|---|---|---|---|---|---|
| R1 基础设施 + ORM | ~12 | ~10 | ~800 行 | ~500 行 | 17 | 低 | **1.5-2.5 天** |
| R2 repository | ~54(新增 36 + 改 18) | ~40 | ~2000 行 | ~1500 行 | 30 | 中 | **3-4 天** |
| R3 聚合根重组 + application | git mv + 新增 ~10 | ~15 | ~600 行 | ~500 行 | 17 | 中高 | **2.5-3.5 天** |
| R4（可选）observability + CLI | ~6 | ~10 | ~300 行 | ~300 行 | 9 | 低 | **1-1.5 天** |
| **合计** | | | **~3700 行** | **~2800 行** | **~73 个** | | **8-11 天** |

> 测试时间按 "实施时间 × 0.4" 估算（业内常见比例 30%-50%）。比裸做实施多 3-4 天，但能避免后期 debug 浪费 1-2 周。

---

## 九、何时开始

**前置条件**：
1. 数据采集 + 事件抽取 + 决策 三条主链路功能基本稳定（不会再大改业务逻辑）
2. 至少跑通过 1 次完整的端到端流程（已具备 ✅）
3. 有 1 整周专心做架构重构的时间（不被业务功能开发打断）

**不应该现在开始**的情况：
- 还在快速迭代业务策略（每天都在改 score 公式 / 改阈值）
- 数据 schema 还在变动（最近 1 周内还加过新字段）
- 没有时间做端到端回归测试

**建议节奏**：
- **R1 单独做**：风险最低，做完就能享受到 ORM 的字段提示和类型检查带来的好处（即使业务代码还是用旧的 raw SQL）
- **R2 + R3 一起做**：因为 R3 的目录重组会影响 R2 的 import 路径，建议连续做完
- **R4 最后做**：可选，不影响功能

---

## 十、完成后的收益

1. **字段含义可见**：IDE 自动补全 + Mapped 类型注解 + comment 注释，新人 5 分钟看懂表
2. **改 schema 一处搞定**：加字段只改 ORM 模型 + DDL 文件，不需要找散落的 INSERT 语句
3. **类型安全**：`Decimal` vs `float`、`datetime` vs `str`、`list[int]` vs `int[]` 全部由 SQLAlchemy 校验
4. **JSONB 自动反序列化**：`row.industries` 直接拿到 list，不需要 `json.loads()`
5. **事务边界清晰**：`with session.begin():` 包多张表的更新自动原子
6. **测试可 mock**：domain service 测试用 mock repository，不需要真实 PG
7. **新人友好**：DDD 分层是通用知识，不需要教"我们这里 fund_db.py 干什么用的"
8. **未来扩展空间**：加新数据源、加新决策模型、加 web UI 都有明确的位置放
