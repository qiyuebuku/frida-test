"""共享工具函数和全局依赖"""

from fastapi import HTTPException

from services.ths_fund_client import THSFundClient

client: THSFundClient = None


def set_client(c: THSFundClient):
    global client
    client = c


async def safe_call(coro):
    try:
        return await coro
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"上游请求失败: {e}")
