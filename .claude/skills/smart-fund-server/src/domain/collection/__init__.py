"""数据采集聚合根 — domain 层

包含:
- repositories/    抽象接口(domain 层只看到接口,不看到 SQLAlchemy)
- (R3 会加 models/ services/)

实现位于 src/infrastructure/persistence/repositories/
"""
