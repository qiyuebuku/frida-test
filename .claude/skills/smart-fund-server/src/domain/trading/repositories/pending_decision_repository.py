"""待执行决策仓储抽象接口"""
from abc import ABC, abstractmethod


class PendingDecisionRepository(ABC):
    """ft_pending_decisions 仓储"""

    @abstractmethod
    def insert_event_driven(self, decision: dict) -> bool:
        """插入 event_driven 决策(幂等键 event_stream_id+fund_code+date)

        decision 字段(必填):
            fund_code, fund_name, action, amount, sell_pct, reason,
            confidence, market_view, risk_notes,
            event_stream_id, source_event_ids (list[int]),
            score, score_breakdown (dict), dry_run

        Returns: True 实际入库, False 冲突跳过 / 写入失败
        """
        ...

    @abstractmethod
    def insert_monitor_decision(self, decision: dict) -> bool:
        """插入 monitor 触发的决策(event_stream_id=NULL,幂等按 fund_code+action+date)

        Returns: True 实际入库
        """
        ...

    @abstractmethod
    def find_pending_event_driven(self, limit: int = 20) -> list[dict]:
        """读今日 event_driven 来源 + status=pending + action in (buy,sell) 的决策"""
        ...

    @abstractmethod
    def mark_executed(self, decision_id: int) -> None:
        ...

    @abstractmethod
    def mark_cancelled(self, decision_id: int, reason: str) -> None:
        ...
