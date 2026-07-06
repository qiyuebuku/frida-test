"""统一 CLI 入口 — click 命令组

    python -m src.interfaces.cli api [--host H] [--port P] [--reload]
    python -m src.interfaces.cli llm-proxy [--host H] [--port P] [--reload]
    python -m src.interfaces.cli worker [-c N]
    python -m src.interfaces.cli scheduler
    python -m src.interfaces.cli persist
    python -m src.interfaces.cli trigger [queue...]
    python -m src.interfaces.cli init db [--target prod|test]
    python -m src.interfaces.cli init state [--reset]
    python -m src.interfaces.cli init schedules
    python -m src.interfaces.cli init all
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import click

from src.infrastructure.observability import configure_logging


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
@click.argument("tasks", nargs=-1)
def worker(concurrency: int, tasks: tuple[str, ...]):
    """启动 jettask Worker

    不传 TASKS 则消费全部任务，传则只消费指定的：

    \b
      worker                          # 默认采集任务
      worker collect_news collect_fund_flow   # 只跑这两个
    """
    from src.interfaces.tasks import app

    ALL_TASKS = [
        "collect_news",
        "collect_fund_flow",
        "collect_market",
        "collect_macro",
        "collect_sentiment",
        "kg_news_ingest",
        "kg_community_insight_refresh",
    ]
    task_names = list(tasks) if tasks else ALL_TASKS

    click.echo(f"🚀 启动 Worker（并发={concurrency}）")
    click.echo(f"   任务: {', '.join(task_names)}")
    app.start_worker(task_names=task_names, concurrency=concurrency, prefetch=100)


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
    click.echo("   包含 LLM 代理接口: /api/llm-proxy/health  /v1/chat/completions  /v1/embeddings")
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


# ==================== 数据审计 ====================


@cli.command("data-types")
def data_types():
    """查看所有 data_type 及其在 DB 中的数据量"""
    from sqlalchemy import text
    from src.infrastructure.connections import get_session

    with get_session() as s:
        click.echo("═══ ft_market_flow ═══")
        rows = s.execute(text(
            "SELECT data_type, count(*), min(trade_date), max(trade_date) "
            "FROM ft_market_flow GROUP BY data_type ORDER BY data_type"
        )).fetchall()
        for r in rows:
            click.echo(f"  {r[0]:25} {r[1]:>6} 条  {r[2]} ~ {r[3]}")

        click.echo("\n═══ ft_watchlist_data ═══")
        rows = s.execute(text(
            "SELECT data_type, count(*), count(DISTINCT code) "
            "FROM ft_watchlist_data GROUP BY data_type ORDER BY data_type"
        )).fetchall()
        for r in rows:
            click.echo(f"  {r[0]:25} {r[1]:>6} 条  {r[2]} 只标的")

        codes = s.execute(text("SELECT count(DISTINCT code) FROM ft_watchlist_data")).scalar()
        click.echo(f"\n  覆盖标的: {codes}")


# ==================== 手动触发 ====================


QUEUE_TO_COLLECTION_AGGREGATOR = {
    "collect_news": "news",
    "collect_fund_flow": "fund_flow",
    "collect_market": "market",
    "collect_sentiment": "sentiment",
    "collect_macro": "macro",
}


def _reset_collection_intervals_for_trigger(target_queues: list[str]) -> int:
    """手动触发采集任务时清空 last_run_at，避免消息被 source interval 跳过。"""
    aggregators = {
        QUEUE_TO_COLLECTION_AGGREGATOR[queue]
        for queue in target_queues
        if queue in QUEUE_TO_COLLECTION_AGGREGATOR
    }
    if not aggregators:
        return 0

    from sqlalchemy import func, update

    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models.collection import CollectionState

    with get_session() as session:
        result = session.execute(
            update(CollectionState)
            .where(CollectionState.aggregator.in_(aggregators))
            .values(last_run_at=None, updated_at=func.now())
        )
        return int(result.rowcount or 0)


@cli.command()
@click.argument("queues", nargs=-1)
@click.option("--list", "list_only", is_flag=True, help="只列出可触发的 queue")
@click.option("--news-id", "news_ids", multiple=True, type=int, help="触发 kg_news_ingest 时传入的 ft_news.id，可重复")
def trigger(queues: tuple[str, ...], list_only: bool, news_ids: tuple[int, ...]):
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
        if q == "kg_news_ingest":
            msgs.append(TaskMessage(queue=q, kwargs=kwargs, max_retries=10, timeout=5000))
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


# ==================== init 子命令组 ====================

AGGREGATORS = [
    ("news", "src.domain.collection.services.news", "NewsAggregator"),
    ("fund_flow", "src.domain.collection.services.fund_flow", "FundFlowAggregator"),
    ("market", "src.domain.collection.services.market", "MarketAggregator"),
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
    from src.interfaces.cli.schedules import SCHEDULES
    from src.interfaces.tasks import app

    click.echo(f"注册 {len(SCHEDULES)} 个调度:")
    for s in SCHEDULES:
        click.echo(f"  - {s.name}")
    try:
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

cli.add_command(kg)


if __name__ == "__main__":
    cli()
