"""复盘 use case 的 DTO"""
from dataclasses import dataclass, field


@dataclass
class ReviewResult:
    """review_decision use case 输出"""
    trades: int = 0
    reviewed: int = 0
    skipped: int = 0
    winrate_stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trades": self.trades,
            "reviewed": self.reviewed,
            "skipped": self.skipped,
            "winrate_stats": self.winrate_stats,
        }
