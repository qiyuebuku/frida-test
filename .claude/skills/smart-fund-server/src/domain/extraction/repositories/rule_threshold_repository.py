"""规则阈值仓储抽象接口"""
from abc import ABC, abstractmethod


class RuleThresholdRepository(ABC):
    """L1b 规则阈值仓储 (ft_rule_thresholds 表)"""

    @abstractmethod
    def get_threshold(self, rule_name: str) -> dict | None:
        """获取单条规则的阈值配置

        Returns: {rule_name, percentile_95, percentile_99, sigma_value, threshold_config, ...}
        """
        ...

    @abstractmethod
    def get_thresholds_by_source(self, data_source: str) -> list[dict]:
        """获取某数据源的所有规则阈值"""
        ...

    @abstractmethod
    def upsert_threshold(self, data: dict) -> bool:
        """写入/更新规则阈值 (rule_name 冲突时更新)"""
        ...

    @abstractmethod
    def get_all_stale(self, max_age_hours: int = 25) -> list[dict]:
        """获取所有超过 max_age_hours 未刷新的规则阈值"""
        ...
