"""定时任务调度器 - 自动创建周期性 AI 分析任务"""

import logging
import threading
import time
from datetime import datetime, timedelta

from services.db import task_db
from services.task_executor import executor

logger = logging.getLogger(__name__)

# 定时任务配置
SCHEDULED_TASKS = [
    {
        "task_type": "fund_trade_run",
        "name": "每日交易决策",
        "cron_hour": 9,
        "cron_minute": 30,
        "weekdays": [0, 1, 2, 3, 4],  # 周一到周五
    },
    {
        "task_type": "fund_review",
        "name": "每周持仓审视",
        "cron_hour": 15,
        "cron_minute": 30,
        "weekdays": [4],  # 仅周五
    },
]


class Scheduler:
    def __init__(self):
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Scheduler started")

    def stop(self):
        self._running = False

    def _loop(self):
        last_triggered = {}  # task_type -> date string

        while self._running:
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")

            for task_cfg in SCHEDULED_TASKS:
                task_type = task_cfg["task_type"]
                key = f"{task_type}_{today_str}"

                # 已经触发过今天的任务
                if key in last_triggered:
                    continue

                # 检查星期几
                if now.weekday() not in task_cfg["weekdays"]:
                    continue

                # 检查时间是否到达
                target_hour = task_cfg["cron_hour"]
                target_minute = task_cfg["cron_minute"]

                if now.hour == target_hour and now.minute >= target_minute:
                    logger.info(f"Scheduler: triggering {task_type}")
                    try:
                        task_id = task_db.create_task(
                            task_type=task_type,
                            input_type="scheduled",
                            client_id="scheduler",
                            title=f"{task_cfg['name']} {now.strftime('%m-%d %H:%M')}"
                        )
                        executor.submit(task_id)
                        last_triggered[key] = True
                        logger.info(f"Scheduler: created task #{task_id} ({task_type})")
                    except Exception as e:
                        logger.exception(f"Scheduler: failed to create {task_type}: {e}")

            # 每 30 秒检查一次
            time.sleep(30)


# 全局单例
scheduler = Scheduler()
