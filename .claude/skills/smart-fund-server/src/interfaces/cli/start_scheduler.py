"""启动 jettask Scheduler — 按 Schedule 配置定时发消息到队列

运行：
    python src/interfaces/cli/start_scheduler.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.interfaces.tasks import app, DB_URL


async def main():
    print("🕐 启动 Scheduler（按 Schedule 定时发消息到队列）")
    print(f"   db_url: {DB_URL}")
    # jettask 1.0.19: start_scheduler 是 async 长跑方法
    await app.start_scheduler()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
