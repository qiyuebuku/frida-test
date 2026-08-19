"""统一 CLI 入口 — click 命令组

    python -m src.interfaces.cli api [--host H] [--port P] [--reload]
    python -m src.interfaces.cli llm-proxy [--host H] [--port P] [--reload]
    python -m src.interfaces.cli worker [-c N]
    python -m src.interfaces.cli scheduler
    python -m src.interfaces.cli persist
    python -m src.interfaces.cli agent check
    python -m src.interfaces.cli agent run research-context.json --json-output
    python -m src.interfaces.cli trigger [queue...]
    python -m src.interfaces.cli init db [--target prod|test]
    python -m src.interfaces.cli init state [--reset]
    python -m src.interfaces.cli init schedules
    python -m src.interfaces.cli init all
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import click

from src.infrastructure.observability import configure_logging


COLLECTION_WORKER_TASKS = (
    "collect_collection_source",
    "advance_collection_backfill",
    "scan_watchlist_instruments",
    "scan_watchlist_daily",
    "scan_watchlist_reference",
    "collect_watchlist_instruments",
    "collect_market_breadth_snapshot",
    "collect_stock_rankings",
    "collect_stock_dynamic_groups",
    "collect_stock_change_events",
    "collect_ths_market_events",
    "collect_ths_market_context",
    "collect_ths_market_profile",
    "collect_market_boundary_snapshot",
    "collect_sector_market_snapshot",
    "collect_sector_fund_flow_snapshot",
    "collect_ths_sector_fragment_v2",
    "collect_ths_sector_reference_snapshot_v2",
    "collect_ths_sector_signal_fragment_v2",
    "collect_cross_market_snapshot",
    "collect_etf_estimated_net_inflow",
    "collect_ths_etf_zone",
    "collect_ths_futures_zone",
    "collect_ths_futures_fragment",
    "collect_ths_futures_cycle",
    "collect_ths_gold_zone",
    "collect_ths_us_overview",
    "collect_ths_us_sectors",
    "collect_ths_us_stock_rankings",
    "collect_ths_us_etf_sectors",
    "collect_etf_daily_shares",
    "collect_pboc_rate_liquidity",
    "collect_ths_index_sentiment",
    "collect_market_daily_bars",
    "collect_market_reference_data",
    "collect_market_daily_catchup",
    "collect_market_valuation",
    "collect_bond_index",
    "materialize_sentiment_signal",
)

COLLECTION_WORKER_GROUPS = {
    # Dedicated latency-sensitive lane. A JetTask queue must have exactly one
    # worker-pool owner: registering the same queue in multiple pools lets one
    # process reserve messages that the other pool can no longer execute in
    # time, producing an ever-growing stale snapshot backlog.
    "ths-sector": (
        "collect_ths_sector_fragment_v2",
        "collect_ths_sector_reference_snapshot_v2",
        "collect_ths_sector_signal_fragment_v2",
    ),
    "ths": (
        "collect_market_breadth_snapshot",
        "collect_market_boundary_snapshot",
        "collect_stock_rankings",
        "collect_stock_dynamic_groups",
        "collect_stock_change_events",
        "collect_sector_market_snapshot",
        "collect_sector_fund_flow_snapshot",
        "collect_cross_market_snapshot",
        "collect_ths_market_events",
        "collect_ths_market_context",
        "collect_ths_market_profile",
        "collect_etf_estimated_net_inflow",
        "collect_ths_etf_zone",
        "collect_ths_futures_zone",
        "collect_ths_futures_fragment",
        "collect_ths_futures_cycle",
        "collect_ths_gold_zone",
        "collect_ths_us_overview",
        "collect_ths_us_sectors",
        "collect_ths_us_stock_rankings",
        "collect_ths_us_etf_sectors",
        "collect_ths_index_sentiment",
    ),
    # HTTP and internal jobs share one general-purpose pool. Their combined
    # concurrency preserves the previous aggregate capacity while removing a
    # redundant long-lived worker process. THS lanes remain isolated because
    # they have device-channel limits and latency-sensitive schedules.
    "general": (
        "collect_collection_source",
        "advance_collection_backfill",
        "collect_pboc_rate_liquidity",
        "collect_market_daily_bars",
        "collect_market_reference_data",
        "collect_market_valuation",
        "collect_bond_index",
        "collect_etf_daily_shares",
        "materialize_sentiment_signal",
        "scan_watchlist_instruments",
        "scan_watchlist_daily",
        "scan_watchlist_reference",
        "collect_watchlist_instruments",
        "collect_market_daily_catchup",
        "run_research_agent",
        "evaluate_research_outcomes",
        "consolidate_research_memory",
    ),
}


@click.group()
@click.option("--log-level", default=None, help="日志级别(DEBUG/INFO/WARNING/ERROR)")
def cli(log_level: str | None):
    """smart-fund-server 统一命令行"""
    configure_logging(level=log_level)


# ==================== worker / scheduler / persist ====================


def _ensure_jettask_partitions(db_url: str, *, months_back: int = 3, months_ahead: int = 6) -> int:
    """Ensure jettask queue partitions around the current month.

    PostgreSQL partition DDL cannot be expressed through SQLAlchemy ORM, so this
    helper intentionally uses SQLAlchemy text() for partition maintenance only.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from sqlalchemy import create_engine, text

    def add_months(dt: datetime, offset: int) -> datetime:
        year = dt.year + (dt.month - 1 + offset) // 12
        month = (dt.month - 1 + offset) % 12 + 1
        return dt.replace(year=year, month=month)

    sync_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(sync_url)
    sh_tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(sh_tz)
    base_month = datetime(now.year, now.month, 1, tzinfo=sh_tz)
    ensured = 0

    try:
        with engine.begin() as conn:
            parent_tables = (
                "tasks",
                "task_runs",
                "task_metrics_minute",
                "task_runs_metrics_minute",
            )
            for parent_table in parent_tables:
                table_exists = conn.execute(text(f"SELECT to_regclass('public.{parent_table}')")).scalar()
                if table_exists is None:
                    continue

                partkey = conn.execute(text(f"SELECT pg_get_partkeydef('public.{parent_table}'::regclass)")).scalar()
                if not partkey:
                    continue

                for offset in range(-months_back, months_ahead + 1):
                    start_local = add_months(base_month, offset)
                    end_local = add_months(base_month, offset + 1)
                    partition_name = f"{parent_table}_{start_local.year}_{start_local.month:02d}"
                    start_utc = start_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")
                    end_utc = end_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00")

                    conn.execute(text(
                        f"""
                        CREATE TABLE IF NOT EXISTS public.{partition_name}
                        PARTITION OF public.{parent_table}
                        FOR VALUES FROM ('{start_utc}') TO ('{end_utc}')
                        """
                    ))
                    ensured += 1
    finally:
        engine.dispose()

    return ensured


@cli.command()
@click.option("-c", "--concurrency", type=int, default=1, help="并发数")
@click.option(
    "--group",
    "worker_group",
    type=click.Choice(sorted(COLLECTION_WORKER_GROUPS)),
    default=None,
    help="按采集通道启动隔离 Worker 池",
)
@click.argument("tasks", nargs=-1)
def worker(
    concurrency: int,
    worker_group: str | None,
    tasks: tuple[str, ...],
):
    """启动 jettask Worker

    不传 TASKS 则消费全部任务，传则只消费指定的：

    \b
      worker                          # 默认采集任务
      worker collect_news collect_fund_flow   # 只跑这两个
    """
    from src.interfaces.tasks import app
    from src.infrastructure.persistence.repositories import (
        CollectionRunRepository,
    )

    if worker_group and tasks:
        raise click.UsageError("--group 与显式 TASKS 不能同时使用")
    task_names = (
        list(COLLECTION_WORKER_GROUPS[worker_group])
        if worker_group
        else (list(tasks) if tasks else list(COLLECTION_WORKER_TASKS))
    )

    interrupted = CollectionRunRepository().finish_interrupted_running()
    if interrupted:
        click.echo(f"已关闭上次 Worker 中断遗留的运行记录: {interrupted} 条")
    click.echo(
        f"🚀 启动 Worker（通道={worker_group or 'all'}，并发={concurrency}）"
    )
    click.echo(f"   任务: {', '.join(task_names)}")
    # Keep the reservation window proportional to executable capacity.  A
    # fixed prefetch=100 let one process claim hours of periodic snapshots;
    # after a restart Jettask reclaimed those stale PEL entries before current
    # futures/ETF tasks and all eight slots starved on obsolete work.
    app.start_worker(
        task_names=task_names,
        concurrency=concurrency,
        prefetch=max(concurrency * 2, 4),
    )


@cli.command("knowledge-worker")
@click.option(
    "--stage",
    type=click.Choice(["card", "relation", "graph", "all"]),
    default="all",
    show_default=True,
    help="知识工作流阶段；生产环境建议三个阶段分别启动",
)
@click.option("-c", "--concurrency", type=int, default=1, help="并发数")
def knowledge_worker(stage: str, concurrency: int):
    """启动关系优先知识图谱 Worker。"""

    from src.interfaces.tasks import app

    task_groups = {
        "card": ["kg_news_ingest"],
        "relation": ["kg_relation_discovery"],
        "graph": ["kg_graph_community_refresh"],
        "all": [
            "kg_news_ingest",
            "kg_relation_discovery",
            "kg_graph_community_refresh",
        ],
    }
    task_names = task_groups[stage]
    click.echo(f"启动知识 Worker（阶段={stage}，并发={concurrency}）")
    click.echo(f"任务: {', '.join(task_names)}")
    app.start_worker(
        task_names=task_names,
        concurrency=concurrency,
        # Graph refreshes may expand a large affected subgraph.  Reserving 100
        # messages made one single-concurrency process retain a large stale
        # backlog and repeat nearly identical aggregation work for hours.
        prefetch=max(concurrency * 2, 4),
    )


@cli.command()
def scheduler():
    """启动 jettask Scheduler 按 Schedule 定时发消息"""
    from src.interfaces.tasks import app, DB_URL

    click.echo(f"🕐 启动 Scheduler  db_url={DB_URL}")
    app.start_scheduler(db_url=DB_URL)


@cli.command()
def persist():
    """启动 jettask Persist 消费 _commands 队列落库"""
    from src.interfaces.tasks import app, DB_URL

    partition_count = _ensure_jettask_partitions(DB_URL)
    if partition_count:
        click.echo(f"✅ jettask 分区已检查/初始化: {partition_count} 个")
    click.echo(f"📦 启动 Persist  db_url={DB_URL}")
    app.start_persist(db_url=DB_URL)


# ==================== API / LLM Proxy ====================


def _run_api_server(host: str, port: int, reload: bool) -> None:
    import uvicorn

    click.echo(f"🌐 启动 API 服务  http://{host}:{port}")
    click.echo("   API 文档: /docs")
    click.echo("   Market Data Observatory: /market-dashboard")
    click.echo("   Graph Community Explorer: /api/kg/graph-viewer")
    click.echo("   Relation Graph MCP: /mcp")
    click.echo("   Browser Spy: /api/spy/status")
    click.echo("   LLM Proxy: /api/llm-proxy/health  /v1/chat/completions  /v1/embeddings")
    uvicorn.run("main:app", host=host, port=port, reload=reload)


@cli.command()
@click.option("--host", default=None, help="监听地址，默认使用 SERVER_HOST")
@click.option("--port", default=None, type=int, help="监听端口，默认使用 SERVER_PORT")
@click.option("--reload", is_flag=True, help="开发模式热重载")
def api(host: str | None, port: int | None, reload: bool):
    """启动 FastAPI 服务（包含交易、采集、LLM代理接口）"""
    from src.infrastructure.config.settings import SERVER_HOST, SERVER_PORT

    _run_api_server(host or SERVER_HOST, port or SERVER_PORT, reload)


@cli.command("llm-proxy")
@click.option("--host", default=None, help="监听地址，默认使用 SERVER_HOST")
@click.option("--port", default=None, type=int, help="监听端口，默认使用 SERVER_PORT")
@click.option("--reload", is_flag=True, help="开发模式热重载")
def llm_proxy(host: str | None, port: int | None, reload: bool):
    """启动包含 Claude LLM 代理接口的 API 服务"""
    from src.infrastructure.config.settings import SERVER_HOST, SERVER_PORT

    _run_api_server(host or SERVER_HOST, port or SERVER_PORT, reload)


@cli.command("ths-realtime-stream")
@click.option("--host", default=None, help="ADB 转发监听地址")
@click.option("--port", default=None, type=int, help="ADB 转发监听端口")
def ths_realtime_stream(host: str | None, port: int | None):
    """维护一个同花顺 App 会话中的多路实时订阅并异步入库。"""
    import asyncio
    import signal

    from src.application.services.ths_realtime_stream_service import (
        THSRealtimeStreamService,
    )

    async def run() -> None:
        service = THSRealtimeStreamService(host=host, port=port)
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        for name in ("SIGINT", "SIGTERM"):
            value = getattr(signal, name, None)
            if value is not None:
                loop.add_signal_handler(value, stop_event.set)
        service_task = asyncio.create_task(
            service.run(),
            name="ths-realtime-stream",
        )
        stop_task = asyncio.create_task(
            stop_event.wait(),
            name="ths-realtime-stream-stop",
        )
        done, _pending = await asyncio.wait(
            {service_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            await service.stop()
        else:
            stop_task.cancel()
        await service_task

    click.echo("启动同花顺单 App 多路实时订阅")
    asyncio.run(run())


# ==================== 数据审计 ====================


@cli.command("data-types")
def data_types():
    """查看所有 data_type 及其在 DB 中的数据量"""
    from sqlalchemy import func, select
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models.collection import (
        InstrumentDisclosure,
        InstrumentObservation,
        InstrumentProfile,
        MarketFlow,
    )

    with get_session() as s:
        click.echo("═══ ft_market_flow ═══")
        rows = s.execute(
            select(MarketFlow.data_type, func.count(), func.min(MarketFlow.trade_date), func.max(MarketFlow.trade_date))
            .group_by(MarketFlow.data_type).order_by(MarketFlow.data_type)
        ).all()
        for r in rows:
            click.echo(f"  {r[0]:25} {r[1]:>6} 条  {r[2]} ~ {r[3]}")

        for model in (InstrumentProfile, InstrumentDisclosure, InstrumentObservation):
            click.echo(f"\n═══ {model.__tablename__} ═══")
            rows = s.execute(
                select(model.data_type, func.count(), func.count(func.distinct(model.code)))
                .group_by(model.data_type).order_by(model.data_type)
            ).all()
            for r in rows:
                click.echo(f"  {r[0]:25} {r[1]:>6} 条  {r[2]} 只标的")


# ==================== 手动触发 ====================


def _reset_collection_intervals_for_trigger(target_queues: list[str]) -> int:
    """采集周期已迁移到 JetTask；手工触发不再修改 checkpoint 状态。"""
    return 0


@cli.command()
@click.argument("queues", nargs=-1)
@click.option("--list", "list_only", is_flag=True, help="只列出可触发的 queue")
@click.option("--news-id", "news_ids", multiple=True, type=int, help="触发 kg_news_ingest 时传入的 ft_news.id，可重复")
@click.option("--card-id", "card_ids", multiple=True, type=str, help="触发 kg_relation_discovery 时传入的 Card ID，可重复")
def trigger(
    queues: tuple[str, ...],
    list_only: bool,
    news_ids: tuple[int, ...],
    card_ids: tuple[str, ...],
):
    """手动触发任务(绕过 scheduler)"""
    from jettask import TaskMessage
    from src.interfaces.tasks import app

    available = sorted({info.queue for info in app._tasks.values()})
    if list_only:
        click.echo(f"可触发的 queue(共 {len(available)} 个):")
        for q in available:
            click.echo(f"  - {q}")
        app.close()
        return

    if queues:
        qset = set(available)
        target: list[str] = []
        for a in queues:
            if a in qset:
                target.append(a)
            elif f"collect_{a}" in qset:
                target.append(f"collect_{a}")
            else:
                click.echo(f"⚠️  未知 queue: {a}", err=True)
        if not target:
            click.echo("❌ 没有匹配的 queue,用 --list 查看", err=True)
            app.close()
            sys.exit(1)
    else:
        target = available

    reset_count = _reset_collection_intervals_for_trigger(target)
    if reset_count:
        click.echo(f"⏰ 已重置 {reset_count} 条采集状态的 last_run_at，手动触发会绕过 interval")

    msgs = []
    for q in target:
        kwargs = {"news_ids": list(news_ids)} if q == "kg_news_ingest" and news_ids else {}
        if q == "kg_relation_discovery" and card_ids:
            kwargs = {"card_ids": list(card_ids)}
        if q == "kg_news_ingest":
            msgs.append(TaskMessage(queue=q, kwargs=kwargs, max_retries=10, timeout=5000))
        elif q == "kg_relation_discovery":
            msgs.append(TaskMessage(queue=q, kwargs=kwargs, max_retries=5, timeout=5000))
        else:
            msgs.append(TaskMessage(queue=q, kwargs=kwargs))
    click.echo(f"🚀 触发 {len(msgs)} 个 task:")
    for q in target:
        click.echo(f"   → {q}")
    ids = app.send_sync(msgs)
    click.echo("✅ 发送成功,event_ids:")
    for q, eid in zip(target, ids):
        click.echo(f"   {q:<24} {eid}")
    app.close()


# ==================== collection 运维命令 ====================


@cli.group("collection")
def collection():
    """数据采集状态与历史回填运维。"""


@collection.command("backfill-sources")
@click.option(
    "--aggregator",
    type=click.Choice(["news", "fund_flow", "market", "sentiment", "macro"]),
    default=None,
    help="只查看指定 aggregator",
)
def collection_backfill_sources(aggregator: str | None):
    """列出各 source 的历史回填能力和当前状态。"""
    from src.application.services.collection_backfill_service import (
        CollectionBackfillError,
        CollectionBackfillService,
    )

    service = CollectionBackfillService()
    try:
        rows = service.list_capabilities(aggregator)
    except CollectionBackfillError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"{'aggregator':<12} {'source':<24} {'supported':<10} "
        f"{'mode':<12} {'oldest':<12} {'target_days':>11}"
    )
    for item in rows:
        click.echo(
            f"{item['aggregator']:<12} "
            f"{item['source_name']:<24} "
            f"{str(item['supported']):<10} "
            f"{str(item['mode'] or '-'):<12} "
            f"{str(item['oldest_time'] or '-')[:10]:<12} "
            f"{item['configured_target_days']:>11}"
        )


@collection.command("backfill")
@click.argument(
    "aggregator",
    type=click.Choice(["news", "fund_flow", "market", "sentiment", "macro"]),
)
@click.argument("source_name")
@click.option("--start-date", required=True, help="历史回填目标日期 YYYY-MM-DD")
@click.option("--dry-run", is_flag=True, help="只预览 checkpoint 变更，不写数据库")
def collection_backfill(
    aggregator: str,
    source_name: str,
    start_date: str,
    dry_run: bool,
):
    """将单个支持历史接口的 source 切换到受控回填模式。"""
    from src.application.services.collection_backfill_service import (
        CollectionBackfillError,
        CollectionBackfillService,
    )
    from src.application.services.collection_backfill_chain_service import (
        CollectionBackfillChainService,
    )

    try:
        if dry_run:
            result = CollectionBackfillService().prepare(
                aggregator=aggregator,
                source_name=source_name,
                start_date=start_date,
                dry_run=True,
            )
        else:
            data = asyncio.run(CollectionBackfillChainService().start(
                aggregator=aggregator,
                source_name=source_name,
                start_date=start_date,
            ))
            result = None
    except CollectionBackfillError as exc:
        raise click.ClickException(str(exc)) from exc

    if result is not None:
        data = result.to_dict()
    click.echo(f"status={data['status']} changed={data['changed']} dry_run={data['dry_run']}")
    click.echo(
        f"source={data['aggregator']}:{data['source_name']} "
        f"target={data['target_time']} queue={data['queue']}"
    )
    click.echo(
        f"previous_mode={data['previous_mode']} "
        f"oldest={data['oldest_time'] or '-'} newest={data['newest_time'] or '-'} "
        f"cursor_preserved={data['cursor_preserved']}"
    )
    if data["warning"]:
        click.echo(f"warning={data['warning']}", err=True)
    if data["changed"] and not dry_run:
        click.echo(
            "已启动独立链式回填；完成后自动停止投递，长期 Schedule 不参与回填"
        )


# ==================== init 子命令组 ====================

AGGREGATORS = [
    ("news", "src.domain.collection.services.news", "NewsAggregator"),
    ("fund_flow", "src.domain.collection.services.fund_flow", "FundFlowAggregator"),
    ("market", "src.domain.collection.services.market", "MarketAggregator"),
    (
        "market_observation",
        "src.application.services.market_observation_service",
        "MarketObservationService",
    ),
    ("sentiment", "src.domain.collection.services.sentiment", "SentimentAggregator"),
    ("macro", "src.domain.collection.services.macro", "MacroAggregator"),
]


@cli.group()
def init():
    """初始化命令组（首次部署时执行）"""
    pass


@init.command("db")
@click.option("--target", type=click.Choice(["prod", "test"]), default="test")
@click.option("--no-drop", is_flag=True, help="不 drop 已存在的表")
@click.option("--yes", is_flag=True, help="对 prod 库的确认标记")
def init_db(target: str, no_drop: bool, yes: bool):
    """按 schema/*.sql 初始化数据库表结构"""
    import subprocess

    script = Path(__file__).resolve().parents[3] / "scripts" / "init_db.py"
    cmd = [sys.executable, str(script), f"--target={target}"]
    if no_drop:
        cmd.append("--no-drop")
    if yes:
        cmd.append("--yes")
    click.echo(f"▶ {' '.join(cmd)}")
    sys.exit(subprocess.call(cmd))


@init.command("state")
@click.option("--reset", is_flag=True, help="清空已有记录后重新初始化")
def init_state(reset: bool):
    """初始化 ft_collection_state 采集状态"""
    import importlib

    if reset:
        from src.infrastructure.db import checkpoint_store
        existing = checkpoint_store.list_all()
        if existing:
            click.echo(f"⚠️  即将清空 {len(existing)} 条 ft_collection_state 记录")
            if not click.confirm("确认?"):
                return
            from src.infrastructure.connections import get_session
            from sqlalchemy import text
            with get_session() as s:
                s.execute(text("TRUNCATE ft_collection_state RESTART IDENTITY"))
            click.echo("已清空")

    total = 0
    for name, mod_path, cls_name in AGGREGATORS:
        try:
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, cls_name)
            cls.init_state()
            count = len(cls.SOURCE_CONFIGS)
            click.echo(f"  ✅ {name}: {count} 个源")
            total += count
        except Exception as e:
            click.echo(f"  ❌ {name}: {e}", err=True)

    click.echo(f"\n✅ 初始化完成，共 {total} 个源")


@init.command("schedules")
def init_schedules():
    """注册定时调度到 jettask scheduler（需要 Persist 在跑）"""
    from src.interfaces.cli.schedules import (
        SCHEDULES,
        RETIRED_REDUNDANT_MARKET_SCHEDULE_NAMES,
        THS_LEGACY_FUTURES_SCHEDULE_NAMES,
        THS_LEGACY_SECTOR_SCHEDULE_NAMES,
    )
    # Schedule registration only needs the command publisher. Importing
    # ``src.interfaces.tasks`` eagerly initializes every market client and raw
    # data partition, which can block indefinitely when an App bridge is busy.
    # Keep this control-plane command independent from worker business startup.
    from jettask import Jettask
    from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL

    app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)

    click.echo(f"注册 {len(SCHEDULES)} 个调度:")
    for s in SCHEDULES:
        click.echo(f"  - {s.name}")
    try:
        schedule_names = [schedule.name for schedule in SCHEDULES]
        deleted = app.schedule_delete(
            [
                *schedule_names,
                "collect_news_3min",
                "collect_market_valuation_after_close",
                "collect_bond_index_after_close",
                "collect_fund_flow_5min",
                "collect_market_1min",
                "collect_ths_sector_core_60s",
                "collect_stock_rankings_30s",
                "collect_stock_dynamic_groups_60s",
                "collect_ths_us_market_zone_120s",
                "collect_ths_us_stock_rankings_180s",
                "collect_ths_us_stock_rankings_60s",
                "collect_ths_us_etf_sectors_300s",
                "collect_ths_etf_zone_60s",
                "collect_ths_futures_zone_60s",
                "collect_ths_futures_zone_120s",
                *THS_LEGACY_FUTURES_SCHEDULE_NAMES,
                *THS_LEGACY_SECTOR_SCHEDULE_NAMES,
                *RETIRED_REDUNDANT_MARKET_SCHEDULE_NAMES,
            ]
        )
        click.echo(f"  已删除 {deleted} 个旧调度定义")
        count = app.schedule_register(SCHEDULES)
    except RuntimeError as exc:
        if "command result polling timeout" in str(exc):
            click.echo(
                "❌ 注册调度超时：jettask persist 没有运行或没有消费当前 JETTASK_PREFIX 的 _commands 队列。\n"
                "请在另一个终端先启动：python -m src.interfaces.cli persist\n"
                "然后重新执行：python -m src.interfaces.cli init schedules",
                err=True,
            )
            app.close()
            sys.exit(1)
        raise
    click.echo(f"✅ 已发送 {count} 个调度命令")
    app.close()


# ==================== 手动物化命令 ====================


@cli.command("materialize-sentiment")
@click.option("--date", "trade_date", default=None, help="指定日期 YYYY-MM-DD，默认今天")
def materialize_sentiment(trade_date: str | None):
    """手动执行 L2 情绪信号物化（写 ft_sentiment_signal）"""
    import asyncio
    from src.application.services.collection_app_service import CollectionAppService

    svc = CollectionAppService()
    result = asyncio.run(svc.materialize_sentiment_signal(trade_date=trade_date))
    click.echo(f"✅ {result.to_dict()}")


@init.command("all")
@click.option("--target", type=click.Choice(["prod", "test"]), default="test")
@click.pass_context
def init_all(ctx, target: str):
    """一键执行全部初始化: db → state → schedules"""
    click.echo("═══ Step 1/3: 初始化数据库 ═══")
    ctx.invoke(init_db, target=target, no_drop=False, yes=False)

    click.echo("\n═══ Step 2/3: 初始化采集状态 ═══")
    ctx.invoke(init_state, reset=False)

    click.echo("\n═══ Step 3/3: 注册定时调度 ═══")
    ctx.invoke(init_schedules)


from src.interfaces.cli.knowledge import kg
from src.interfaces.cli.agent import agent

cli.add_command(kg)
cli.add_command(agent)


if __name__ == "__main__":
    cli()
