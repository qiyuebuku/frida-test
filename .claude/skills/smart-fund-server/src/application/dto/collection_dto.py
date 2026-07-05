"""数据采集 use case 的 DTO"""
from dataclasses import dataclass


@dataclass
class CollectionResult:
    """数据采集 use case 输出

    所有 5 个采集 use case 共用,通过 aggregator 字段区分:
    news / fund_flow / market / sentiment / macro
    """
    aggregator: str
    sources_run: int
    total_saved: int
    new_ids: list[int] | None = None

    def to_dict(self) -> dict:
        data = {
            "aggregator": self.aggregator,
            "sources_run": self.sources_run,
            "total_saved": self.total_saved,
        }
        if self.new_ids is not None:
            data["new_ids"] = self.new_ids
        return data
