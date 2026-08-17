"""THS token 上报路由：真机探针自动上报 + 最新有效查询。

安全：X-Api-Key 认证（settings.THS_TOKEN_REPORT_API_KEY，留空拒绝全部）。
token 为敏感登录凭证，公网部署必须 HTTPS 且配置强随机 key。
"""

import logging

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from src.application.services.trade_token_service import TradeTokenService
from src.infrastructure.config import settings

router = APIRouter(prefix="/api/ths", tags=["ths-token"])
logger = logging.getLogger("ths-token")

_service = TradeTokenService()


class ThsTokenReportRequest(BaseModel):
    token: str = Field(min_length=1)
    time: str = Field(min_length=1)
    user_id: str = ""
    device_id: str = ""
    livetime: int | None = None
    qsid: str | None = None
    account: str | None = None
    wtid: str | None = None
    accounttype: int | None = None
    accountNatureType: int | None = None
    source: str | None = None


def _require_key(x_api_key: str | None) -> None:
    expected = settings.THS_TOKEN_REPORT_API_KEY
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="token report endpoint disabled: THS_TOKEN_REPORT_API_KEY not configured",
        )
    if x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid api key")


@router.post("/token")
def report_token(
    payload: ThsTokenReportRequest,
    x_api_key: str | None = Header(default=None),
) -> dict:
    """接收设备端上报的交易 token（幂等去重，入 ft_ths_tokens）。"""
    _require_key(x_api_key)
    try:
        result = _service.store_report(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 不回显 token；只记录长度级信息
    logger.info(
        "ths token reported: user=%s device=%s stored=%s",
        payload.user_id or "unknown",
        payload.device_id or "unknown",
        result.get("stored"),
    )
    return {"ok": True, **result}


@router.get("/token/latest")
def latest_token(x_api_key: str | None = Header(default=None)) -> dict:
    """最新有效 token（内部/运维用，返回含敏感明文）。"""
    _require_key(x_api_key)
    report = _service.latest_valid_token()
    if report is None:
        return {"ok": False, "reason": "no valid token in store"}
    return {
        "ok": True,
        "token": report.token,
        "time": report.token_time,
        "expire_at": report.expire_at.isoformat() if report.expire_at else None,
        "user_id": report.user_id,
        "device_id": report.device_id,
        "source": report.source,
        "reported_at": (
            report.reported_at.isoformat() if report.reported_at else None
        ),
    }
