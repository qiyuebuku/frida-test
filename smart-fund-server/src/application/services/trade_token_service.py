"""同花顺交易 token 上报存储与设备登录自愈。

链路（2026-08-17 token 跨设备共享）：
- 真机探针在 token 刷新（z7m.w 捕获）或主动登录成功（export 解密）后，
  自动 POST 上报到 /api/ths/token，本服务入库 ft_ths_tokens。
- 设备端登录失败且属 token 类错误（过期/未存储）时，ensure_device_logged_in
  从库中取最新有效 token，经设备端 import 端点注入并重新登录（官方入口
  z7m.o 用设备本机密钥重加密，无需迁移加密材料）。

敏感约束：token 是券商交易登录凭证的明文，仅限受控通道传输（公网必须
HTTPS + X-Api-Key），禁止写入日志或提交到代码库。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.infrastructure.clients.ths_trade import (
    THSTradeClient,
    THSTradeError,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.trading import ThsTokenReport

logger = logging.getLogger(__name__)

# 视为"token 类"失败、可尝试用库中 token 自愈的原因码
_TOKEN_ERROR_CODES = {
    "trade_token_unavailable",
    "trade_account_not_logged_in",
}


def _expire_at(token_time: str, livetime_min: int | None) -> datetime | None:
    if not token_time or livetime_min is None:
        return None
    try:
        base = datetime.fromtimestamp(int(token_time), tz=UTC)
    except (ValueError, OverflowError):
        return None
    return base + timedelta(minutes=livetime_min)


class TradeTokenService:
    """token 上报入库 / 最新有效查询 / 设备登录自愈。"""

    def store_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        """入库一条上报（幂等去重：同 user+token 已存在则跳过）。"""
        token = str(payload.get("token") or "").strip()
        token_time = str(payload.get("time") or payload.get("token_time") or "").strip()
        if not token or not token_time:
            raise ValueError("token and time are required")
        user_id = str(payload.get("user_id") or "").strip() or "unknown"
        device_id = str(payload.get("device_id") or "unknown").strip()
        livetime_min = payload.get("livetime")
        livetime_min = int(livetime_min) if livetime_min is not None else None
        expire = _expire_at(token_time, livetime_min)
        with get_session() as session:
            exists = session.scalar(
                select(ThsTokenReport.id).where(
                    ThsTokenReport.user_id == user_id,
                    ThsTokenReport.token == token,
                )
            )
            if exists is not None:
                return {"stored": False, "duplicate": True, "id": exists}
            row = ThsTokenReport(
                user_id=user_id,
                device_id=device_id,
                token=token,
                token_time=token_time,
                livetime_min=livetime_min,
                expire_at=expire,
                qsid=payload.get("qsid"),
                account=payload.get("account"),
                wtid=payload.get("wtid"),
                accounttype=payload.get("accounttype"),
                account_nature_type=payload.get("accountNatureType"),
                source=payload.get("source"),
            )
            session.add(row)
            session.flush()
            return {"stored": True, "id": row.id, "expire_at": expire}

    def latest_valid_token(
        self, *, now: datetime | None = None
    ) -> ThsTokenReport | None:
        """最新一条有效上报（expire_at 为空视为未知，保守跳过）。"""
        now = now or datetime.now(tz=UTC)
        with get_session() as session:
            stmt = (
                select(ThsTokenReport)
                .where(ThsTokenReport.expire_at > now)
                .order_by(ThsTokenReport.reported_at.desc(), ThsTokenReport.id.desc())
                .limit(1)
            )
            return session.scalar(stmt)

    def ensure_device_logged_in(
        self, client: THSTradeClient | None = None
    ) -> dict[str, Any]:
        """确保设备端已登录；token 类失败时用库中最新 token 自愈。

        返回 {"logged_in": bool, "via": "already|login|import", ...}；
        自愈仍失败时抛最后一次 THSTradeError（上层降级 unavailable）。
        """
        client = client or THSTradeClient.shared()
        try:
            result = client.login()
            return {
                "logged_in": result.get("ok") is True,
                "via": result.get("result", "login"),
                "detail": result,
            }
        except THSTradeError as exc:
            if exc.reason_code not in _TOKEN_ERROR_CODES:
                raise
            report = self.latest_valid_token()
            if report is None:
                raise
            logger.info(
                "trade token self-heal: import token reported_at=%s source=%s",
                report.reported_at,
                report.source,
            )
            result = client.import_token(token=report.token, time=report.token_time)
            ok = result.get("ok") is True
            return {
                "logged_in": ok,
                "via": "import",
                "detail": result,
            }
