"""SQLAlchemy engine + session 管理

进程级单例 engine,按 target(prod/test)切换。

用法:
    from src.infrastructure.connections import get_session

    with get_session() as session:
        result = session.scalars(select(News).limit(10)).all()
        # autocommit on context exit (no exception)
        # autorollback on context exit (with exception)

切换目标(测试用):
    from src.infrastructure.connections import set_target
    set_target("test")
"""
import os
from contextlib import contextmanager
from typing import Iterator, Literal

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.infrastructure.config.settings import DB_CONFIG

Target = Literal["prod", "test"]

# 进程内单例
_engines: dict[Target, Engine] = {}
_session_factories: dict[Target, sessionmaker] = {}
_current_target: Target = "prod"


def _build_url(target: Target) -> str:
    """根据 target 构造连接 URL

    prod → 走 settings.DB_CONFIG (jettask)
    test → 同主机/用户/密码,但 dbname=jettask_test
    """
    cfg = dict(DB_CONFIG)
    if target == "test":
        cfg["dbname"] = os.environ.get("TEST_DB_NAME", "jettask_test")
    return (
        f"postgresql+psycopg2://{cfg['user']}:{cfg['password']}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['dbname']}"
    )


def get_engine(target: Target | None = None) -> Engine:
    """获取(或懒加载创建) target 对应的 SQLAlchemy engine"""
    t = target or _current_target
    if t not in _engines:
        _engines[t] = create_engine(
            _build_url(t),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=3600,
            future=True,
        )
    return _engines[t]


def get_session_factory(target: Target | None = None) -> sessionmaker:
    """获取 target 对应的 sessionmaker"""
    t = target or _current_target
    if t not in _session_factories:
        _session_factories[t] = sessionmaker(
            bind=get_engine(t),
            expire_on_commit=False,
            future=True,
        )
    return _session_factories[t]


@contextmanager
def get_session(target: Target | None = None) -> Iterator[Session]:
    """上下文管理器: 拿一个 session,正常退出 commit,异常 rollback

    Example:
        with get_session() as s:
            news = s.scalars(select(News).limit(5)).all()
            for n in news:
                print(n.title)
    """
    factory = get_session_factory(target)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_target(target: Target) -> None:
    """切换默认 target(主要用于测试)"""
    global _current_target
    if target not in ("prod", "test"):
        raise ValueError(f"target 必须是 prod/test,实际 {target!r}")
    _current_target = target
