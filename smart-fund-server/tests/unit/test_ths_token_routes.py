"""/api/ths/token 路由单元测试 — 认证门控 + 请求校验（服务层打桩，不连 DB）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.infrastructure.config import settings
from src.interfaces.api.routes import ths_token

pytestmark = pytest.mark.unit

API_KEY = "test-api-key"


@pytest.fixture
def client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(ths_token.router)
    monkeypatch.setattr(settings, "THS_TOKEN_REPORT_API_KEY", API_KEY)
    return TestClient(app)


def stub_service(monkeypatch, **overrides):
    service = SimpleNamespace(**overrides)
    monkeypatch.setattr(ths_token, "_service", service)
    return service


# ----------------------------------------------------------------------
# 认证门控
# ----------------------------------------------------------------------


def test_report_disabled_when_api_key_not_configured(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(ths_token.router)
    monkeypatch.setattr(settings, "THS_TOKEN_REPORT_API_KEY", "")
    with TestClient(app) as c:
        resp = c.post(
            "/api/ths/token",
            json={"token": "t", "time": "123"},
            headers={"X-Api-Key": "anything"},
        )
        assert resp.status_code == 503
        resp = c.get("/api/ths/token/latest", headers={"X-Api-Key": "anything"})
        assert resp.status_code == 503


def test_report_rejects_wrong_api_key(client: TestClient) -> None:
    resp = client.post(
        "/api/ths/token",
        json={"token": "t", "time": "123"},
        headers={"X-Api-Key": "wrong"},
    )
    assert resp.status_code == 401
    resp = client.get("/api/ths/token/latest", headers={"X-Api-Key": "wrong"})
    assert resp.status_code == 401
    resp = client.post("/api/ths/token", json={"token": "t", "time": "123"})
    assert resp.status_code == 401


# ----------------------------------------------------------------------
# POST /api/ths/token
# ----------------------------------------------------------------------


def test_report_stores_payload(client: TestClient, monkeypatch) -> None:
    captured: dict = {}

    def store_report(payload: dict) -> dict:
        captured.update(payload)
        return {"stored": True, "id": 7, "expire_at": "2026-08-18T00:00:00+00:00"}

    stub_service(monkeypatch, store_report=store_report)
    resp = client.post(
        "/api/ths/token",
        json={
            "token": "secret-token",
            "time": "1765430400",
            "user_id": "690359103",
            "device_id": "oneplus",
            "livetime": 1440,
            "qsid": "10",
            "accountNatureType": 0,
            "source": "hook_capture",
        },
        headers={"X-Api-Key": API_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["stored"] is True
    assert "token" not in body  # 不回显 token
    assert captured["token"] == "secret-token"
    assert captured["accountNatureType"] == 0


def test_report_validation_error(client: TestClient, monkeypatch) -> None:
    stub_service(monkeypatch, store_report=lambda payload: {"stored": True})
    resp = client.post(
        "/api/ths/token", json={"time": "123"}, headers={"X-Api-Key": API_KEY}
    )
    assert resp.status_code == 422


def test_report_value_error_maps_to_422(client: TestClient, monkeypatch) -> None:
    def store_report(payload: dict) -> dict:
        raise ValueError("token and time are required")

    stub_service(monkeypatch, store_report=store_report)
    resp = client.post(
        "/api/ths/token",
        json={"token": "t", "time": "123"},
        headers={"X-Api-Key": API_KEY},
    )
    assert resp.status_code == 422
    assert "required" in resp.json()["detail"]


# ----------------------------------------------------------------------
# GET /api/ths/token/latest
# ----------------------------------------------------------------------


def test_latest_returns_token_for_ops(client: TestClient, monkeypatch) -> None:
    now = datetime.now(tz=UTC)
    report = SimpleNamespace(
        token="secret-token",
        token_time="1765430400",
        expire_at=now + timedelta(hours=10),
        user_id="690359103",
        device_id="oneplus",
        source="hook_capture",
        reported_at=now,
    )
    stub_service(monkeypatch, latest_valid_token=lambda: report)
    resp = client.get("/api/ths/token/latest", headers={"X-Api-Key": API_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["token"] == "secret-token"
    assert body["user_id"] == "690359103"


def test_latest_no_valid_token(client: TestClient, monkeypatch) -> None:
    stub_service(monkeypatch, latest_valid_token=lambda: None)
    resp = client.get("/api/ths/token/latest", headers={"X-Api-Key": API_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["reason"] == "no valid token in store"
