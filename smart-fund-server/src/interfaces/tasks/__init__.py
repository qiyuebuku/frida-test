"""分布式任务（基于 jettask-rs / jettask-python）

启动 Worker：
    python -m src.interfaces.cli worker -c 1

注册定时调度：
    python -m src.interfaces.cli init schedules
"""

import logging

from jettask import Jettask
from src.infrastructure.config.settings import REDIS_URL, JETTASK_PREFIX, PG_URL
from src.infrastructure.db import raw_data

logger = logging.getLogger(__name__)

# jettask-rs 的 Python 绑定入口：Jettask(redis_url=, prefix=)
# pg_url 在 start_scheduler / start_persist 时单独传入
app = Jettask(redis_url=REDIS_URL, prefix=JETTASK_PREFIX)
DB_URL = PG_URL

# Worker 进程不经过 FastAPI 生命周期；采集任务启动前必须确保 raw_data 分区存在。
try:
    raw_data.init_raw_data_tables()
except Exception as exc:
    logger.warning("ft_raw_data 初始化失败，worker 继续启动；采集写入时会再次检查分区: %s", exc)

# 注册采集任务路由
from src.interfaces.tasks.collection_tasks import router as collection_router
app.include_router(collection_router)

# 注册高维知识索引任务路由
from src.interfaces.tasks.knowledge_tasks import router as knowledge_router
app.include_router(knowledge_router)

# 注册 Research Agent 独立调度任务
from src.interfaces.tasks.research_agent_tasks import router as research_agent_router
app.include_router(research_agent_router)
