"""共享工具函数和全局依赖

客户端实例统一由 src.infrastructure.clients 管理，
此模块仅做 re-export 供 routes 使用。
"""

from fastapi import HTTPException

# 客户端实例和管理函数从 infrastructure 层导入
from src.infrastructure.clients import (  # noqa: F401
    init_clients, close_clients,
    ths, eastmoney, trade, sina, tencent, pboc,
    aggregator, cls, gov, xueqiu,
)


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上游请求失败: {e}")
