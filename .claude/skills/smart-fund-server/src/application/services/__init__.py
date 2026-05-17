"""应用层 services — 用例 (use case) 入口

每个 use case = 一个公共方法,接收 dto/参数,调 domain services + repositories,
返回 dto。task 层只调这里,不直接 import domain。

4 个 app service 对应 4 个聚合根:
- CollectionAppService: 5 个数据采集 use case
- ExtractionAppService: 3 个事件抽取 use case
- TradingAppService: 3 个交易决策 use case
- ReflectionAppService: 1 个复盘 use case
"""

from __future__ import annotations

from typing import Any


__all__ = [
    "CollectionAppService",
    "ExtractionAppService",
    "TradingAppService",
    "ReflectionAppService",
]


def __getattr__(name: str) -> Any:
    """Lazy-load app services to avoid importing unrelated infrastructure deps.

    Importing a submodule such as ``src.application.services.knowledge_service`` must
    not require optional dependencies from collection/trading services.
    """

    if name == "CollectionAppService":
        from src.application.services.collection_app_service import CollectionAppService

        return CollectionAppService
    if name == "ExtractionAppService":
        from src.application.services.extraction_app_service import ExtractionAppService

        return ExtractionAppService
    if name == "TradingAppService":
        from src.application.services.trading_app_service import TradingAppService

        return TradingAppService
    if name == "ReflectionAppService":
        from src.application.services.reflection_app_service import ReflectionAppService

        return ReflectionAppService
    raise AttributeError(name)
