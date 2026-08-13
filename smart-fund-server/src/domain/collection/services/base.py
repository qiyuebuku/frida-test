"""聚合基类：单数据源执行、断点管理与查询/重放 API。"""

import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import psycopg2
import psycopg2.extras

from src.infrastructure.db import checkpoint_store, redis_lock
from src.infrastructure.db.fund_db import get_conn
from src.infrastructure.time_utils import app_today

logger = logging.getLogger(__name__)

# 单 source 锁的默认 TTL（秒）
# 必须大于该 source 的最长执行时间，否则锁过期会导致并发
DEFAULT_LOCK_TTL = 600


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
        SOURCE_CONFIGS: dict      — 源模板配置（仅首次初始化时写入 DB）

    子类需要实现:
        _save(items) -> int       — 写入结果表，返回成功写入条数
        _get_checkpoint(name)     — 返回增量采集断点
        query(**filters) -> list  — 从结果表查询
    """

    data_domain: str = ""
    task_interval: int = 60
    sources: list[SourceDef] = []
    SOURCE_CONFIGS: dict = {}
    BACKFILL_SOURCES: frozenset[str] = frozenset()
    AUTO_CATCHUP_SOURCES: frozenset[str] = frozenset()
    AUTO_CATCHUP_MIN_STALE_SECONDS: int = 3600
    AUTO_CATCHUP_OVERLAP_DAYS: int = 1
    MAX_PAGES_PER_TICK: int = 500  # 回填安全阀：单次 tick 最多翻页数

    def __init__(self):
        pass

    @classmethod
    def init_state(cls):
        """初始化 ft_collection_state 记录（由 CLI `init-state` 命令调用）

        从 SOURCE_CONFIGS 模板写入 DB，已有记录不覆盖。
        """
        if not cls.SOURCE_CONFIGS or not cls.data_domain:
            return
        from datetime import timedelta
        today = app_today()
        for name, cfg in cls.SOURCE_CONFIGS.items():
            target_days = cfg.get("target_days", 0)
            mode = cfg.get("default_mode", "backfill" if target_days > 0 else "incremental")
            target_time = (today - timedelta(days=target_days)).isoformat() if target_days else None
            cp = {
                "mode": mode,
                "target_time": target_time,
                "newest_time": None,
                "oldest_time": None,
                "cursor": None,
            }
            checkpoint_store.ensure_initialized(cls.data_domain, name, cp, cfg)

    # ==================== 网络重试 ====================

    MAX_RETRIES = 3
    RETRY_DELAY = 2  # 秒

    # 值得重试的异常类型（网络暂时性错误）
    RETRYABLE_ERRORS = (
        "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
        "ConnectError", "RemoteProtocolError",
        "ConnectionError", "TimeoutError",
    )

    async def _fetch_with_retry(self, src: SourceDef, cp: dict):
        """fetch_fn 带重试 + 自动续锁，仅对网络超时/连接错误重试"""
        import asyncio as _asyncio
        lock = cp.get("_lock")
        last_err = None
        for attempt in range(1, self.MAX_RETRIES + 1):
            # 每次尝试前续锁，防止长时间 fetch 导致锁过期
            if lock:
                lock.renew()
            try:
                return await src.fetch_fn(cp)
            except Exception as e:
                err_type = type(e).__name__
                if err_type not in self.RETRYABLE_ERRORS and \
                   not isinstance(e, (ConnectionError, TimeoutError)):
                    raise  # 非网络错误，不重试
                last_err = e
                if attempt < self.MAX_RETRIES:
                    delay = self.RETRY_DELAY * attempt
                    logger.warning(
                        f"[{self.data_domain}:{src.name}] {err_type} (第{attempt}次)，{delay}s 后重试"
                    )
                    await _asyncio.sleep(delay)
        raise last_err

    # ==================== JetTask 单源执行入口 ====================

    async def run_source(
        self,
        source_name: str,
        *,
        state_override: dict | None = None,
        persist_checkpoint: bool = True,
    ) -> dict:
        """执行一个明确的数据源，不在领域层判断调度周期。

        周期、启停、超时和重试全部由 JetTask 的独立 Schedule/Task 控制；
        ``ft_collection_state`` 在这里仅保存增量游标、回填进度和执行结果。
        同源互斥仍保留，防止手工触发与定时触发重叠写入。
        """
        from datetime import datetime, timezone

        source = next((item for item in self.sources if item.name == source_name), None)
        if source is None:
            available = ", ".join(item.name for item in self.sources)
            raise ValueError(
                f"未知采集源 [{self.data_domain}:{source_name}]，可用源: {available}"
            )

        state = state_override or checkpoint_store.get(self.data_domain, source.name)
        if not state:
            raise RuntimeError(
                f"ft_collection_state 中未找到 [{self.data_domain}:{source.name}] 的记录，"
                "请先执行: python -m src.interfaces.cli init state"
            )

        lock_name = f"{self.data_domain}:{source.name}"
        with redis_lock.acquire(lock_name, ttl=DEFAULT_LOCK_TTL) as lock:
            if not lock:
                raise RuntimeError(
                    f"[{self.data_domain}:{source.name}] 上一次执行仍持有锁"
                )

            if state_override is None:
                state = checkpoint_store.get(self.data_domain, source.name) or state
            cp = self._build_runtime_checkpoint(
                state=state,
                source=source,
                now_utc=datetime.now(timezone.utc),
                lock=lock,
            )
            checkpoint_store.mark_started(
                task_id=f"collect_{self.data_domain}_{source.name}",
                aggregator=self.data_domain,
                source_name=source.name,
                task_type="pull",
            )
            try:
                raw = await self._fetch_with_retry(source, cp)
                fetched_count = len(raw) if isinstance(raw, (list, tuple, dict)) else int(bool(raw))
                items = source.normalize_fn(raw) if raw else []
                saved = self._save(items) if items else 0
                new_cp = self._compute_checkpoint(source.name, items, cp)
                checkpoint_store.update_success(
                    self.data_domain,
                    source.name,
                    new_cp if items and persist_checkpoint else None,
                    saved or 0,
                )
                logger.info(
                    "[%s:%s] 拉取 %s 条，归一化 %s 条，入库 %s 条 cp=%s",
                    self.data_domain,
                    source.name,
                    fetched_count,
                    len(items),
                    saved or 0,
                    new_cp,
                )
                return {
                    "source_name": source.name,
                    "fetched_count": fetched_count,
                    "valid_count": len(items),
                    "saved_count": saved or 0,
                    "checkpoint_before": {
                        key: value for key, value in cp.items() if not key.startswith("_")
                    },
                    "checkpoint_after": new_cp,
                }
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                checkpoint_store.update_failure(self.data_domain, source.name, err_msg)
                logger.warning("[%s:%s] 采集失败: %s", self.data_domain, source.name, err_msg)
                raise

    async def tick(self) -> dict:
        """手工批量执行入口；生产调度不得调用，由 JetTask 逐源调度。"""
        sources_run = 0
        total_saved = 0
        for src in self.sources:
            result = await self.run_source(src.name)
            sources_run += 1
            total_saved += int(result.get("saved_count") or 0)
        return {"sources_run": sources_run, "total_saved": total_saved}

    def _build_runtime_checkpoint(
        self,
        *,
        state: dict,
        source: SourceDef,
        now_utc: datetime,
        lock,
    ) -> dict:
        """构建本次 fetch checkpoint，并在长时间停机后自动追赶断档。"""
        cp = {
            "mode": state.get("mode", "incremental"),
            "target_time": state.get("target_time"),
            "newest_time": state.get("newest_time"),
            "oldest_time": state.get("oldest_time"),
            "backfill_status": state.get("backfill_status"),
            "cursor": state.get("cursor"),
            "_config": state.get("config") or {},
            "_lock": lock,
        }

        if cp["mode"] != "incremental" or source.name not in self.AUTO_CATCHUP_SOURCES:
            return cp

        last_success_at = state.get("last_success_at")
        newest_time = state.get("newest_time")
        if last_success_at is None or not newest_time:
            return cp

        effective_interval = int(source.interval)
        stale_after = max(effective_interval * 2, self.AUTO_CATCHUP_MIN_STALE_SECONDS)
        if (now_utc - last_success_at).total_seconds() <= stale_after:
            return cp

        try:
            newest_date = datetime.fromisoformat(str(newest_time)[:10]).date()
        except ValueError:
            logger.warning(
                f"[{self.data_domain}:{source.name}] newest_time 无法解析，跳过自动断档追赶: "
                f"{newest_time!r}"
            )
            return cp

        target_date = newest_date - timedelta(days=self.AUTO_CATCHUP_OVERLAP_DAYS)
        cp.update(
            {
                "mode": "backfill",
                "target_time": target_date.isoformat(),
                "cursor": None,
                "backfill_status": None,
                "_auto_catchup": True,
            }
        )
        logger.warning(
            f"[{self.data_domain}:{source.name}] 检测到采集断档 "
            f"{int((now_utc - last_success_at).total_seconds())}s，"
            f"自动从当前数据向后追赶至 {target_date.isoformat()}"
        )
        return cp

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
            - 保留 prev_cp 中的所有字段（mode/target_time/cursor 等）
            - 计算 newest_time / oldest_time（从 items 的 trade_date 或 published_at）
            - 兼容旧格式：同时更新 max_trade_date

        子类可以重写（如 NewsAggregator 有回填逻辑）。
        """
        prev_cp = prev_cp or {}
        if not isinstance(prev_cp, dict):
            prev_cp = {}

        # 剥离内部字段（不持久化到 DB）
        new_cp = {k: v for k, v in prev_cp.items() if not k.startswith("_")}

        if not items:
            return new_cp

        # 提取所有时间（保留完整精度，不截断到天）
        dates = []
        for item in items:
            d = item.get("trade_date") or item.get("published_at")
            if not d:
                continue
            try:
                d_str = d if isinstance(d, str) else str(d)
                dates.append(d_str)
            except Exception:
                continue

        if not dates:
            return new_cp

        batch_newest = max(dates)
        batch_oldest = min(dates)

        # 更新 newest_time（取历史最大值）
        prev_newest = new_cp.get("newest_time")
        new_cp["newest_time"] = max(batch_newest, prev_newest) if prev_newest else batch_newest

        # 更新 oldest_time（取历史最小值）
        prev_oldest = new_cp.get("oldest_time")
        if prev_oldest:
            new_cp["oldest_time"] = min(batch_oldest, prev_oldest)
        else:
            new_cp["oldest_time"] = batch_oldest

        # ── 通用模式切换：backfill → incremental ──
        if new_cp.get("mode") == "backfill":
            target_time = new_cp.get("target_time")
            oldest_time = new_cp.get("oldest_time")
            is_auto_catchup = bool(prev_cp.get("_auto_catchup"))
            reached_time = batch_oldest if is_auto_catchup else oldest_time
            target_date = str(target_time)[:10] if target_time else ""
            reached_date = str(reached_time)[:10] if reached_time else ""
            no_time_progress = bool(
                target_date
                and prev_oldest
                and batch_oldest >= prev_oldest
                and str(oldest_time)[:10] > target_date
            )

            # 子类 hook：可通过 _get_backfill_signal 注入额外信号（如 _backfill_loop 的触顶/完成）
            signal = self._get_backfill_signal(source_name)

            if signal == "done" or (
                target_date and reached_date and reached_date <= target_date
            ):
                new_cp["mode"] = "incremental"
                new_cp["cursor"] = None
                new_cp["backfill_status"] = "done"
                logger.info(
                    f"[{self.data_domain}:{source_name}] 回填完成: "
                    f"覆盖 {oldest_time} ~ {new_cp.get('newest_time')}"
                )
            elif signal == "ceiling":
                new_cp["mode"] = "incremental"
                new_cp["cursor"] = None
                new_cp["backfill_status"] = "ceiling"
                logger.warning(
                    f"[{self.data_domain}:{source_name}] 回填触顶（数据源历史不足）: "
                    f"覆盖 {oldest_time} ~ {new_cp.get('newest_time')}, "
                    f"目标 {target_time} 未达到"
                )
            elif is_auto_catchup:
                new_cp["mode"] = "incremental"
                new_cp["cursor"] = None
                new_cp["backfill_status"] = "ceiling"
                logger.warning(
                    f"[{self.data_domain}:{source_name}] 自动断档追赶未到达停机边界，"
                    f"按当前接口单次返回上限结束: batch_oldest={batch_oldest}, "
                    f"target={target_time}"
                )
            elif no_time_progress:
                new_cp["mode"] = "incremental"
                new_cp["cursor"] = None
                new_cp["backfill_status"] = "ceiling"
                logger.warning(
                    f"[{self.data_domain}:{source_name}] 回填无时间进展，按数据源历史上限结束: "
                    f"覆盖 {oldest_time} ~ {new_cp.get('newest_time')}, "
                    f"目标 {target_time} 未达到"
                )
            elif signal is not None:
                # 保存翻页游标，下次继续
                new_cp["cursor"] = signal

        return new_cp

    def _get_backfill_signal(self, source_name: str) -> str | None:
        """子类 hook：返回回填信号

        Returns:
            "done" — 回填完成（时间达标）
            "ceiling" — 触顶（数据源历史不足）
            其他值 — 翻页游标，保存到 checkpoint.cursor
            None — 无信号
        """
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
