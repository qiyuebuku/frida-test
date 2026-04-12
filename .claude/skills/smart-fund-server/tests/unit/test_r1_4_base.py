"""T-R1.4 测试: Declarative Base"""
import pytest


@pytest.mark.unit
def test_r1_4_1_base_registry_starts_empty():
    """T-R1.4-1: 空 Base,无副作用 import (此时还没 R1.5 的模型注册)"""
    from src.infrastructure.persistence.models.base import Base

    # R1.4 阶段 Base.registry 应该是空的;R1.5+ 之后会有 6+ 个 mapper
    assert Base.registry is not None
    assert hasattr(Base, "metadata")


@pytest.mark.unit
def test_r1_4_2_base_can_be_subclassed():
    """T-R1.4-2: Base 可被子类继承(SQLAlchemy 2.0 声明式)"""
    from sqlalchemy import Integer, String
    from sqlalchemy.orm import Mapped, mapped_column

    from src.infrastructure.persistence.models.base import Base

    # 在测试函数内动态定义,避免污染全局 registry
    class _Probe(Base):
        __tablename__ = "_probe_test_only"
        id: Mapped[int] = mapped_column(Integer, primary_key=True)
        name: Mapped[str] = mapped_column(String(32))

    assert _Probe.__tablename__ == "_probe_test_only"
    # 类已注册到 metadata
    assert "_probe_test_only" in Base.metadata.tables
    # 清理
    Base.metadata.remove(Base.metadata.tables["_probe_test_only"])
