"""应用层 DTO — 跨层数据传输对象

只用 dataclass,不带任何业务逻辑。
"""
from src.application.dto.collection_dto import (
    CollectionResult,
)
__all__ = [
    "CollectionResult",
]
