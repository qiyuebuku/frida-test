"""采集 checkpoint + 调度元数据仓储抽象接口"""
from abc import ABC, abstractmethod
from typing import Any


class CollectionStateRepository(ABC):
    """采集状态仓储 — 替代旧的 checkpoint_store 函数式 API

    每个 (aggregator, source_name) 一行,记录:
    - checkpoint: JSONB 增量游标
    - last_run_at / last_success_at / last_error / consecutive_failures
    - enabled / interval_override
    - total_runs / total_saved
    """

    @abstractmethod
    def get(self, aggregator: str, source_name: str) -> dict | None:
        """读取完整状态行,返回 dict;不存在返回 None"""
        ...

    @abstractmethod
    def get_checkpoint(self, aggregator: str, source_name: str) -> dict:
        """便捷方法: 只取 checkpoint dict,无记录返回 {}"""
        ...

    @abstractmethod
    def is_enabled(self, aggregator: str, source_name: str) -> bool:
        """检查 source 是否启用(无记录默认 True)"""
        ...

    @abstractmethod
    def update_success(
        self,
        aggregator: str,
        source_name: str,
        new_checkpoint: dict | None,
        saved_count: int,
    ) -> None:
        """记录一次成功运行"""
        ...

    @abstractmethod
    def update_failure(
        self, aggregator: str, source_name: str, error_msg: str,
    ) -> None:
        """记录失败"""
        ...

    @abstractmethod
    def reset(self, aggregator: str, source_name: str) -> bool:
        """重置 checkpoint(下次自动全量重拉)"""
        ...

    @abstractmethod
    def arm_backfill(
        self,
        aggregator: str,
        source_name: str,
        target_time: str,
        cursor: Any = None,
    ) -> bool:
        """保留现有时间边界，将单个 source 安全切换到历史回填模式。"""
        ...

    @abstractmethod
    def disable(self, aggregator: str, source_name: str) -> bool:
        ...

    @abstractmethod
    def enable(self, aggregator: str, source_name: str) -> bool:
        ...

    @abstractmethod
    def list_all(self, aggregator: str | None = None) -> list[dict]:
        """列出所有(或指定 aggregator)的 source 状态"""
        ...
