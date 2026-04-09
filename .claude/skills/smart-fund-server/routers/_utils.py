"""共享工具函数和全局依赖"""

from fastapi import HTTPException

from services.clients import (
    THSClient,
    EastmoneyClient,
    TianTianClient,
    SinaClient,
    TencentClient,
    PBOCClient,
    AggregatorClient,
    CLSClient,
    GovClient,
    XueqiuClient,
)

# 各数据源独立客户端
ths: THSClient = None
eastmoney: EastmoneyClient = None
trade: TianTianClient = None
sina: SinaClient = None
tencent: TencentClient = None
pboc: PBOCClient = None
aggregator: AggregatorClient = None
cls: CLSClient = None
gov: GovClient = None
xueqiu: XueqiuClient = None


def init_clients(timeout: float = 10.0):
    """初始化所有客户端"""
    global ths, eastmoney, trade, sina, tencent, pboc, aggregator, cls, gov, xueqiu
    ths = THSClient(timeout=timeout)
    eastmoney = EastmoneyClient(timeout=timeout)
    trade = TianTianClient(timeout=timeout)
    sina = SinaClient(timeout=timeout)
    tencent = TencentClient(timeout=timeout)
    pboc = PBOCClient(timeout=timeout)
    cls = CLSClient(timeout=timeout)
    gov = GovClient(timeout=15.0)
    xueqiu = XueqiuClient(timeout=15.0)
    aggregator = AggregatorClient(ths=ths, eastmoney=eastmoney, sina=sina, tencent=tencent)


async def close_clients():
    """关闭所有客户端"""
    for c in [ths, eastmoney, trade, sina, tencent, pboc, cls, gov, xueqiu]:
        if c:
            await c.close()


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上游请求失败: {e}")
