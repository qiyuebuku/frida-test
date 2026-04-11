"""pytest 全局 fixtures

测试库：jettask_test（独立 PG 库,不污染生产）
单测/集成区分：
    - tests/unit/        : 不连任何外部，pytest -m unit
    - tests/integration/ : 连真实测试库 + redis，pytest -m integration
    - tests/e2e/         : 完整 worker + scheduler，pytest -m e2e

启动 fixture:
    db_session       : 真实测试库 SQLAlchemy session（autorollback）
    clean_db         : 一个空表的测试库（每个测试前 TRUNCATE）
    prod_session     : 生产库 *只读* session（用于 R1 期 ORM 模型 smoke test）
"""
import os
import sys
from pathlib import Path

# 让测试能 import src.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest


# ==================== 测试库连接配置 ====================

TEST_DB_CONFIG = {
    "host": os.getenv("TEST_DB_HOST", "119.23.227.187"),
    "port": int(os.getenv("TEST_DB_PORT", "5432")),
    "dbname": os.getenv("TEST_DB_NAME", "jettask_test"),
    "user": os.getenv("TEST_DB_USER", "jettask"),
    "password": os.getenv("TEST_DB_PASSWORD", "123456"),
}

PROD_DB_CONFIG = {
    "host": os.getenv("PROD_DB_HOST", "119.23.227.187"),
    "port": int(os.getenv("PROD_DB_PORT", "5432")),
    "dbname": os.getenv("PROD_DB_NAME", "jettask"),
    "user": os.getenv("PROD_DB_USER", "jettask"),
    "password": os.getenv("PROD_DB_PASSWORD", "123456"),
}


# ==================== 基础 fixtures ====================


@pytest.fixture(scope="session")
def test_db_url() -> str:
    c = TEST_DB_CONFIG
    return f"postgresql+psycopg2://{c['user']}:{c['password']}@{c['host']}:{c['port']}/{c['dbname']}"


@pytest.fixture(scope="session")
def prod_db_url() -> str:
    c = PROD_DB_CONFIG
    return f"postgresql+psycopg2://{c['user']}:{c['password']}@{c['host']}:{c['port']}/{c['dbname']}"


# ==================== smoke 验证 fixture ====================


@pytest.fixture
def baseline_path() -> Path:
    """R1 基线文件路径"""
    return PROJECT_ROOT / "tests" / "baselines" / "r1_pre.json"


# ==================== pytest 配置 ====================


def pytest_configure(config):
    """注册自定义 markers"""
    config.addinivalue_line("markers", "unit: 单元测试,无外部依赖")
    config.addinivalue_line("markers", "integration: 集成测试,需要真实测试库")
    config.addinivalue_line("markers", "e2e: 端到端测试,需要完整 worker/scheduler 链路")
    config.addinivalue_line("markers", "smoke: 快速冒烟测试")
