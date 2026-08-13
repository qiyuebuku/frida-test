"""共享工具函数和全局依赖。

客户端由 ``src.infrastructure.clients`` 在 FastAPI lifespan 中初始化。
这里必须动态读取客户端属性，不能在模块导入时复制尚未初始化的
``None`` 引用。
"""

from fastapi import HTTPException

from src.infrastructure import clients as _clients


init_clients = _clients.init_clients
close_clients = _clients.close_clients


def __getattr__(name: str):
    if name in _clients.__all__:
        return getattr(_clients, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上游请求失败: {e}")
