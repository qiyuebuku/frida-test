"""全局配置 — 所有配置集中管理，支持环境变量覆盖

优先级：环境变量 > .env 文件 > 此文件默认值
"""

import os
from pathlib import Path

# 自动加载 .env 文件（项目根目录）
_env_file = Path(__file__).resolve().parents[3] / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:  # 不覆盖已设置的环境变量
            os.environ[key] = value


# ==================== 数据库 ====================

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "119.23.227.187"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "jettask"),
    "user": os.getenv("DB_USER", "jettask"),
    "password": os.getenv("DB_PASSWORD", "123456"),
}

# PostgreSQL 连接 URL（供 jettask / SQLAlchemy 使用，独立数据库）
JETTASK_DB_NAME = os.getenv("JETTASK_DB_NAME", "jettask_queue")
PG_URL = os.getenv(
    "PG_URL",
    f"postgresql+asyncpg://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{JETTASK_DB_NAME}"
)

# ==================== Redis ====================

REDIS_URL = os.getenv("REDIS_URL", "redis://119.23.227.187:6379/0")

# ==================== 服务 ====================

SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8900"))

# 本服务的外部可访问地址（供客户端内部互调用）
SERVICE_BASE_URL = os.getenv("SERVICE_BASE_URL", "http://119.23.227.187:8900")

# ==================== OCR ====================

OCR_URL = os.getenv("OCR_URL", "http://119.23.227.187:8675/glmocr/parse")

# ==================== Embedding 服务 ====================

# Qwen3-Embedding-4B HTTP 服务（部署在远程 GPU 机器，监听 0.0.0.0:8901）
# - 远程同机部署时：http://127.0.0.1:8901
# - 本地开发连远程内网：http://10.168.1.210:8901（远程内网地址）
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "http://10.168.1.210:8901")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1024"))  # MRL 截断维度
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
EMBEDDING_TIMEOUT = float(os.getenv("EMBEDDING_TIMEOUT", "60"))

# ==================== Claude / Skill ====================

SKILL_DIR = os.getenv("SKILL_DIR", "/home/yuyangruan/claude-skills/.claude/skills/fund-trade")
SKILLS_DIR = os.getenv("SKILLS_DIR", "")  # SkillRegistry 根目录，空则用 main.py 上级目录

# ==================== 交易 ====================

TRADE_BASE_URL = "https://trade.5ifund.com"
TRADE_DEVICE_ID = os.getenv("TRADE_DEVICE_ID", "7246091a5f126b63")
TRADE_DEVICE_SIGN = os.getenv("TRADE_DEVICE_SIGN", "2293a78f6581c12bbb334759458d4de3")

# ==================== jettask ====================

JETTASK_PREFIX = os.getenv("JETTASK_PREFIX", "fund_aggregator")
