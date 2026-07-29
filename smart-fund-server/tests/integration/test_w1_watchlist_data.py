"""W1 测试: ft_watchlist_data 表 + Repository CRUD"""
from datetime import date

import pytest
from sqlalchemy import text

from src.infrastructure.connections import get_session, set_target


@pytest.fixture(autouse=True)
def use_test_db():
    set_target("test")
    yield
    set_target("prod")


@pytest.fixture
def clean_watchlist_data():
    with get_session() as s:
        s.execute(text("DELETE FROM ft_watchlist_data WHERE code LIKE 'test_%'"))
    yield
    with get_session() as s:
        s.execute(text("DELETE FROM ft_watchlist_data WHERE code LIKE 'test_%'"))


# ==================== ORM 模型 ====================


@pytest.mark.unit
def test_w1_orm_columns():
    """WatchlistData ORM 列数和字段正确"""
    from src.infrastructure.persistence.models.collection import WatchlistData
    cols = {c.name for c in WatchlistData.__table__.columns}
    assert cols == {
        "id",
        "code",
        "data_type",
        "trade_date",
        "data",
        "created_at",
        "updated_at",
    }


@pytest.mark.unit
def test_w1_orm_tablename():
    """表名为 ft_watchlist_data"""
    from src.infrastructure.persistence.models.collection import WatchlistData
    assert WatchlistData.__tablename__ == "ft_watchlist_data"


# ==================== Repository CRUD ====================


@pytest.mark.integration
def test_w1_save_and_query(clean_watchlist_data):
    """save_batch + query_by_code 基本流程"""
    from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import (
        WatchlistDataRepositoryImpl,
    )
    repo = WatchlistDataRepositoryImpl()
    today = date.today()

    items = [
        {"code": "test_007380", "data_type": "nav", "trade_date": today,
         "data": {"nav": 1.5678, "acc_nav": 2.1234}},
        {"code": "test_007380", "data_type": "holdings", "trade_date": today,
         "data": {"stocks": [{"code": "600036", "ratio": 8.5}]}},
    ]
    saved = repo.save_batch(items)
    assert saved == 2

    rows = repo.query_by_code("test_007380")
    assert len(rows) == 2
    assert {r["data_type"] for r in rows} == {"nav", "holdings"}


@pytest.mark.integration
def test_w1_upsert_on_conflict(clean_watchlist_data):
    """相同 (code, data_type, trade_date) 更新 data"""
    from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import (
        WatchlistDataRepositoryImpl,
    )
    repo = WatchlistDataRepositoryImpl()
    today = date.today()

    repo.save_batch([
        {"code": "test_007380", "data_type": "nav", "trade_date": today,
         "data": {"nav": 1.0}},
    ])
    repo.save_batch([
        {"code": "test_007380", "data_type": "nav", "trade_date": today,
         "data": {"nav": 2.0}},
    ])

    latest = repo.query_latest("test_007380", "nav")
    assert latest is not None
    assert latest["data"]["nav"] == 2.0
    assert latest["updated_at"] is not None

    rows = repo.query_by_code("test_007380", data_type="nav")
    assert len(rows) == 1  # 没有重复


@pytest.mark.integration
def test_w1_query_latest(clean_watchlist_data):
    """query_latest 返回最新一条"""
    from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import (
        WatchlistDataRepositoryImpl,
    )
    repo = WatchlistDataRepositoryImpl()

    repo.save_batch([
        {"code": "test_007380", "data_type": "nav", "trade_date": date(2026, 4, 10),
         "data": {"nav": 1.0}},
        {"code": "test_007380", "data_type": "nav", "trade_date": date(2026, 4, 12),
         "data": {"nav": 1.5}},
    ])

    latest = repo.query_latest("test_007380", "nav")
    assert latest["trade_date"] == date(2026, 4, 12)
    assert latest["data"]["nav"] == 1.5


@pytest.mark.integration
def test_w1_count_and_delete(clean_watchlist_data):
    """count_by_code + delete_by_code"""
    from src.infrastructure.persistence.repositories.watchlist_data_repository_impl import (
        WatchlistDataRepositoryImpl,
    )
    repo = WatchlistDataRepositoryImpl()
    today = date.today()

    repo.save_batch([
        {"code": "test_007380", "data_type": "nav", "trade_date": today, "data": {"v": 1}},
        {"code": "test_007380", "data_type": "holdings", "trade_date": today, "data": {"v": 2}},
        {"code": "test_007380", "data_type": "nav", "trade_date": date(2026, 4, 11), "data": {"v": 3}},
    ])

    counts = repo.count_by_code("test_007380")
    assert counts == {"nav": 2, "holdings": 1}

    deleted = repo.delete_by_code("test_007380")
    assert deleted == 3
    assert repo.query_by_code("test_007380") == []
