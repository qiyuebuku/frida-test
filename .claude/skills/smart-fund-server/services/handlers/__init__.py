"""Handler 注册表：(skill_name, command_id) → handler 函数"""

from typing import Callable

_registry: dict[tuple[str, str], Callable] = {}


def register(skill_name: str, command_id: str):
    """注册 handler，支持一个函数注册多个命令"""
    def decorator(fn):
        _registry[(skill_name, command_id)] = fn
        return fn
    return decorator


def get_handler(skill_name: str, command_id: str):
    """精确匹配，找不到返回 None"""
    return _registry.get((skill_name, command_id))


# 导入所有 handler 模块以触发 @register 注册
from services.handlers import ocr  # noqa: F401, E402
from services.handlers import chat_reply  # noqa: F401, E402
from services.handlers import fund_holdings  # noqa: F401, E402
