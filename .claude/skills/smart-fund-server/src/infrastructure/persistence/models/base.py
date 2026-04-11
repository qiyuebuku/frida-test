"""SQLAlchemy 2.0 Declarative Base

所有 ORM 模型的统一基类。仅做声明式映射,不包含业务方法。
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的根 Base

    使用 SQLAlchemy 2.0 类型注解风格 (Mapped[xxx])。
    所有模型必须继承本类才能被 metadata 收集。
    """

    pass
