"""TradeTokenService 集成测试 — 真实测试库 jettask_test（PG）。

覆盖：store_report 入库/幂等去重、latest_valid_token 有效期过滤与排序、
ensure_device_logged_in 自愈（login 直成 / token 类失败走 import / 无 token
抛错 / 非 token 错误透传）、投影服务 token 自愈重试。
设备端交互用 httpx.MockTransport（不触网），DB 走 set_target("test")。
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

# 必须在首次 get_engine("test") 前设置：connections._build_url 按 target 读
os.environ.setdefault("TEST_DB_NAME", "jettask_test")

import httpx
import pytest
from sqlalchemy import delete

from src.application.services.trade_account_projection_service import (
    TradeAccountProjectionService,
)
from src.application.services.trade_token_service import TradeTokenService
from src.infrastructure.clients.ths_trade import THSTradeClient, THSTradeError
from src.infrastructure.connections import get_session, set_target
from src.infrastructure.persistence.models.trading import ThsTokenReport

pytestmark = pytest.mark.integration

CUTOFF = datetime(2026, 8, 17, tzinfo=UTC)
USER = "test_user_token_service"


def make_client(handler, **kwargs) -> THSTradeClient:
    client = THSTradeClient(base_url="http://device.test", **kwargs)
    client._client = httpx.Client(
        transport=httpx.MockTransport(handler), timeout=client._timeout
    )
    return client


FUNDS_PAYLOAD = {
    "query": "proto_1807",
    "ok": True,
    "elapsed_ms": 100,
    "data": {"fields": {"total_assets": "3139.80", "available_amount": "3139.80"}},
}
POSITIONS_PAYLOAD = {
    "query": "proto_1891",
    "ok": True,
    "elapsed_ms": 100,
    "data": {"columns": ["名称", "代码"], "rows": []},
}


def token_error_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={"query": "proto_1891", "ok": False, "error": "trade token unavailable or expired"},
    )


@pytest.fixture(autouse=True)
def test_db():
    set_target("test")
    with get_session() as session:
        session.execute(delete(ThsTokenReport).where(ThsTokenReport.user_id == USER))
    yield
    with get_session() as session:
        session.execute(delete(ThsTokenReport).where(ThsTokenReport.user_id == USER))


def seed_token_report(
    *,
    token: str,
    reported_at: datetime | None = None,
    expire_at: datetime | None = None,
    token_time: str | None = None,
    livetime_min: int | None = 1440,
) -> None:
    if token_time is None:
        base = datetime.now(tz=UTC)
        token_time = str(int(base.timestamp()))
    with get_session() as session:
        session.add(
            ThsTokenReport(
                user_id=USER,
                device_id="test-device",
                token=token,
                token_time=token_time,
                livetime_min=livetime_min,
                expire_at=expire_at,
                source="test",
                reported_at=reported_at or datetime.now(tz=UTC),
            )
        )


# ----------------------------------------------------------------------
# _expire_at / store_report / latest_valid_token
# ----------------------------------------------------------------------


def test_expire_at_computed_from_token_time_and_livetime() -> None:
    from src.application.services.trade_token_service import _expire_at

    expire = _expire_at("1765430400", 1440)
    assert expire == datetime.fromtimestamp(1765430400, tz=UTC) + timedelta(minutes=1440)
    assert _expire_at("", 1440) is None
    assert _expire_at("1765430400", None) is None
    assert _expire_at("not-a-ts", 1440) is None


def test_store_report_insert_and_idempotent_dedupe() -> None:
    service = TradeTokenService()
    payload = {
        "token": "tok-abc",
        "time": "1765430400",
        "user_id": USER,
        "device_id": "oneplus",
        "livetime": 1440,
        "qsid": "10",
        "source": "hook_capture",
    }
    first = service.store_report(payload)
    assert first["stored"] is True
    assert first["expire_at"] is not None

    second = service.store_report(dict(payload))
    assert second["stored"] is False
    assert second["duplicate"] is True
    assert second["id"] == first["id"]

    # 同 user 不同 token 仍入库
    third = service.store_report({**payload, "token": "tok-def"})
    assert third["stored"] is True
    assert third["id"] != first["id"]


def test_store_report_rejects_missing_fields() -> None:
    service = TradeTokenService()
    with pytest.raises(ValueError):
        service.store_report({"token": "", "time": "123"})
    with pytest.raises(ValueError):
        service.store_report({"token": "t", "time": ""})


def test_latest_valid_token_filters_expired_and_prefers_newest() -> None:
    service = TradeTokenService()
    now = datetime.now(tz=UTC)
    # 过期记录（expire 在过去）
    seed_token_report(
        token="tok-expired",
        reported_at=now - timedelta(hours=2),
        expire_at=now - timedelta(hours=1),
    )
    # 无 expire_at（未知，保守跳过）
    seed_token_report(token="tok-unknown", reported_at=now - timedelta(hours=1), expire_at=None)
    # 有效但较旧
    seed_token_report(
        token="tok-old-valid",
        reported_at=now - timedelta(minutes=30),
        expire_at=now + timedelta(hours=12),
    )
    # 有效且最新
    seed_token_report(
        token="tok-new-valid",
        reported_at=now - timedelta(minutes=5),
        expire_at=now + timedelta(hours=20),
    )

    report = service.latest_valid_token()
    assert report is not None
    assert report.token == "tok-new-valid"

    assert service.latest_valid_token(now=now + timedelta(hours=25)) is None


# ----------------------------------------------------------------------
# ensure_device_logged_in 自愈
# ----------------------------------------------------------------------


def test_ensure_device_logged_in_already_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/stock/trade/login"
        return httpx.Response(200, json={"ok": True, "result": "already_logged_in"})

    service = TradeTokenService()
    result = service.ensure_device_logged_in(make_client(handler))
    assert result["logged_in"] is True
    assert result["via"] == "already_logged_in"


def test_ensure_device_logged_in_heals_via_import() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(f"{request.method} {path}")
        if path == "/stock/trade/login":
            return httpx.Response(
                200, json={"ok": False, "error": "trade token unavailable or expired"}
            )
        if path == "/stock/trade/token/import":
            body = httpx.Response(200, json={"ok": True, "result": "success"})
            return body
        raise AssertionError(f"unexpected request {path}")

    seed_token_report(token="tok-heal", expire_at=datetime.now(tz=UTC) + timedelta(hours=10))
    service = TradeTokenService()
    result = service.ensure_device_logged_in(make_client(handler))
    assert result["logged_in"] is True
    assert result["via"] == "import"
    assert calls[0] == "POST /stock/trade/login"
    assert calls[1] == "POST /stock/trade/token/import"


def test_ensure_device_logged_in_reraises_when_store_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": False, "error": "trade token unavailable or expired"}
        )

    service = TradeTokenService()
    with pytest.raises(THSTradeError) as exc_info:
        service.ensure_device_logged_in(make_client(handler))
    assert exc_info.value.reason_code == "trade_token_unavailable"


def test_ensure_device_logged_in_non_token_error_passthrough() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200, json={"ok": False, "error": "trade runtime classloader not ready"}
        )

    service = TradeTokenService()
    with pytest.raises(THSTradeError) as exc_info:
        service.ensure_device_logged_in(make_client(handler))
    assert exc_info.value.reason_code == "trade_runtime_not_ready"
    assert paths == ["/stock/trade/login"]  # 非 token 错误不触发 import


# ----------------------------------------------------------------------
# 投影服务 token 自愈接线
# ----------------------------------------------------------------------


def test_projection_self_heal_recovers_to_available() -> None:
    state = {"healed": False}
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(f"{request.method} {path}")
        if path == "/stock/trade/query" and not state["healed"]:
            return token_error_response()
        if path == "/stock/trade/login":
            state["healed"] = True
            return httpx.Response(200, json={"ok": True, "result": "success"})
        if path == "/stock/trade/query":
            name = request.url.params.get("name")
            return httpx.Response(
                200, json=FUNDS_PAYLOAD if name == "funds" else POSITIONS_PAYLOAD
            )
        raise AssertionError(f"unexpected {path}")

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.exposure_summary(cutoff_at=CUTOFF, account_ids=())
    assert result["status"] == "available"
    assert result["funds"]["total_assets"] == 3139.80
    # 首查失败 → login 自愈 → 重试 positions+funds
    assert seen[0] == "GET /stock/trade/query"
    assert seen[1] == "POST /stock/trade/login"
    assert seen.count("GET /stock/trade/query") >= 2


def test_projection_unavailable_when_heal_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return token_error_response()

    service = TradeAccountProjectionService(client=make_client(handler))
    result = service.exposure_summary(cutoff_at=CUTOFF, account_ids=())
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "trade_token_unavailable"
