"""数据采集 checkpoint 统一存储 (ft_collection_state)

每个 (aggregator, source_name) 对应一行，记录:
- checkpoint：JSONB 增量游标，结构由 source 自己定义
    例: {"max_trade_date": "2026-04-11"}
        {"max_id": 12345, "since_iso": "..."}
        {"cursor": "abc123"}
- 运行元数据: last_run_at, consecutive_failures, last_error
- 运行时控制: enabled, interval_override

设计原则:
    1. checkpoint 更新和数据入库不强耦合事务
       依赖各 aggregator 的 ON CONFLICT DO NOTHING 保证幂等
    2. checkpoint 失败不影响数据入库（容错）
    3. checkpoint 是 dict，每个 source 自定义结构
    4. 提供 reset() 让某 source 下次自动全量
"""

import json
import logging
from typing import Any, Optional

import psycopg2
import psycopg2.extras

from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)


DDL = """
CREATE TABLE IF NOT EXISTS ft_collection_state (
    id                   SERIAL PRIMARY KEY,
    aggregator           VARCHAR(32) NOT NULL,
    source_name          VARCHAR(64) NOT NULL,
    checkpoint           JSONB DEFAULT '{}'::jsonb,
    last_run_at          TIMESTAMPTZ,
    last_success_at      TIMESTAMPTZ,
    last_error           TEXT DEFAULT '',
    consecutive_failures INT DEFAULT 0,
    enabled              BOOLEAN DEFAULT TRUE,
    interval_override    INT,
    total_runs           BIGINT DEFAULT 0,
    total_saved          BIGINT DEFAULT 0,
    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(aggregator, source_name)
);

CREATE INDEX IF NOT EXISTS idx_ft_collection_state_enabled
  ON ft_collection_state(enabled, aggregator);
"""


def init_table() -> None:
    """建表（应用启动时调用一次，可重复执行）"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(DDL)
            conn.commit()
    except Exception as e:
        logger.warning(f"checkpoint_store 建表失败: {e}")


def get(aggregator: str, source_name: str) -> Optional[dict]:
    """读取 (aggregator, source) 完整状态行；不存在返回 None"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM ft_collection_state
                    WHERE aggregator=%s AND source_name=%s
                    """,
                    (aggregator, source_name),
                )
                row = cur.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.debug(f"checkpoint_store.get({aggregator},{source_name}) 失败: {e}")
        return None


def get_checkpoint(aggregator: str, source_name: str) -> dict:
    """便捷方法：只取 checkpoint dict，无记录返回 {}（即首次拉取）"""
    state = get(aggregator, source_name)
    if not state:
        return {}
    cp = state.get("checkpoint")
    if cp is None:
        return {}
    if isinstance(cp, str):
        try:
            return json.loads(cp)
        except Exception:
            return {}
    return cp if isinstance(cp, dict) else {}


def is_enabled(aggregator: str, source_name: str) -> bool:
    """检查 source 是否启用（无记录默认 True）"""
    state = get(aggregator, source_name)
    if not state:
        return True
    return bool(state.get("enabled", True))


def update_success(
    aggregator: str,
    source_name: str,
    new_checkpoint: dict | None = None,
    saved_count: int = 0,
) -> None:
    """记录一次成功运行：更新 checkpoint + 重置失败计数 + 累加统计

    new_checkpoint=None 时不更新 checkpoint，只更新元数据
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if new_checkpoint is None:
                    # 不更新 checkpoint
                    cur.execute(
                        """
                        INSERT INTO ft_collection_state (
                            aggregator, source_name,
                            last_run_at, last_success_at,
                            last_error, consecutive_failures,
                            total_runs, total_saved, updated_at
                        ) VALUES (
                            %s, %s,
                            NOW(), NOW(),
                            '', 0,
                            1, %s, NOW()
                        )
                        ON CONFLICT (aggregator, source_name) DO UPDATE SET
                            last_run_at = NOW(),
                            last_success_at = NOW(),
                            last_error = '',
                            consecutive_failures = 0,
                            total_runs = ft_collection_state.total_runs + 1,
                            total_saved = ft_collection_state.total_saved + EXCLUDED.total_saved,
                            updated_at = NOW()
                        """,
                        (aggregator, source_name, saved_count),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO ft_collection_state (
                            aggregator, source_name, checkpoint,
                            last_run_at, last_success_at,
                            last_error, consecutive_failures,
                            total_runs, total_saved, updated_at
                        ) VALUES (
                            %s, %s, %s,
                            NOW(), NOW(),
                            '', 0,
                            1, %s, NOW()
                        )
                        ON CONFLICT (aggregator, source_name) DO UPDATE SET
                            checkpoint = EXCLUDED.checkpoint,
                            last_run_at = NOW(),
                            last_success_at = NOW(),
                            last_error = '',
                            consecutive_failures = 0,
                            total_runs = ft_collection_state.total_runs + 1,
                            total_saved = ft_collection_state.total_saved + EXCLUDED.total_saved,
                            updated_at = NOW()
                        """,
                        (
                            aggregator, source_name,
                            json.dumps(new_checkpoint, ensure_ascii=False, default=str),
                            saved_count,
                        ),
                    )
            conn.commit()
    except Exception as e:
        logger.warning(
            f"checkpoint_store.update_success({aggregator},{source_name}) 失败: {e}"
        )


def update_failure(aggregator: str, source_name: str, error_msg: str) -> None:
    """记录失败：累加 consecutive_failures + 写 last_error；不动 checkpoint"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ft_collection_state (
                        aggregator, source_name,
                        last_run_at, last_error, consecutive_failures,
                        total_runs, updated_at
                    ) VALUES (
                        %s, %s,
                        NOW(), %s, 1,
                        1, NOW()
                    )
                    ON CONFLICT (aggregator, source_name) DO UPDATE SET
                        last_run_at = NOW(),
                        last_error = EXCLUDED.last_error,
                        consecutive_failures = ft_collection_state.consecutive_failures + 1,
                        total_runs = ft_collection_state.total_runs + 1,
                        updated_at = NOW()
                    """,
                    (aggregator, source_name, (error_msg or "")[:500]),
                )
            conn.commit()
    except Exception as e:
        logger.warning(
            f"checkpoint_store.update_failure({aggregator},{source_name}) 失败: {e}"
        )


def reset(aggregator: str, source_name: str) -> bool:
    """重置 checkpoint（→ 下次 tick 自动全量重拉）"""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ft_collection_state
                    SET checkpoint='{}'::jsonb,
                        consecutive_failures=0,
                        last_error='',
                        updated_at=NOW()
                    WHERE aggregator=%s AND source_name=%s
                    """,
                    (aggregator, source_name),
                )
                conn.commit()
                return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"checkpoint_store.reset 失败: {e}")
        return False


def disable(aggregator: str, source_name: str) -> bool:
    return _set_enabled(aggregator, source_name, False)


def enable(aggregator: str, source_name: str) -> bool:
    return _set_enabled(aggregator, source_name, True)


def _set_enabled(aggregator: str, source_name: str, value: bool) -> bool:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO ft_collection_state (aggregator, source_name, enabled)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (aggregator, source_name) DO UPDATE SET
                        enabled = EXCLUDED.enabled, updated_at = NOW()
                    """,
                    (aggregator, source_name, value),
                )
                conn.commit()
                return True
    except Exception as e:
        logger.warning(f"checkpoint_store._set_enabled 失败: {e}")
        return False


def list_all(aggregator: str | None = None) -> list[dict]:
    """列出所有(或指定 aggregator)的 source 状态（监控/调试用）"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                if aggregator:
                    cur.execute(
                        """
                        SELECT * FROM ft_collection_state
                        WHERE aggregator=%s
                        ORDER BY source_name
                        """,
                        (aggregator,),
                    )
                else:
                    cur.execute(
                        "SELECT * FROM ft_collection_state ORDER BY aggregator, source_name"
                    )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"checkpoint_store.list_all 失败: {e}")
        return []
