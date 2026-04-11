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
        "agg_news", "agg_fund_flow", "agg_macro",
        "agg_sentiment", "agg_market", "agg_event_feedback",
    ]

    print(f"🚀 启动 Worker（并发={args.concurrency}）")
    print(f"   任务: {', '.join(task_names)}")
    app.start_worker(task_names=task_names, concurrency=args.concurrency)


if __name__ == "__main__":
    main()
