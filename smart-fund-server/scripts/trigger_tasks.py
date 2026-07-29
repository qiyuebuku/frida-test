"""手动触发 task — 绕过 scheduler 立即执行

通过 jettask app.send_sync 直接往 queue 发消息，让正在运行的 Worker 立即消费。
触发前自动重置 ft_collection_state.last_run_at，绕过采集间隔检查。
queue 名从 app._tasks 自动发现，不会和 tasks router 的 @router.task 失同步。

用法:
    # 触发全部 task（自动重置间隔）
    python scripts/trigger_tasks.py

    # 只触发指定 queue（可写完整名或省略 agg_ 前缀）
    python scripts/trigger_tasks.py news fund_flow market
    python scripts/trigger_tasks.py collect_news collect_sentiment

    # 列出所有可触发的 queue
    python scripts/trigger_tasks.py --list

注意：
    - Worker 必须在跑，否则消息会进入 jettask-rs 队列等待消费
    - 本脚本不传 args/kwargs；kg_news_ingest 需要 news_ids，通常由 collect_news 级联触发
    - 触发是 fire-and-forget，本脚本只发不等结果
    - 采集类任务（collect_news/fund_flow/market/sentiment/macro）会自动重置 last_run_at
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jettask import TaskMessage
from src.interfaces.tasks import app

# queue → ft_collection_state.aggregator 映射（只有采集类任务需要重置间隔）
QUEUE_TO_AGGREGATOR = {
    "collect_news": "news",
    "collect_fund_flow": "fund_flow",
    "collect_market": "market",
    "collect_sentiment": "sentiment",
    "collect_macro": "macro",
}


def all_queues() -> list[str]:
    """从 app 注册表自动发现所有 queue（去重 + 排序）"""
    return sorted({info.queue for info in app._tasks.values()})


def resolve(args: list[str], queues: list[str]) -> list[str]:
    """把用户输入解析为 queue 列表，支持省略 agg_ 前缀"""
    qset = set(queues)
    wanted: list[str] = []
    seen: set[str] = set()
    for a in args:
        candidate = None
        if a in qset:
            candidate = a
        elif f"collect_{a}" in qset:
            candidate = f"collect_{a}"
        elif f"trade_{a}" in qset:
            candidate = f"trade_{a}"
        else:
            print(f"⚠️  未知 queue: {a}")
            continue
        if candidate not in seen:
            seen.add(candidate)
            wanted.append(candidate)
    return wanted


def reset_collection_intervals(target_queues: list[str]) -> int:
    """重置采集任务的 last_run_at，绕过 interval 检查

    只影响采集类任务（agg_news/fund_flow/market/sentiment/macro）。
    非采集任务（L1/L2/trade 等）没有 interval 检查，无需重置。
    """
    aggregators = {QUEUE_TO_AGGREGATOR[q] for q in target_queues if q in QUEUE_TO_AGGREGATOR}
    if not aggregators:
        return 0

    from sqlalchemy import func, update
    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models.collection import CollectionState

    with get_session() as s:
        result = s.execute(
            update(CollectionState)
            .where(CollectionState.aggregator.in_(aggregators))
            .values(last_run_at=None, updated_at=func.now())
        )
        count = result.rowcount or 0

    if count:
        print(f"⏰ 已重置 {count} 条采集状态的 last_run_at（{', '.join(sorted(aggregators))}）")
    return count


def main():
    args = sys.argv[1:]
    queues = all_queues()

    if "--list" in args or "-l" in args:
        print(f"可触发的 queue（共 {len(queues)} 个）:")
        for q in queues:
            tag = " [采集]" if q in QUEUE_TO_AGGREGATOR else ""
            print(f"  - {q}{tag}")
        app.close()
        return

    if args:
        target = resolve(args, queues)
        if not target:
            print("❌ 没有匹配的 queue，用 --list 查看")
            app.close()
            sys.exit(1)
    else:
        target = queues

    # 自动重置采集间隔
    reset_collection_intervals(target)

    msgs = [TaskMessage(queue=q, kwargs={}) for q in target]
    print(f"🚀 触发 {len(msgs)} 个 task:")
    for q in target:
        print(f"   → {q}")

    ids = app.send_sync(msgs)
    print(f"\n✅ 发送成功，event_ids:")
    for q, eid in zip(target, ids):
        print(f"   {q:<24} {eid}")
    print("\n💡 提示：消息已入 jettask-rs 队列，Worker 会立即消费。")
    print("       看 worker 日志确认执行：tail -f <worker.log>")

    app.close()


if __name__ == "__main__":
    main()
