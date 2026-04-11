"""启动 jettask Worker — 消费聚合任务队列

运行：
    python src/interfaces/cli/start_worker.py
    python src/interfaces/cli/start_worker.py -c 2    # 指定并发数
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from src.interfaces.tasks import app


def main():
    parser = argparse.ArgumentParser(description="启动聚合任务 Worker")
    parser.add_argument("-c", "--concurrency", type=int, default=1, help="并发数（默认 1）")
    args = parser.parse_args()

    task_names = [
        # P0 数据采集
        "agg_news", "agg_fund_flow",
        # P1 辅助数据
        "agg_macro", "agg_sentiment", "agg_market",
        # AI 处理
        "agg_event_extraction", "agg_event_stream",
        # 决策与交易
        "trade_decision", "trade_execution", "trade_monitor",
        # 复盘与反馈
        "agg_event_feedback", "review_decision",
    ]

    print(f"🚀 启动 Worker（并发={args.concurrency}）")
    print(f"   任务: {', '.join(task_names)}")
    # jettask 1.0.19: app.start(tasks=[...], concurrency=N)
    app.start(tasks=task_names, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
