"""聚合基类：定时任务框架 + 源级间隔控制 + 查询/重放 API"""

import json
import logging
import time
from datetime import datetime
from typing import Any, Callable

import psycopg2
import psycopg2.extras

from src.infrastructure.db import checkpoint_store
from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)

# 应用启动时确保 ft_collection_state 表存在
checkpoint_store.init_table()


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

        流程:
            1. checkpoint_store.get_checkpoint(domain, source) → cp dict
            2. _should_fetch 检查内存缓存 + DB enabled 状态
            3. fetch_fn(cp) → raw
            4. normalize_fn(raw) → items
            5. _save(items) → saved_count
            6. _compute_checkpoint(source, items, cp) → new_cp
            7. checkpoint_store.update_success(...) 或 update_failure(...)

        Returns: {sources_run, total_saved}
            供事件驱动级联（D.1）判断是否触发下游 task
        """
        now = time.time()
        sources_run = 0
        total_saved = 0
        for src in self.sources:
            # 1. DB 层 enabled 检查
            if not checkpoint_store.is_enabled(self.data_domain, src.name):
                continue
            # 2. 内存层 interval 检查
            if not self._should_fetch(src.name, src.interval, now):
                continue
            try:
                # 3. 拿 checkpoint（dict 结构，{} 表示首次）
                cp = checkpoint_store.get_checkpoint(self.data_domain, src.name)
                # 4. fetch
                raw = await src.fetch_fn(cp)
                if not raw:
                    self._last_fetch[src.name] = now
                    checkpoint_store.update_success(self.data_domain, src.name, None, 0)
                    continue

                items = src.normalize_fn(raw)
                if not items:
                    self._last_fetch[src.name] = now
                    checkpoint_store.update_success(self.data_domain, src.name, None, 0)
                    continue

                saved = self._save(items)
                self._last_fetch[src.name] = now
                sources_run += 1
                total_saved += saved or 0

                # 5. 计算并写入新 checkpoint
                new_cp = self._compute_checkpoint(src.name, items, cp)
                checkpoint_store.update_success(
                    self.data_domain, src.name, new_cp, saved or 0,
                )
                logger.info(
                    f"[{self.data_domain}:{src.name}] normalize {len(items)} 条，入库 {saved} 条 cp={new_cp}"
                )
            except Exception as e:
                logger.warning(f"[{self.data_domain}:{src.name}] 采集失败: {e}")
                checkpoint_store.update_failure(self.data_domain, src.name, str(e))
        return {"sources_run": sources_run, "total_saved": total_saved}

    def _should_fetch(self, name: str, interval: int, now: float) -> bool:
        last = self._last_fetch.get(name, 0)
        return (now - last) >= interval

    # ==================== 子类必须实现 ====================

    def _save(self, items: list[dict]) -> int:
        raise NotImplementedError

    def _get_checkpoint(self, source_name: str) -> Any:
        """[已废弃] 旧 checkpoint 接口

        新代码用 checkpoint_store + _compute_checkpoint。
        保留此方法仅为兼容已存在的子类（fund_flow 等老代码逐步迁移）。
        """
        return None

    def _compute_checkpoint(
        self,
        source_name: str,
        items: list[dict],
        prev_cp: dict,
    ) -> dict:
        """根据本次入库的 items 计算新的 checkpoint dict

        默认实现:
            - 取 items 中最大的 trade_date 作为 max_trade_date
            - 与 prev_cp 中的旧值取 max（防止本次拉到旧数据导致 checkpoint 倒退）

        子类可以重写返回任意 dict 结构（max_id / cursor / 多字段）。
        """
        max_date = (prev_cp or {}).get("max_trade_date")
        for item in items:
            d = item.get("trade_date") or item.get("published_at")
            if d:
                d_str = d if isinstance(d, str) else str(d)
                d_str = d_str[:10]
                if not max_date or d_str > max_date:
                    max_date = d_str
        return {"max_trade_date": max_date} if max_date else (prev_cp or {})

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
