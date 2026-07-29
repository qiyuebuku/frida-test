"""资金流仓储抽象接口"""
from abc import ABC, abstractmethod
from datetime import date


class MarketFlowRepository(ABC):
    """资金流仓储 (data_type: northbound/sector_flow/stock_flow/dragon_tiger)"""

    @abstractmethod
    def upsert_batch(self, items: list[dict]) -> int:
        """批量插入资金流数据

        items 每条 = {data_type, trade_date, data: dict}
        4 个 partial unique index 自动按 data_type 去重:
        - stock_flow: (data->>code, trade_date)
        - sector_flow: (data->>name, trade_date)
        - northbound: (data->>mutual_type, trade_date)
        - dragon_tiger: (data->>source, data->>code, trade_date)
        """
        ...

    @abstractmethod
    def max_trade_date(self, data_type: str) -> date | None:
        """取某 data_type 的最大 trade_date(checkpoint 用)"""
        ...

    @abstractmethod
    def count_by_type(self) -> dict[str, int]:
        """按 data_type 分组的行数(监控用)"""
        ...

    @abstractmethod
    def get_northbound_net_flow(self, trade_date: str) -> float | None:
        """指定日期的北向净流入(从 northbound 类型最新一行的 data.net_flow 取)"""
        ...

    @abstractmethod
    def get_sector_net_inflow(self, sector_name: str, trade_date: str) -> float | None:
        """指定日期某板块的资金净流入(从 sector_flow 类型 data.net_amount 取)"""
        ...
