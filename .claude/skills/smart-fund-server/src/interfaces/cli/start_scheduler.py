"""启动 jettask Scheduler — 按 Schedule 配置定时发消息到队列

运行：
    python src/interfaces/cli/start_scheduler.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.interfaces.tasks import app, DB_URL


def main():
    print("🕐 启动 Scheduler（按 Schedule 定时发消息到队列）")
    print(f"   db_url: {DB_URL}")
    app.start_scheduler(db_url=DB_URL)


if __name__ == "__main__":
    main()
