"""Business-oriented catalogue and live status for collection tasks."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import redis

from src.infrastructure.persistence.repositories import (
    CollectionRunRepository,
    MarketSnapshotRepository,
)
from src.infrastructure.clients.ths_native_stream import THS_NATIVE_STREAM_HEALTH_KEY
from src.infrastructure.config.settings import REDIS_URL
from src.infrastructure.persistence.repositories.jettask_schedule_repository import (
    JetTaskScheduleRepository,
)
from src.infrastructure.persistence.repositories.collection_state_repository_impl import (
    CollectionStateRepositoryImpl,
)


PUSH_TASKS = (
    ("push_cn_indices", "A股指数与市场宽度", "市场总览", ("ths_cn_index_quote", "ths_cn_market_summary", "ths_cn_market_breadth"), ()),
    ("push_market_context", "资金、情绪与跨市场指标", "市场总览", ("market_capital", "market_sentiment", "northbound_capital_current", "futures_intraday", "reverse_repo", "forex_intraday"), ()),
    ("push_market_events", "大盘与个股实时异动", "市场总览", ("market_anomaly",), ()),
    ("push_etf_quotes", "ETF 实时行情", "ETF", ("ths_etf_home_ranking",), ()),
    ("push_futures_hot", "期货热门主连实时行情", "期货", ("ths_futures_module",), ("hot_quotes_stream",)),
    ("push_gold_quotes", "黄金专区实时行情", "黄金", ("ths_gold_module",), ()),
    ("push_us_quotes", "美股实时行情", "美股", ("ths_us_security_quote", "ths_us_market_module"), ()),
)
CN_TIMEZONE = ZoneInfo("Asia/Shanghai")


def _module(name: str) -> str:
    if "sector" in name:
        return "板块市场"
    if "stock" in name or "market_events" in name:
        return "个股市场"
    if "etf" in name:
        return "ETF"
    if "futures" in name:
        return "期货"
    if "gold" in name:
        return "黄金"
    if "_us_" in name:
        return "美股"
    if any(token in name for token in ("market_", "fund_flow", "sentiment", "macro", "bond", "rate_liquidity", "cross_market")):
        return "市场总览"
    if "news" in name:
        return "新闻资讯"
    if "watchlist" in name:
        return "跟踪标的"
    return "数据维护"


def _source(name: str, description: str) -> str:
    if name.startswith("collect_news_ths") or "同花顺" in description:
        return "同花顺"
    if name.startswith("collect_news_cls"):
        return "财联社"
    if name.startswith("collect_news_pboc") or name.startswith("collect_macro_pboc"):
        return "中国人民银行"
    if name.startswith("collect_news_em_") or name.startswith("collect_fund_flow_dragon_tiger_em") or name.startswith("collect_macro_em_"):
        return "东方财富"
    if name.startswith("collect_news_sina"):
        return "新浪财经"
    if name.startswith("collect_news_xueqiu") or name.startswith("collect_sentiment_xueqiu"):
        return "雪球"
    if name.startswith("collect_sentiment_tencent"):
        return "腾讯财经"
    if name.startswith("collect_pboc_rate_liquidity"):
        return "中国人民银行"
    if name.startswith("collect_macro"):
        return "官方宏观数据"
    if any(token in name for token in ("market_daily_bars", "market_reference", "etf_daily_shares")):
        return "交易所 / 公共行情"
    if any(token in name for token in ("watchlist", "materialize_", "catchup")):
        return "系统内部"
    if "ths" in name or "同花顺" in description:
        return "同花顺"
    if "etf_daily_shares" in name:
        return "交易所"
    return "来源待识别"


def _channel(name: str, description: str) -> tuple[str, str]:
    if "etf_daily_shares" in name:
        return "http", "HTTP 拉取"
    if any(token in name for token in ("watchlist", "materialize_", "catchup")):
        return "internal", "内部处理"
    if name.startswith("collect_ths_index_sentiment"):
        return "http", "HTTP 拉取"
    if "ths" in name or "同花顺" in description:
        if any(token in name for token in ("zone", "rankings", "dynamic_groups", "sector_", "futures")):
            return "native_callback", "App 请求回调"
        return "app_http", "App HTTP"
    return "http", "HTTP 拉取"


def _task_name(schedule: Any) -> str:
    queue = str(getattr(schedule, "queue", "") or "")
    kwargs = dict(getattr(schedule, "kwargs", None) or {})
    if queue == "collect_collection_source":
        return f"collect_{kwargs.get('aggregator')}_{kwargs.get('source_name')}"
    if queue in {"collect_ths_sector_fragment", "collect_ths_sector_fragment_v2"}:
        key = "_".join(str(kwargs.get(key) or "") for key in ("kind", "classification", "metric")).strip("_")
        return f"collect_ths_sector_fragment_{key}"
    if queue in {"collect_ths_sector_signal_fragment", "collect_ths_sector_signal_fragment_v2"}:
        key = "_".join(str(kwargs.get(key) or "") for key in ("kind", "sector_type", "metric")).strip("_")
        return f"collect_ths_sector_signal_{key}"
    if queue == "collect_ths_futures_cycle":
        return "collect_ths_futures_cycle"
    return queue


def _period(schedule: Any) -> tuple[int | None, str]:
    seconds = getattr(schedule, "interval_seconds", None)
    if seconds:
        seconds = int(seconds)
        if seconds < 60:
            return seconds, f"每 {seconds} 秒"
        if seconds < 3600:
            return seconds, f"每 {seconds // 60} 分钟"
        return seconds, f"每 {seconds // 3600} 小时"
    cron = str(getattr(schedule, "cron_expression", "") or "")
    description = str(getattr(schedule, "description", "") or "")
    return None, description.rsplit("—", 1)[-1].strip() if "—" in description else f"Cron {cron}"


def _schedule_is_active(record: dict[str, Any], now: datetime) -> bool:
    windows = list(record.get("active_windows") or [])
    if not windows:
        return True
    timezone_name = str(record.get("schedule_timezone") or "UTC")
    local = now.astimezone(ZoneInfo(timezone_name))
    calendar = dict(record.get("calendar_config") or {})
    iso_date = local.date().isoformat()
    if iso_date in set(calendar.get("excluded_dates") or []):
        return False
    weekdays = set(calendar.get("weekdays") or [])
    weekday = local.isoweekday()
    if weekdays and weekday not in weekdays and iso_date not in set(calendar.get("included_dates") or []):
        return False
    current = local.hour * 60 + local.minute
    for window in windows:
        allowed_days = set(window.get("weekdays") or [])
        if allowed_days and weekday not in allowed_days:
            continue
        start_h, start_m = map(int, str(window["start"]).split(":"))
        end_h, end_m = map(int, str(window["end"]).split(":"))
        if start_h * 60 + start_m <= current <= end_h * 60 + end_m:
            return True
    return False


def _latest_time(
    rows: Iterable[dict[str, Any]],
    keys: tuple[str, ...],
    subjects: tuple[str, ...] = (),
) -> datetime | None:
    values = [
        row.get("fetched_at")
        for row in rows
        if row.get("data_type") in keys
        and (not subjects or row.get("subject_id") in subjects)
        and row.get("fetched_at")
    ]
    return max(values, default=None)


def _stream_connected() -> bool:
    client = redis.Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
    )
    try:
        return bool(client.get(THS_NATIVE_STREAM_HEALTH_KEY))
    except redis.RedisError:
        return False
    finally:
        client.close()


def _push_data_is_delayed(
    task_id: str,
    last_data_at: datetime | None,
    now: datetime,
    *,
    stream_connected: bool = True,
) -> bool:
    if task_id == "push_futures_hot":
        # This is a change-driven subscription, not a 30-second polling job.
        # An unchanged contract legitimately emits no event. Transport health
        # is the freshness signal; using snapshot age creates false alarms.
        return not stream_connected
    if last_data_at is None:
        return True
    local_now = now.astimezone(CN_TIMEZONE)
    local_data = last_data_at.astimezone(CN_TIMEZONE)
    if task_id in {"push_cn_indices", "push_market_context", "push_etf_quotes"}:
        # These feeds follow the A-share trading calendar.  A Saturday/Sunday
        # clock time such as 13:20 must not be interpreted as an active market
        # window merely because its hour falls between 13:00 and 15:00.
        if local_now.weekday() >= 5:
            return False
        # Once an A-share weekday has opened, yesterday's terminal snapshot is
        # no longer a valid current-day feed even if the transport is healthy.
        if (local_now.hour, local_now.minute) >= (9, 15):
            if local_data.date() != local_now.date():
                return True
        minute = local_now.hour * 60 + local_now.minute
        if (555 <= minute <= 690 or 780 <= minute <= 900):
            return (now - last_data_at).total_seconds() > 180
    return False


class CollectionTaskObservabilityService:
    def __init__(self, schedule_repository=None, state_repository=None) -> None:
        self._runs = CollectionRunRepository()
        self._snapshots = MarketSnapshotRepository()
        self._schedules = schedule_repository or JetTaskScheduleRepository()
        self._states = state_repository or CollectionStateRepositoryImpl()

    def catalogue(self, schedules: Iterable[Any] | None = None) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        schedule_rows = self._schedules.list_all() if schedules is None else []
        if schedules is None:
            schedules = [
                SimpleNamespace(
                    name=row["scheduler_id"], queue=row["queue_name"],
                    kwargs=row["task_kwargs"], interval_seconds=row["interval_seconds"],
                    cron_expression=row["cron_expression"], description=row["description"],
                    metadata=row["metadata"], tags=row["tags"], _record=row,
                )
                for row in schedule_rows
            ]
        runs = self._runs.list_latest(limit=1000)
        states = self._states.list_all()
        states_by_task = {
            str(row.get("task_id")): row
            for row in states
            if row.get("task_id")
        }
        states_by_source = {
            (str(row.get("aggregator") or ""), str(row.get("source_name") or "")): row
            for row in states
        }
        by_task: dict[str, list[dict[str, Any]]] = {}
        for row in runs:
            by_task.setdefault(str(row.get("task_name") or ""), []).append(row)
        items: list[dict[str, Any]] = []

        for schedule in schedules:
            schedule_name = str(getattr(schedule, "name", "") or "")
            task_name = _task_name(schedule)
            description = str(getattr(schedule, "description", "") or "")
            candidates = by_task.get(task_name, [])
            run = max(candidates, key=lambda row: row.get("started_at") or datetime.min.replace(tzinfo=timezone.utc), default={})
            run_source = str(run.get("source_name") or "")
            state = (
                states_by_task.get(task_name)
                or states_by_task.get(schedule_name)
                or states_by_source.get(("market_observation", run_source))
                or states_by_source.get(("internal", task_name))
                or {}
            )
            seconds, period_label = _period(schedule)
            channel, channel_label = _channel(schedule_name, description)
            metadata = dict(getattr(schedule, "metadata", None) or {})
            observation = dict(metadata.get("observability") or {})
            record = dict(getattr(schedule, "_record", None) or {})
            finished = run.get("finished_at")
            duration_ms = (
                round((finished - run["started_at"]).total_seconds() * 1000)
                if finished and run.get("started_at") else None
            )
            last_data_at = (
                state.get("last_persisted_at")
                or state.get("last_received_at")
                or run.get("source_time_max")
                or finished
            )
            age = (now - last_data_at).total_seconds() if last_data_at else None
            status = str(state.get("status") or run.get("status") or "pending")
            if (
                status in ("success", "partial_success")
                and seconds
                and age is not None
                and _schedule_is_active(record, now)
                and age > max(seconds * 3, 180)
            ):
                status = "delayed"
            items.append({
                "id": schedule_name,
                "task_name": task_name,
                "name": observation.get("display_name") or description.split("—", 1)[0].strip() or schedule_name,
                "source": observation.get("source") or _source(schedule_name, description),
                "module": observation.get("module") or _module(schedule_name),
                "category": observation.get("category") or ("行情快照" if any(token in schedule_name for token in ("market", "sector", "stock", "etf", "futures", "gold", "_us_", "bond")) else "资讯与基础数据"),
                "channel": observation.get("channel") or channel,
                "channel_label": observation.get("channel_label") or channel_label,
                "period_seconds": seconds,
                "period_label": period_label,
                "status": status,
                "last_data_at": last_data_at,
                "last_run_at": state.get("last_started_at") or run.get("started_at"),
                "duration_ms": state.get("last_duration_ms") or duration_ms,
                "fetched_count": state.get("last_fetched_count") if state else run.get("fetched_count"),
                "saved_count": state.get("last_saved_count") if state else run.get("saved_count"),
                "error_message": state.get("last_error") or run.get("error_message") or "",
                "enabled": record.get("enabled", True),
                "next_run_at": record.get("next_run_time"),
                "schedule_timezone": record.get("schedule_timezone") or "UTC",
                "active_windows": record.get("active_windows") or [],
                "calendar": record.get("calendar_config") or {},
                "tags": record.get("tags") or list(getattr(schedule, "tags", None) or []),
            })

        for state in states:
            if state.get("mode") != "backfill":
                continue
            aggregator = str(state.get("aggregator") or "")
            source_name = str(state.get("source_name") or "")
            synthetic_name = f"collect_{aggregator}_{source_name}"
            items.append({
                "id": f"backfill:{aggregator}:{source_name}",
                "task_name": "advance_collection_backfill",
                "name": f"{source_name} 历史回填",
                "source": _source(synthetic_name, ""),
                "module": _module(synthetic_name),
                "category": "临时回填",
                "channel": "backfill",
                "channel_label": "链式延迟任务",
                "period_seconds": None,
                "period_label": f"推进至 {state.get('target_time') or '-'}",
                "status": "running" if not state.get("last_error") else "failed",
                "last_data_at": state.get("oldest_time"),
                "last_run_at": state.get("last_run_at"),
                "duration_ms": None,
                "fetched_count": None,
                "saved_count": state.get("total_saved"),
                "error_message": state.get("last_error") or "",
                "enabled": state.get("enabled", True),
                "next_run_at": None,
                "backfill": {"target_time": state.get("target_time"), "cursor": state.get("cursor"), "oldest_time": state.get("oldest_time"), "status": state.get("backfill_status")},
            })

        stream_connected = _stream_connected()
        for task_id, name, module, data_types, subject_ids in PUSH_TASKS:
            state = states_by_task.get(task_id) or {}
            last_data_at = state.get("last_persisted_at") or state.get("last_received_at")
            data_delayed = bool(last_data_at) and _push_data_is_delayed(
                task_id,
                last_data_at,
                now,
                stream_connected=stream_connected,
            )
            items.append({
                "id": task_id,
                "task_name": task_id,
                "name": name,
                "source": "同花顺",
                "module": module,
                "category": "行情快照",
                "channel": "push",
                "channel_label": "服务端主动推送",
                "period_seconds": None,
                "period_label": "持续订阅 · 数据变化即推送",
                "status": "delayed" if data_delayed else str(state.get("status") or "pending"),
                "connection_status": (
                    "connected"
                    if stream_connected
                    else (state.get("runtime_details") or {}).get(
                        "connection_status", "disconnected"
                    )
                ),
                "last_data_at": last_data_at,
                "last_run_at": None,
                "duration_ms": None,
                "fetched_count": state.get("last_fetched_count"),
                "saved_count": state.get("last_saved_count"),
                "error_message": state.get("last_error") or "",
            })
        return {"generated_at": now, "count": len(items), "items": items}
