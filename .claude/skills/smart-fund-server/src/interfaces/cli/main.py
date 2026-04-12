"""统一 CLI 入口 — click 命令组

把 6 个散落的脚本合并成一个命令组：

    python -m src.interfaces.cli worker [-c N]
    python -m src.interfaces.cli scheduler
    python -m src.interfaces.cli persist
    python -m src.interfaces.cli register-schedules
    python -m src.interfaces.cli trigger [queue...]
    python -m src.interfaces.cli init-db [--target prod|test]

每个子命令只做最薄的参数解析 + 调后端逻辑，核心实现仍在
start_worker/start_scheduler/... 以及 scripts/init_db.py。
"""
import sys
from pathlib import Path

# 让 `python -m src.interfaces.cli` 和 `python src/interfaces/cli/main.py` 都能 import src.*
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
def worker(concurrency: int):
    """启动 jettask Worker 消费聚合任务队列"""
    from src.interfaces.tasks import app

    task_names = [
        "agg_news", "agg_fund_flow",
        "agg_macro", "agg_sentiment", "agg_market",
        "agg_event_extraction", "agg_event_stream",
        "trade_decision", "trade_execution", "trade_monitor",
        "agg_event_feedback", "review_decision",
    ]
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


# ==================== 注册调度 ====================


@cli.command("register-schedules")
def register_schedules_cmd():
    """把 Schedule 列表注册到 jettask scheduler（需要 Persist 在跑）"""
    from src.interfaces.cli.register_schedules import SCHEDULES
    from src.interfaces.tasks import app

    count = app.schedule_register(SCHEDULES)
    click.echo(f"✅ 已发送 {count} 个调度命令")
    app.close()


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


# ==================== 初始化数据库 ====================


@cli.command("init-db")
@click.option("--target", type=click.Choice(["prod", "test"]), default="test")
@click.option("--no-drop", is_flag=True, help="不 drop 已存在的表")
@click.option("--yes", is_flag=True, help="对 prod 库的确认标记")
def init_db_cmd(target: str, no_drop: bool, yes: bool):
    """按 schema/*.sql 初始化数据库"""
    import subprocess

    script = Path(__file__).resolve().parents[3] / "scripts" / "init_db.py"
    cmd = [sys.executable, str(script), f"--target={target}"]
    if no_drop:
        cmd.append("--no-drop")
    if yes:
        cmd.append("--yes")
    click.echo(f"▶ {' '.join(cmd)}")
    sys.exit(subprocess.call(cmd))


if __name__ == "__main__":
    cli()
