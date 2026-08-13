from src.infrastructure.clients.base import BaseClient
from src.infrastructure.clients.ths import THSClient
from src.infrastructure.clients.eastmoney import EastmoneyClient
from src.infrastructure.clients.tiantianjijin import TianTianClient
from src.infrastructure.clients.sina import SinaClient
from src.infrastructure.clients.tencent import TencentClient
from src.infrastructure.clients.pboc import PBOCClient
from src.infrastructure.clients.aggregator import AggregatorClient
from src.infrastructure.clients.cls import CLSClient
from src.infrastructure.clients.gov import GovClient
from src.infrastructure.clients.xueqiu import XueqiuClient
from src.infrastructure.clients.market_calendar import MarketCalendarClient
from src.infrastructure.clients.exchange_fund import ExchangeFundClient
from src.infrastructure.clients.market_valuation import MarketValuationClient
from src.infrastructure.clients.chinabond import ChinaBondClient

__all__ = [
    "BaseClient",
    "THSClient",
    "EastmoneyClient",
    "TianTianClient",
    "SinaClient",
    "TencentClient",
    "PBOCClient",
    "AggregatorClient",
    "CLSClient",
    "GovClient",
    "XueqiuClient",
    "MarketCalendarClient",
    "ExchangeFundClient",
    "MarketValuationClient",
    "ChinaBondClient",
    "init_clients",
    "close_clients",
    "ths", "eastmoney", "trade", "sina", "tencent", "pboc",
    "aggregator", "cls", "gov", "xueqiu", "market_calendar", "exchange_fund",
    "market_valuation", "chinabond",
]

# ==================== 客户端实例管理 ====================

ths: THSClient | None = None
eastmoney: EastmoneyClient | None = None
trade: TianTianClient | None = None
sina: SinaClient | None = None
tencent: TencentClient | None = None
pboc: PBOCClient | None = None
aggregator: AggregatorClient | None = None
cls: CLSClient | None = None
gov: GovClient | None = None
xueqiu: XueqiuClient | None = None
market_calendar: MarketCalendarClient = MarketCalendarClient()
exchange_fund: ExchangeFundClient | None = None
market_valuation: MarketValuationClient | None = None
chinabond: ChinaBondClient | None = None


def init_clients(timeout: float = 10.0):
    """初始化所有客户端实例"""
    global ths, eastmoney, trade, sina, tencent, pboc, aggregator, cls, gov, xueqiu
    global exchange_fund, market_valuation, chinabond
    ths = THSClient(timeout=timeout)
    eastmoney = EastmoneyClient(timeout=timeout)
    trade = TianTianClient(timeout=timeout)
    sina = SinaClient(timeout=timeout)
    tencent = TencentClient(timeout=timeout)
    pboc = PBOCClient(timeout=timeout)
    cls = CLSClient(timeout=timeout)
    gov = GovClient(timeout=15.0)
    xueqiu = XueqiuClient(timeout=15.0)
    exchange_fund = ExchangeFundClient(timeout=timeout)
    market_valuation = MarketValuationClient(timeout=max(timeout, 30.0))
    chinabond = ChinaBondClient(timeout=max(timeout, 30.0))
    aggregator = AggregatorClient(ths=ths, eastmoney=eastmoney, sina=sina, tencent=tencent)


async def close_clients():
    """关闭所有客户端"""
    for c in [
        ths,
        eastmoney,
        trade,
        sina,
        tencent,
        pboc,
        cls,
        gov,
        xueqiu,
        exchange_fund,
        market_valuation,
        chinabond,
    ]:
        if c:
            await c.close()
