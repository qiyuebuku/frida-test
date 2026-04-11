"""启动 jettask Persist — 消费 _commands 队列，将调度命令落库到 PG

jettask-rs 架构里有 3 个进程：
    - Persist：消费 schedule_register/update/delete 命令，写 PG
    - Scheduler：按时间触发调度，发任务消息到业务队列
    - Worker：消费业务队列执行 task

运行：
    python src/interfaces/cli/start_persist.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from src.interfaces.tasks import app, DB_URL


def main():
    print("📦 启动 Persist（消费 _commands 队列，落库 PG）")
    print(f"   db_url: {DB_URL}")
    # jettask-rs: start_persist 是同步阻塞方法
    app.start_persist(db_url=DB_URL)


if __name__ == "__main__":
    main()
