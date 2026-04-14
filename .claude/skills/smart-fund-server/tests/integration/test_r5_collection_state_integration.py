"""R5 集成测试: ft_collection_state 独立列 + ensure_initialized + update_success"""
import pytest
from sqlalchemy import text

from src.infrastructure.connections import get_session, set_target


@pytest.fixture(autouse=True)
def use_test_db():
    set_target("test")
    yield
    set_target("prod")


@pytest.fixture
def clean_state():
    with get_session() as s:
        s.execute(text("DELETE FROM ft_collection_state WHERE aggregator = 'test_r5'"))
    yield
    with get_session() as s:
        s.execute(text("DELETE FROM ft_collection_state WHERE aggregator = 'test_r5'"))


@pytest.mark.integration
def test_r5_ensure_initialized_creates_record(clean_state):
    """ensure_initialized 创建新记录"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl

    repo = CollectionStateRepositoryImpl()
    created = repo.ensure_initialized(
        "test_r5", "src1",
        checkpoint={"mode": "backfill", "target_time": "2026-01-01"},
        config={"target_days": 60, "interval": 300},
    )
    assert created is True

    state = repo.get("test_r5", "src1")
    assert state is not None
    assert state["mode"] == "backfill"
    assert state["target_time"] == "2026-01-01"
    assert state["config"]["target_days"] == 60


@pytest.mark.integration
def test_r5_ensure_initialized_no_overwrite(clean_state):
    """ensure_initialized 不覆盖已有记录"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl

    repo = CollectionStateRepositoryImpl()
    repo.ensure_initialized("test_r5", "src1",
                            checkpoint={"mode": "backfill", "target_time": "2026-01-01"},
                            config={"target_days": 60})
    # 第二次调用不覆盖
    created = repo.ensure_initialized("test_r5", "src1",
                                      checkpoint={"mode": "incremental", "target_time": "2099-01-01"},
                                      config={"target_days": 999})
    assert created is False

    state = repo.get("test_r5", "src1")
    assert state["mode"] == "backfill"  # 未被覆盖
    assert state["config"]["target_days"] == 60


@pytest.mark.integration
def test_r5_update_success_writes_columns(clean_state):
    """update_success 把 dict 字段写入独立列"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl

    repo = CollectionStateRepositoryImpl()
    repo.update_success("test_r5", "src1", {
        "mode": "backfill",
        "target_time": "2026-02-11",
        "newest_time": "2026-04-12T15:30:00+08:00",
        "oldest_time": "2026-03-01T08:00:00+08:00",
        "cursor": 42,
    }, saved_count=10)

    state = repo.get("test_r5", "src1")
    assert state["mode"] == "backfill"
    assert state["target_time"] == "2026-02-11"
    assert state["newest_time"] == "2026-04-12T15:30:00+08:00"
    assert state["oldest_time"] == "2026-03-01T08:00:00+08:00"
    assert state["cursor"] == 42
    assert state["total_saved"] == 10


@pytest.mark.integration
def test_r5_update_success_increments_counters(clean_state):
    """update_success 累加 total_runs 和 total_saved"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl

    repo = CollectionStateRepositoryImpl()
    repo.update_success("test_r5", "src1", {"mode": "incremental"}, saved_count=5)
    repo.update_success("test_r5", "src1", {"mode": "incremental"}, saved_count=3)

    state = repo.get("test_r5", "src1")
    assert state["total_runs"] == 2
    assert state["total_saved"] == 8


@pytest.mark.integration
def test_r5_update_success_mode_switch(clean_state):
    """update_success 可以切换 mode 和 backfill_status"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl

    repo = CollectionStateRepositoryImpl()
    repo.update_success("test_r5", "src1", {"mode": "backfill"}, 0)
    assert repo.get("test_r5", "src1")["mode"] == "backfill"

    repo.update_success("test_r5", "src1", {
        "mode": "incremental",
        "backfill_status": "ceiling",
        "cursor": None,
    }, 0)
    state = repo.get("test_r5", "src1")
    assert state["mode"] == "incremental"
    assert state["backfill_status"] == "ceiling"


@pytest.mark.integration
def test_r5_reset_clears_state(clean_state):
    """reset 清空进度，mode 回到 backfill"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl

    repo = CollectionStateRepositoryImpl()
    repo.update_success("test_r5", "src1", {
        "mode": "incremental",
        "newest_time": "2026-04-12",
        "backfill_status": "done",
    }, 100)

    repo.reset("test_r5", "src1")
    state = repo.get("test_r5", "src1")
    assert state["mode"] == "backfill"
    assert state["newest_time"] is None
    assert state["oldest_time"] is None
    assert state["backfill_status"] is None
    assert state["cursor"] is None


@pytest.mark.integration
def test_r5_init_state_classmethod():
    """BaseAggregator.init_state 写入正确的列"""
    from src.infrastructure.persistence.repositories import CollectionStateRepositoryImpl
    from src.infrastructure.connections import get_session
    from sqlalchemy import text

    # 清理
    with get_session() as s:
        s.execute(text("DELETE FROM ft_collection_state WHERE aggregator = 'news' AND source_name = '__test_init__'"))

    from src.domain.collection.services.base import BaseAggregator

    class TestAgg(BaseAggregator):
        data_domain = "news"
        SOURCE_CONFIGS = {
            "__test_init__": {"target_days": 30, "interval": 600},
        }

    TestAgg.init_state()

    repo = CollectionStateRepositoryImpl()
    state = repo.get("news", "__test_init__")
    assert state is not None
    assert state["mode"] == "backfill"
    assert state["target_time"] is not None
    assert state["config"]["target_days"] == 30

    # 清理
    with get_session() as s:
        s.execute(text("DELETE FROM ft_collection_state WHERE aggregator = 'news' AND source_name = '__test_init__'"))
