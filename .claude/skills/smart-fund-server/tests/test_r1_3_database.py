"""T-R1.3 测试: connections/database.py"""
from sqlalchemy import text

import pytest


@pytest.mark.unit
def test_r1_3_1_get_session_runs_select_1():
    """T-R1.3-1: get_session() 能拿到 session 跑 SELECT 1"""
    from src.infrastructure.connections import get_session

    with get_session() as s:
        result = s.execute(text("SELECT 1")).scalar()
        assert result == 1


@pytest.mark.unit
def test_r1_3_2_two_sessions_independent():
    """T-R1.3-2: 同时拿两个 session,互不干扰"""
    from src.infrastructure.connections import get_session

    with get_session() as s1, get_session() as s2:
        v1 = s1.execute(text("SELECT 1")).scalar()
        v2 = s2.execute(text("SELECT 2")).scalar()
        assert v1 == 1 and v2 == 2


@pytest.mark.unit
def test_r1_3_3_target_switch():
    """T-R1.3-3: prod / test 两个 target 引擎独立"""
    from src.infrastructure.connections import get_engine

    eng_prod = get_engine("prod")
    eng_test = get_engine("test")
    assert eng_prod is not eng_test
    assert "jettask_test" in str(eng_test.url)
    assert "jettask_test" not in str(eng_prod.url)


@pytest.mark.unit
def test_r1_3_4_session_autorollback_on_exception():
    """T-R1.3-4: 异常时自动 rollback,不会污染连接"""
    from src.infrastructure.connections import get_session

    with pytest.raises(RuntimeError):
        with get_session() as s:
            s.execute(text("SELECT 1"))
            raise RuntimeError("intentional")
    # 接下来再用 session 不应受影响
    with get_session() as s:
        v = s.execute(text("SELECT 42")).scalar()
        assert v == 42
