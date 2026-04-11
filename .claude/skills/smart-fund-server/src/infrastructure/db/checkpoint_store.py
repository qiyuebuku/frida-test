"""数据采集 checkpoint 统一存储 — thin wrapper

【R2.10 重构】本模块原本直接 psycopg2 + raw SQL 维护 ft_collection_state 表。
现在改为 thin wrapper, 委托给 CollectionStateRepositoryImpl。

保留模块级函数式 API 是为了向后兼容(base.py 等多处已经在 import 这些函数)。
新代码请直接用 CollectionStateRepository。

调用链:
    checkpoint_store.get(...) → CollectionStateRepositoryImpl.get(...)
                              → SQLAlchemy session → PG ft_collection_state
"""
import logging
from typing import Optional

from src.infrastructure.persistence.repositories.collection_state_repository_impl import (
    CollectionStateRepositoryImpl,
)

logger = logging.getLogger(__name__)


# 单例 repository (避免每次 new)
_repo: Optional[CollectionStateRepositoryImpl] = None


def _get_repo() -> CollectionStateRepositoryImpl:
    global _repo
    if _repo is None:
        _repo = CollectionStateRepositoryImpl()
    return _repo


def init_table() -> None:
    """[已废弃] 表结构由 schema/01_collection.sql 维护

    保留空函数避免破坏 base.py 等模块的 import。
    """
    pass


def get(aggregator: str, source_name: str) -> Optional[dict]:
    return _get_repo().get(aggregator, source_name)


def get_checkpoint(aggregator: str, source_name: str) -> dict:
    return _get_repo().get_checkpoint(aggregator, source_name)


def is_enabled(aggregator: str, source_name: str) -> bool:
    return _get_repo().is_enabled(aggregator, source_name)


def update_success(
    aggregator: str,
    source_name: str,
    new_checkpoint: dict | None = None,
    saved_count: int = 0,
) -> None:
    return _get_repo().update_success(
        aggregator, source_name, new_checkpoint, saved_count,
    )


def update_failure(aggregator: str, source_name: str, error_msg: str) -> None:
    return _get_repo().update_failure(aggregator, source_name, error_msg)


def reset(aggregator: str, source_name: str) -> bool:
    return _get_repo().reset(aggregator, source_name)


def disable(aggregator: str, source_name: str) -> bool:
    return _get_repo().disable(aggregator, source_name)


def enable(aggregator: str, source_name: str) -> bool:
    return _get_repo().enable(aggregator, source_name)


def list_all(aggregator: str | None = None) -> list[dict]:
    return _get_repo().list_all(aggregator)
