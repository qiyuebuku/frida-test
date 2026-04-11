"""应用层 services — 用例 (use case) 入口

每个 use case = 一个公共方法,接收 dto/参数,调 domain services + repositories,
返回 dto。task 层只调这里,不直接 import domain。

4 个 app service 对应 4 个聚合根:
- CollectionAppService: 5 个数据采集 use case
- ExtractionAppService: 3 个事件抽取 use case
- TradingAppService: 3 个交易决策 use case
- ReflectionAppService: 1 个复盘 use case
"""
from src.application.services.collection_app_service import CollectionAppService
from src.application.services.extraction_app_service import ExtractionAppService
from src.application.services.reflection_app_service import ReflectionAppService
from src.application.services.trading_app_service import TradingAppService

__all__ = [
    "CollectionAppService",
    "ExtractionAppService",
    "TradingAppService",
    "ReflectionAppService",
]
