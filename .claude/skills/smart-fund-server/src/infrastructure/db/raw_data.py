"""ft_raw_data — 客户端原始数据归档 + 缓存

按月分区（fetched_at），自动创建未来分区。
每次客户端请求外部 API 的返回值原样存入，永久保留。
同时作为缓存层：参数相同 + 未过期 → 直接返回。

R5: 统一使用 SQLAlchemy 连接池（不再 psycopg2.connect 裸连）
"""

import hashlib
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import text

from src.infrastructure.connections import get_session

logger = logging.getLogger(__name__)


# ==================== 建表 ====================

INIT_SQL = """
-- 主表（按月分区）
CREATE TABLE IF NOT EXISTS ft_raw_data (
    id              BIGSERIAL,
    source          VARCHAR(32) NOT NULL,
    method          VARCHAR(64) NOT NULL,
    params_hash     VARCHAR(64) NOT NULL,
    params          JSONB NOT NULL DEFAULT '{}',
    data            JSONB NOT NULL,
    data_count      INT DEFAULT 0,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,
    api_latency_ms  INT,
    source_name     VARCHAR(50) DEFAULT '',
    data_domain     VARCHAR(32) DEFAULT '',
    data_frequency  VARCHAR(16) DEFAULT '',
    market          VARCHAR(16) DEFAULT '',
    related_codes   JSONB DEFAULT '[]',
    trade_date      DATE,
    is_success      BOOLEAN DEFAULT TRUE,
    error_msg       TEXT DEFAULT '',
    PRIMARY KEY (id, fetched_at)
) PARTITION BY RANGE (fetched_at);

-- 索引（在主表上创建，自动应用到所有分区）
CREATE INDEX IF NOT EXISTS idx_raw_cache
    ON ft_raw_data(source, method, params_hash, fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_domain_time
    ON ft_raw_data(data_domain, fetched_at);
CREATE INDEX IF NOT EXISTS idx_raw_trade_date
    ON ft_raw_data(trade_date) WHERE trade_date IS NOT NULL;
"""


def init_raw_data_tables():
    """初始化 ft_raw_data 主表 + 当月及未来 2 个月的分区"""
    with get_session() as s:
        s.execute(text(INIT_SQL))
        now = datetime.now()
        for i in range(3):
            dt = now + timedelta(days=30 * i)
            _ensure_partition(s, dt.year, dt.month)
    logger.info("ft_raw_data 表及分区已初始化")


def _ensure_partition(session, year: int, month: int):
    """确保某个月份的分区存在"""
    name = f"ft_raw_data_{year}{month:02d}"
    start = f"{year}-{month:02d}-01"
    if month == 12:
        end = f"{year + 1}-01-01"
    else:
        end = f"{year}-{month + 1:02d}-01"

    session.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {name}
        PARTITION OF ft_raw_data
        FOR VALUES FROM ('{start}') TO ('{end}')
    """))


def ensure_future_partitions(months_ahead: int = 2):
    """确保未来 N 个月的分区已创建"""
    with get_session() as s:
        now = datetime.now()
        for i in range(months_ahead + 1):
            dt = now + timedelta(days=30 * i)
            _ensure_partition(s, dt.year, dt.month)


# ==================== 参数哈希 ====================

def params_hash(params: dict) -> str:
    """将参数字典规范化后计算 SHA256"""
    normalized = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# ==================== 缓存读取 ====================

def get_cached(source: str, method: str, params: dict, ttl_seconds: int) -> dict | None:
    """查询缓存：参数相同 + 未过期 → 返回 data，否则 None"""
    ph = params_hash(params)
    min_time = datetime.now() - timedelta(seconds=ttl_seconds)

    with get_session() as s:
        row = s.execute(text("""
            SELECT data FROM ft_raw_data
            WHERE source = :source AND method = :method AND params_hash = :ph
              AND fetched_at > :min_time AND is_success = TRUE
            ORDER BY fetched_at DESC
            LIMIT 1
        """), {"source": source, "method": method, "ph": ph, "min_time": min_time}).first()
        return row[0] if row else None


# ==================== 写入 ====================

def save_raw(
    source: str,
    method: str,
    params: dict,
    data,
    *,
    ttl_seconds: int | None = None,
    latency_ms: int | None = None,
    source_name: str = "",
    data_domain: str = "",
    data_frequency: str = "",
    market: str = "",
    related_codes: list | None = None,
    trade_date: str | None = None,
    is_success: bool = True,
    error_msg: str = "",
):
    """将客户端方法的原始返回值存入 ft_raw_data"""
    ph = params_hash(params)
    now = datetime.now()
    expires = (now + timedelta(seconds=ttl_seconds)) if ttl_seconds else None

    # 计算 data_count
    data_count = 0
    if isinstance(data, dict):
        inner = data.get("data", data)
        if isinstance(inner, list):
            data_count = len(inner)
        elif isinstance(inner, dict):
            for v in inner.values():
                if isinstance(v, list):
                    data_count = max(data_count, len(v))
    elif isinstance(data, list):
        data_count = len(data)

    # 序列化 + 清洗 \u0000（PG jsonb 不接受 NUL 字符）
    try:
        params_json = json.dumps(params, ensure_ascii=False, default=str).replace("\\u0000", "")
        data_json = json.dumps(data, ensure_ascii=False, default=str).replace("\\u0000", "")
        related_json = json.dumps(related_codes or [], ensure_ascii=False)
    except Exception as e:
        logger.warning(f"ft_raw_data 序列化失败: {e}")
        return

    try:
        with get_session() as s:
            s.execute(text("""
                INSERT INTO ft_raw_data
                (source, method, params_hash, params, data, data_count,
                 fetched_at, expires_at, api_latency_ms,
                 source_name, data_domain, data_frequency, market,
                 related_codes, trade_date, is_success, error_msg)
                VALUES (:source, :method, :ph, CAST(:params AS jsonb), CAST(:data AS jsonb), :data_count,
                        :fetched_at, :expires_at, :latency_ms,
                        :source_name, :data_domain, :data_frequency, :market,
                        CAST(:related_codes AS jsonb), :trade_date, :is_success, :error_msg)
            """), {
                "source": source, "method": method, "ph": ph,
                "params": params_json, "data": data_json, "data_count": data_count,
                "fetched_at": now, "expires_at": expires, "latency_ms": latency_ms,
                "source_name": source_name, "data_domain": data_domain,
                "data_frequency": data_frequency, "market": market,
                "related_codes": related_json, "trade_date": trade_date,
                "is_success": is_success, "error_msg": error_msg,
            })
    except Exception as e:
        logger.warning(f"ft_raw_data 写入失败: {e}")


# ==================== 重放查询 ====================

def query_raw(
    data_domain: str | None = None,
    source: str | None = None,
    method: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    trade_date: str | None = None,
    limit: int = 1000,
) -> list[dict]:
    """查询历史原始数据（供聚合层重放用）"""
    conditions = ["is_success = TRUE"]
    bind_params = {}

    if data_domain:
        conditions.append("data_domain = :data_domain")
        bind_params["data_domain"] = data_domain
    if source:
        conditions.append("source = :source")
        bind_params["source"] = source
    if method:
        conditions.append("method = :method")
        bind_params["method"] = method
    if start_time:
        conditions.append("fetched_at >= :start_time")
        bind_params["start_time"] = start_time
    if end_time:
        conditions.append("fetched_at <= :end_time")
        bind_params["end_time"] = end_time
    if trade_date:
        conditions.append("trade_date = :trade_date")
        bind_params["trade_date"] = trade_date

    where = " AND ".join(conditions)
    bind_params["limit"] = limit

    with get_session() as s:
        rows = s.execute(text(f"""
            SELECT source, method, params, data, data_count,
                   fetched_at, source_name, data_domain, trade_date
            FROM ft_raw_data
            WHERE {where}
            ORDER BY fetched_at ASC
            LIMIT :limit
        """), bind_params).fetchall()
        return [dict(row._mapping) for row in rows]
