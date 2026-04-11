"""统一的连接管理（database / redis）

设计原则:
- 单一进程内 engine/client 是单例
- 提供上下文管理器接口,自动 close
- 支持多目标(prod/test)切换
- 不提供事务管理 — 那是 repository 的事
"""

from src.infrastructure.connections.database import (
    get_engine, get_session, get_session_factory, set_target,
)

__all__ = [
    "get_engine",
    "get_session",
    "get_session_factory",
    "set_target",
]
