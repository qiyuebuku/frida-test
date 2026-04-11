"""聚合基类：定时任务框架 + 源级间隔控制 + 查询/重放 API"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Callable

import psycopg2
import psycopg2.extras

from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)


class SourceDef:
    """数据源定义"""
    __slots__ = ("name", "fetch_fn", "interval", "normalize_fn")

    def __init__(
        self,
        name: str,
        fetch_fn: Callable,
        interval: int,
        normalize_fn: Callable,
    ):
        self.name = name
        self.fetch_fn = fetch_fn          # async (checkpoint) -> raw_items
        self.interval = interval           # 秒
        self.normalize_fn = normalize_fn   # (raw_items) -> list[dict]


class BaseAggregator:
    """聚合基类

    子类需要定义:
        data_domain: str          — 数据领域标识（用于 ft_raw_data 重放查询）
        task_interval: int        — 任务触发间隔（秒），取所有源的最小间隔
        sources: list[SourceDef]  — 数据源列表

    子类需要实现:
        _save(items) -> int       — 写入结果表，返回成功写入条数
        _get_checkpoint(name)     — 返回增量采集断点
        query(**filters) -> list  — 从结果表查询
    """

    data_domain: str = ""
    task_interval: int = 60
    sources: list[SourceDef] = []

    def __init__(self):
        self._last_fetch: dict[str, float] = {}

    # ==================== 定时任务入口 ====================

    async def tick(self) -> dict:
        """串行遍历所有源，到期的才请求，拿到即入库

        Returns: {sources_run: int, total_saved: int}
            供事件驱动级联（D.1）判断是否触发下游 task
        """
        now = time.time()
        sources_run = 0
        total_saved = 0
        for src in self.sources:
            if not self._should_fetch(src.name, src.interval, now):
                continue
            try:
                checkpoint = self._get_checkpoint(src.name)
                raw = await src.fetch_fn(checkpoint)
                if not raw:
                    self._last_fetch[src.name] = now
                    continue

                items = src.normalize_fn(raw)
                if not items:
                    self._last_fetch[src.name] = now
                    continue

                saved = self._save(items)
                self._last_fetch[src.name] = now
                sources_run += 1
                total_saved += saved or 0
                logger.info(f"[{self.data_domain}:{src.name}] 采集 {len(raw)} 条，入库 {saved} 条")
            except Exception as e:
                # 失败不更新 last_fetch，下次继续尝试
                logger.warning(f"[{self.data_domain}:{src.name}] 采集失败: {e}")
        return {"sources_run": sources_run, "total_saved": total_saved}

    def _should_fetch(self, name: str, interval: int, now: float) -> bool:
        last = self._last_fetch.get(name, 0)
        return (now - last) >= interval

    # ==================== 子类必须实现 ====================

    def _save(self, items: list[dict]) -> int:
        raise NotImplementedError

    def _get_checkpoint(self, source_name: str) -> Any:
        """返回增量采集的断点值（如 since_id, since_date 等）"""
        return None

    async def query(self, **filters) -> list[dict]:
        raise NotImplementedError

    # ==================== 重放 ====================

    async def replay(self, start: str, end: str) -> list[dict]:
        """从 ft_raw_data 读历史数据，用当前 normalize 逻辑重新处理"""
        from src.infrastructure.db import raw_data as rd

        records = rd.query_raw(
            data_domain=self.data_domain,
            start_time=start,
            end_time=end,
            limit=5000,
        )
        items = []
        for record in records:
            normalize_fn = self._find_normalize_fn(record["source"], record["method"])
            if normalize_fn:
                raw = record["data"]
                # raw_data 中 data 可能是单条或列表
                if not isinstance(raw, list):
                    raw = [raw]
                try:
                    items.extend(normalize_fn(raw))
                except Exception as e:
                    logger.debug(f"重放 normalize 失败: {e}")
        return items

    def _find_normalize_fn(self, source: str, method: str) -> Callable | None:
        """根据 source+method 匹配对应的 normalize 函数"""
        for src in self.sources:
            if src.name == source or source in src.name:
                return src.normalize_fn
        return None

    # ==================== 建表辅助 ====================

    @staticmethod
    def _exec_ddl(sql: str):
        """执行建表 DDL"""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
        except Exception as e:
            logger.warning(f"建表失败: {e}")

    @staticmethod
    def _insert_many(
        table: str,
        columns: list[str],
        rows: list[tuple],
        conflict_clause: str = "",
    ) -> int:
        """批量插入，返回成功写入行数"""
        if not rows:
            return 0
        placeholders = ", ".join(["%s"] * len(columns))
        cols = ", ".join(columns)
        sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
        if conflict_clause:
            sql += f" {conflict_clause}"

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    saved = 0
                    for row in rows:
                        try:
                            cur.execute(sql, row)
                            saved += cur.rowcount
                        except psycopg2.errors.UniqueViolation:
                            conn.rollback()
                        except Exception as e:
                            conn.rollback()
                            logger.debug(f"插入失败: {e}")
                conn.commit()
                return saved
        except Exception as e:
            logger.warning(f"批量插入 {table} 失败: {e}")
            return 0

    @staticmethod
    def _query_table(
        table: str,
        conditions: list[str] | None = None,
        values: list | None = None,
        order_by: str = "id DESC",
        limit: int = 100,
    ) -> list[dict]:
        """通用结果表查询"""
        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)
        vals = list(values or [])
        vals.append(limit)

        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    f"SELECT * FROM {table} {where} ORDER BY {order_by} LIMIT %s",
                    vals,
                )
                return [dict(r) for r in cur.fetchall()]
