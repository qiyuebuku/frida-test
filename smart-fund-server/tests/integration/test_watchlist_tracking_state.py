from __future__ import annotations

import pytest
from sqlalchemy import delete

from src.application.services.watchlist_service import WatchlistService
from src.infrastructure.connections import get_session, set_target
from src.infrastructure.persistence.models.collection import CollectionState


TEST_CODE = "sh699999"


@pytest.fixture(autouse=True)
def use_test_db():
    set_target("test")
    _delete_test_state()
    yield
    _delete_test_state()
    set_target("prod")


def _delete_test_state() -> None:
    with get_session() as session:
        session.execute(
            delete(CollectionState).where(
                CollectionState.aggregator == "watchlist",
                CollectionState.source_name == TEST_CODE,
            )
        )


@pytest.mark.integration
def test_watchlist_create_update_disable_and_reactivate() -> None:
    service = WatchlistService()
    payload = {
        "code": "699999",
        "name": "测试股票",
        "type": "stock",
        "source": "agent",
        "reason": "集成测试",
        "interval": 3600,
        "target_days": 12,
    }

    created = service.upsert_batch([payload])[0]
    repeated = service.upsert_batch([payload])[0]
    disabled = service.update_batch(
        [{"code": TEST_CODE, "enabled": False}]
    )[0]
    reactivated = service.upsert_batch([payload])[0]
    state = service.get(TEST_CODE)

    assert created.status == "created"
    assert repeated.status == "unchanged"
    assert disabled.updated is True
    assert disabled.reactivated is False
    assert reactivated.status == "reactivated"
    assert reactivated.should_collect_now is True
    assert state is not None
    assert state.enabled is True
    assert state.mode == "backfill"
    assert state.config["interval"] == 3600
    assert state.config["target_days"] == 12
    assert state.reason == "集成测试"


@pytest.mark.integration
def test_watchlist_rejects_interval_below_scanner_resolution() -> None:
    with pytest.raises(ValueError, match="大于或等于 300"):
        WatchlistService().upsert_batch(
            [
                {
                    "code": "699999",
                    "type": "stock",
                    "interval": 60,
                }
            ]
        )
