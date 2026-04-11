"""交易决策 use case 的 DTO"""
from dataclasses import dataclass, field


@dataclass
class DecisionResult:
    """trade_decision use case 输出"""
    streams: int
    decisions: int
    skipped: int = 0

    def to_dict(self) -> dict:
        return {
            "streams": self.streams,
            "decisions": self.decisions,
            "skipped": self.skipped,
        }


@dataclass
class ExecutionResult:
    """trade_execution use case 输出"""
    total: int
    executed: int
    dry_run: int
    live: int
    failed: int

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "executed": self.executed,
            "dry_run": self.dry_run,
            "live": self.live,
            "failed": self.failed,
        }


@dataclass
class MonitorResult:
    """trade_monitor use case 输出"""
    positions: int
    stop_loss: int = 0
    decay_sell: int = 0
    add_position: int = 0

    def to_dict(self) -> dict:
        return {
            "positions": self.positions,
            "stop_loss": self.stop_loss,
            "decay_sell": self.decay_sell,
            "add_position": self.add_position,
        }
