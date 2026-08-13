"""受控历史回填状态编排。"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass
from datetime import date
from typing import Callable

from src.domain.collection.services.fund_flow import FundFlowAggregator
from src.domain.collection.services.macro import MacroAggregator
from src.domain.collection.services.market import MarketAggregator
from src.domain.collection.services.news import NewsAggregator
from src.domain.collection.services.sentiment import SentimentAggregator
from src.infrastructure.db import redis_lock
from src.infrastructure.persistence.repositories.collection_state_repository_impl import (
    CollectionStateRepositoryImpl,
)
from src.infrastructure.time_utils import app_today


class CollectionBackfillError(ValueError):
    """回填请求无法安全执行。"""


@dataclass(frozen=True)
class CollectionBackfillResult:
    aggregator: str
    source_name: str
    target_time: str
    status: str
    changed: bool
    dry_run: bool
    queue: str
    previous_mode: str
    previous_target_time: str | None
    newest_time: str | None
    oldest_time: str | None
    cursor_preserved: bool
    warning: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


AGGREGATOR_CLASSES = {
    "news": NewsAggregator,
    "fund_flow": FundFlowAggregator,
    "market": MarketAggregator,
    "sentiment": SentimentAggregator,
    "macro": MacroAggregator,
}

COLLECTION_QUEUE_BY_AGGREGATOR = {
    "news": "collect_collection_source",
    "fund_flow": "collect_collection_source",
    "market": "collect_market",
    "sentiment": "collect_collection_source",
    "macro": "collect_collection_source",
}


def _parse_date(value: str | date | None, *, field_name: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise CollectionBackfillError(
            f"{field_name} 必须是 YYYY-MM-DD，实际为 {value!r}"
        ) from exc


class CollectionBackfillService:
    """只调整单个 source checkpoint，不执行网络抓取。"""

    def __init__(
        self,
        repository=None,
        lock_acquire: Callable[..., AbstractContextManager] | None = None,
    ):
        self._repository = repository or CollectionStateRepositoryImpl()
        self._lock_acquire = lock_acquire or redis_lock.acquire

    def list_capabilities(self, aggregator: str | None = None) -> list[dict]:
        if aggregator and aggregator not in AGGREGATOR_CLASSES:
            raise CollectionBackfillError(
                f"未知 aggregator={aggregator!r}，可选: {', '.join(sorted(AGGREGATOR_CLASSES))}"
            )

        state_map = {
            (item["aggregator"], item["source_name"]): item
            for item in self._repository.list_all(aggregator)
        }
        rows: list[dict] = []
        for name, agg_class in AGGREGATOR_CLASSES.items():
            if aggregator and name != aggregator:
                continue
            supported = set(agg_class.BACKFILL_SOURCES)
            for source_name, config in agg_class.SOURCE_CONFIGS.items():
                state = state_map.get((name, source_name)) or {}
                rows.append(
                    {
                        "aggregator": name,
                        "source_name": source_name,
                        "supported": source_name in supported,
                        "configured_target_days": int(config.get("target_days") or 0),
                        "mode": state.get("mode"),
                        "target_time": state.get("target_time"),
                        "oldest_time": state.get("oldest_time"),
                        "newest_time": state.get("newest_time"),
                        "enabled": state.get("enabled"),
                    }
                )
        return rows

    def prepare(
        self,
        *,
        aggregator: str,
        source_name: str,
        start_date: str,
        dry_run: bool = False,
    ) -> CollectionBackfillResult:
        target_date = _parse_date(start_date, field_name="start_date")
        if target_date is None:
            raise CollectionBackfillError("start_date 不能为空")
        if target_date >= app_today():
            raise CollectionBackfillError(
                f"历史回填日期必须早于今天 {app_today().isoformat()}"
            )

        agg_class = AGGREGATOR_CLASSES.get(aggregator)
        if agg_class is None:
            raise CollectionBackfillError(
                f"未知 aggregator={aggregator!r}，可选: {', '.join(sorted(AGGREGATOR_CLASSES))}"
            )
        if source_name not in agg_class.SOURCE_CONFIGS:
            raise CollectionBackfillError(
                f"{aggregator} 不存在 source={source_name!r}"
            )
        if source_name not in agg_class.BACKFILL_SOURCES:
            raise CollectionBackfillError(
                f"{aggregator}:{source_name} 是快照型或不具备受控历史接口，不能执行回填"
            )

        initial_state = self._require_state(aggregator, source_name)
        initial_result, _ = self._evaluate(
            aggregator=aggregator,
            source_name=source_name,
            target_date=target_date,
            state=initial_state,
            dry_run=dry_run,
        )
        if dry_run or not initial_result.changed:
            return initial_result

        lock_name = f"{aggregator}:{source_name}"
        with self._lock_acquire(lock_name, ttl=30) as lock:
            if not lock:
                raise CollectionBackfillError(
                    f"{aggregator}:{source_name} 正在采集，未修改 checkpoint；稍后重试"
                )

            current_state = self._require_state(aggregator, source_name)
            result, cursor = self._evaluate(
                aggregator=aggregator,
                source_name=source_name,
                target_date=target_date,
                state=current_state,
                dry_run=False,
            )
            if not result.changed:
                return result

            updated = self._repository.arm_backfill(
                aggregator,
                source_name,
                target_date.isoformat(),
                cursor=cursor,
            )
            if not updated:
                raise CollectionBackfillError(
                    f"{aggregator}:{source_name} checkpoint 更新失败"
                )
            return result

    def _require_state(self, aggregator: str, source_name: str) -> dict:
        state = self._repository.get(aggregator, source_name)
        if state is None:
            raise CollectionBackfillError(
                f"ft_collection_state 缺少 {aggregator}:{source_name}，请先执行 init state"
            )
        if state.get("enabled") is False:
            raise CollectionBackfillError(
                f"{aggregator}:{source_name} 当前已禁用，请先显式启用"
            )
        return state

    def _evaluate(
        self,
        *,
        aggregator: str,
        source_name: str,
        target_date: date,
        state: dict,
        dry_run: bool,
    ) -> tuple[CollectionBackfillResult, object]:
        oldest_date = _parse_date(state.get("oldest_time"), field_name="oldest_time")
        existing_target = _parse_date(state.get("target_time"), field_name="target_time")
        mode = str(state.get("mode") or "incremental")
        queue = COLLECTION_QUEUE_BY_AGGREGATOR[aggregator]
        config = AGGREGATOR_CLASSES[aggregator].SOURCE_CONFIGS[source_name]
        configured_days = int(config.get("target_days") or 0)
        requested_days = (app_today() - target_date).days
        warning = None
        if configured_days and requested_days > configured_days:
            warning = (
                f"请求回填 {requested_days} 天，超过该源配置覆盖 {configured_days} 天；"
                "数据源达到历史上限时会自动以 ceiling 结束"
            )

        base = {
            "aggregator": aggregator,
            "source_name": source_name,
            "target_time": target_date.isoformat(),
            "dry_run": dry_run,
            "queue": queue,
            "previous_mode": mode,
            "previous_target_time": state.get("target_time"),
            "newest_time": state.get("newest_time"),
            "oldest_time": state.get("oldest_time"),
            "warning": warning,
        }

        if oldest_date and oldest_date <= target_date:
            return (
                CollectionBackfillResult(
                    **base,
                    status="already_covered",
                    changed=False,
                    cursor_preserved=False,
                ),
                None,
            )

        if mode == "backfill" and existing_target and existing_target <= target_date:
            return (
                CollectionBackfillResult(
                    **base,
                    status="already_armed",
                    changed=False,
                    cursor_preserved=state.get("cursor") is not None,
                ),
                state.get("cursor"),
            )

        preserve_cursor = mode == "backfill" and state.get("cursor") is not None
        cursor = state.get("cursor") if preserve_cursor else None
        return (
            CollectionBackfillResult(
                **base,
                status="preview" if dry_run else "armed",
                changed=True,
                cursor_preserved=preserve_cursor,
            ),
            cursor,
        )
