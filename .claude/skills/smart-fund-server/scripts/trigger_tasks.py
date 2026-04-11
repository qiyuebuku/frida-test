"""手动触发 aggregator task — 绕过 scheduler 立即执行

通过 jettask app.send_sync 直接往 queue 发消息，让正在运行的 Worker 立即消费。
queue 名从 app._tasks 自动发现，不会和 aggregator_tasks.py 的 @router.task 失同步。

用法:
    # 触发全部 12 个 task
    python scripts/trigger_tasks.py

    # 只触发指定 queue（可写完整名或省略 agg_ 前缀）
    python scripts/trigger_tasks.py news fund_flow market
    python scripts/trigger_tasks.py agg_news trade_decision

    # 列出所有可触发的 queue
    python scripts/trigger_tasks.py --list

注意：
    - Worker 必须在跑（start_worker.py），否则消息会堆在 Redis Stream 里等消费
    - 不传 args/kwargs，所有 task 都是无参的 self-contained
    - 触发是 fire-and-forget，本脚本只发不等结果
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jettask import TaskMessage
from src.interfaces.tasks import app


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
        elif f"agg_{a}" in qset:
            candidate = f"agg_{a}"
        elif f"trade_{a}" in qset:
            candidate = f"trade_{a}"
        else:
            print(f"⚠️  未知 queue: {a}")
            continue
        if candidate not in seen:
            seen.add(candidate)
            wanted.append(candidate)
    return wanted


def main():
    args = sys.argv[1:]
    queues = all_queues()

    if "--list" in args or "-l" in args:
        print(f"可触发的 queue（共 {len(queues)} 个）:")
        for q in queues:
            print(f"  - {q}")
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

    msgs = [TaskMessage(queue=q, kwargs={}) for q in target]
    print(f"🚀 触发 {len(msgs)} 个 task:")
    for q in target:
        print(f"   → {q}")

    ids = app.send_sync(msgs)
    print(f"\n✅ 发送成功，event_ids:")
    for q, eid in zip(target, ids):
        print(f"   {q:<24} {eid}")
    print("\n💡 提示：消息已入 Redis Stream，Worker 会立即消费。")
    print("       看 worker 日志确认执行：tail -f <worker.log>")

    app.close()


if __name__ == "__main__":
    main()
