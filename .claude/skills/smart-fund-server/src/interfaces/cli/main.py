"""统一 CLI 入口 — click 命令组

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


@cli.command()
@click.option("-c", "--concurrency", type=int, default=1, help="并发数")
@click.argument("tasks", nargs=-1)
def worker(concurrency: int, tasks: tuple[str, ...]):
    """启动 jettask Worker

    不传 TASKS 则消费全部任务，传则只消费指定的：

    \b
      worker                          # 全部 12 个任务
      worker agg_news agg_fund_flow   # 只跑这两个
    """
    from src.interfaces.tasks import app

    ALL_TASKS = [
        # "agg_news",
        "agg_fund_flow",
        # "agg_market",
        "agg_macro",
        # "agg_sentiment",
        # "agg_event_extraction", "agg_event_stream", "agg_event_feedback",
        # "trade_decision", "trade_execution", "trade_monitor",
        # "review_decision",
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

    click.echo(f"📦 启动 Persist  db_url={DB_URL}")
    app.start_persist(db_url=DB_URL)


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


@cli.command()
@click.argument("queues", nargs=-1)
@click.option("--list", "list_only", is_flag=True, help="只列出可触发的 queue")
def trigger(queues: tuple[str, ...], list_only: bool):
    """手动触发聚合任务(绕过 scheduler)"""
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
            elif f"agg_{a}" in qset:
                target.append(f"agg_{a}")
            elif f"trade_{a}" in qset:
                target.append(f"trade_{a}")
            else:
                click.echo(f"⚠️  未知 queue: {a}", err=True)
        if not target:
            click.echo("❌ 没有匹配的 queue,用 --list 查看", err=True)
            app.close()
            sys.exit(1)
    else:
        target = available

    msgs = [TaskMessage(queue=q, kwargs={}) for q in target]
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
    count = app.schedule_register(SCHEDULES)
    click.echo(f"✅ 已发送 {count} 个调度命令")
    app.close()


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


if __name__ == "__main__":
    cli()
