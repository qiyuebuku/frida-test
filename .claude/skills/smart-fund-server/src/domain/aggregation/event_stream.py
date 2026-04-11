"""事件流聚合 (P0)

把"散点事件"聚合成"事件流"，识别正在发酵的行业趋势。

输入：ft_events（含 embedding，最近 24h）
输出：ft_event_streams（每行 = 一个事件流，记录 industry/state/momentum/event_ids）

聚类算法：
    按 industry 分组 → 组内基于 embedding 余弦相似度做贪心聚类
    阈值 0.78（默认）—— 同 industry 内相似度通常较高，阈值不宜太严

状态机：
    emerging:   1-2 条事件
    fermenting: 3-4 条
    peak:       >= 5 条 且 last_event_at < 1h
    decaying:   last_event_at 1h-3h
    closed:     last_event_at > 3h

momentum：
    rising:  最近 1h 事件数 > 前 1h 事件数
    stable:  持平 ±1
    falling: 显著减少

每次 tick 全量重建活跃 streams（state != closed），closed 的归档保留。
"""

import logging
import math
from datetime import datetime, timedelta, timezone

import psycopg2.extras

from src.domain.aggregation.base import BaseAggregator
from src.infrastructure.clients.embedding import decode_embedding
from src.infrastructure.db.fund_db import get_conn

logger = logging.getLogger(__name__)


# ==================== 配置 ====================

LOOKBACK_HOURS = 24       # 聚合最近 N 小时的事件
SIM_THRESHOLD = 0.78      # 同流相似度阈值
MIN_INDUSTRY_LEN = 1      # industry 字符串最小长度
MAX_EVENTS_PER_RUN = 500  # 单次最多处理多少条事件


# ==================== 工具函数 ====================


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na and nb else 0.0


def _classify_state(last_event_at: datetime, event_count: int, now: datetime) -> str:
    """根据事件数和最后时间判断 state

    时间窗口选 24h 因为：
    - 财经事件流通常持续 1-3 天发酵
    - 单条事件 4h 内未续发，仍可能在午盘/隔日续报
    - closed 阈值放到 24h 才算彻底结束
    """
    age = (now - last_event_at).total_seconds() / 3600  # hours
    if age > 24:
        return "closed"
    if age > 6:
        return "decaying"
    if event_count >= 5 and age <= 2:
        return "peak"
    if event_count >= 3:
        return "fermenting"
    return "emerging"


def _classify_momentum(events: list[dict], now: datetime) -> str:
    """根据最近 1h 和前 1h 的事件数对比判断 momentum"""
    one_hour_ago = now - timedelta(hours=1)
    two_hour_ago = now - timedelta(hours=2)
    recent = sum(1 for e in events if e["event_time"] >= one_hour_ago)
    prev = sum(1 for e in events if two_hour_ago <= e["event_time"] < one_hour_ago)
    if recent > prev + 1:
        return "rising"
    if prev > recent + 1:
        return "falling"
    return "stable"


# ==================== 数据库 ====================


def _query_recent_events(hours: int = LOOKBACK_HOURS, limit: int = MAX_EVENTS_PER_RUN) -> list[dict]:
    """读最近 N 小时的事件（含 embedding）"""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, title, summary, event_type, industries,
                       impact_strength, sentiment, event_time, embedding
                FROM ft_events
                WHERE event_time >= NOW() - INTERVAL '%s hours'
                  AND embedding IS NOT NULL
                ORDER BY event_time DESC
                LIMIT %s
                """,
                (hours, limit),
            )
            return [dict(r) for r in cur.fetchall()]


def _delete_active_streams() -> int:
    """删除所有非 closed 的 stream（每次 tick 重建）

    closed 的归档保留。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ft_event_streams WHERE state != 'closed'",
            )
            conn.commit()
            return cur.rowcount


def _upsert_stream(stream: dict) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ft_event_streams (
                    industry, state, momentum, event_ids, event_count,
                    avg_strength, max_strength, avg_sentiment,
                    first_event_at, last_event_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    stream["industry"][:64],
                    stream["state"],
                    stream["momentum"],
                    stream["event_ids"],
                    stream["event_count"],
                    stream["avg_strength"],
                    stream["max_strength"],
                    stream["avg_sentiment"],
                    stream["first_event_at"],
                    stream["last_event_at"],
                ),
            )
            conn.commit()
            return True


# ==================== 聚类算法 ====================


def _expand_events_by_industry(events: list[dict]) -> dict[str, list[dict]]:
    """把每条事件按 industry 展开（一条多行业事件 → 多条记录）"""
    import json
    by_industry: dict[str, list[dict]] = {}
    for ev in events:
        industries = ev.get("industries") or []
        # industries 可能是 JSONB list 或 JSON string
        if isinstance(industries, str):
            try:
                industries = json.loads(industries)
            except Exception:
                industries = []
        if not industries:
            industries = ["其他"]
        for ind in industries:
            if not ind or len(ind) < MIN_INDUSTRY_LEN:
                continue
            ind_key = ind.strip()[:64]
            by_industry.setdefault(ind_key, []).append(ev)
    return by_industry


def _greedy_cluster(events: list[dict], threshold: float = SIM_THRESHOLD) -> list[dict]:
    """同一 industry 内的事件做贪心聚类

    Returns: [{centroid, events: [...]}]
    """
    clusters: list[dict] = []
    for ev in events:
        vec = decode_embedding(ev.get("embedding"))
        if not vec:
            continue

        best_cluster = None
        best_sim = 0.0
        for c in clusters:
            sim = _cosine(vec, c["centroid"])
            if sim > best_sim:
                best_sim = sim
                best_cluster = c

        if best_cluster and best_sim >= threshold:
            # 加入现有 cluster，更新 centroid 为均值
            best_cluster["events"].append(ev)
            n = len(best_cluster["events"])
            old = best_cluster["centroid"]
            best_cluster["centroid"] = [(o * (n - 1) + v) / n for o, v in zip(old, vec)]
        else:
            clusters.append({"centroid": vec, "events": [ev]})
    return clusters


def _build_stream_record(industry: str, cluster: dict, now: datetime) -> dict:
    """从一个 cluster 构造 stream 行"""
    events = cluster["events"]
    event_ids = [e["id"] for e in events]
    times = [e["event_time"] for e in events]
    first_at = min(times)
    last_at = max(times)
    strengths = [float(e.get("impact_strength") or 0) for e in events]
    sentiments = [float(e.get("sentiment") or 0.5) for e in events]
    return {
        "industry": industry,
        "state": _classify_state(last_at, len(events), now),
        "momentum": _classify_momentum(events, now),
        "event_ids": event_ids,
        "event_count": len(events),
        "avg_strength": sum(strengths) / len(strengths) if strengths else 0,
        "max_strength": max(strengths) if strengths else 0,
        "avg_sentiment": sum(sentiments) / len(sentiments) if sentiments else 0.5,
        "first_event_at": first_at,
        "last_event_at": last_at,
    }


# ==================== 聚合器 ====================


class EventStreamAggregator(BaseAggregator):
    """事件流聚合器

    本聚合器与其他聚合器不同：
    - 不调外部 API，从 ft_events 读已抽取的事件
    - 不通过 SourceDef 框架
    - 输出到 ft_event_streams
    """

    data_domain = "event_stream"
    task_interval = 600  # 10 分钟

    def __init__(self):
        super().__init__()
        self.sources = []

    def _save(self, items: list[dict]) -> int:
        return 0

    def _get_checkpoint(self, source_name: str):
        return None

    async def tick(self):
        """主流程：读最近 24h 事件 → 按 industry 分组 → 组内贪心聚类 → 重建 streams"""
        events = _query_recent_events(LOOKBACK_HOURS, MAX_EVENTS_PER_RUN)
        if not events:
            logger.info("[event_stream] 无可聚合事件（需要 ft_events 有 embedding）")
            return

        logger.info(f"[event_stream] 待聚合 {len(events)} 条事件（最近 {LOOKBACK_HOURS}h）")

        # 1. 按 industry 展开
        by_industry = _expand_events_by_industry(events)
        logger.info(f"[event_stream] 涉及 {len(by_industry)} 个行业")

        # 2. 删除现有的 active streams（每次 tick 重建）
        deleted = _delete_active_streams()
        logger.info(f"[event_stream] 清理 {deleted} 条旧 active streams")

        # 3. 每个 industry 内贪心聚类
        now = datetime.now(timezone.utc)
        total_streams = 0
        active_streams = 0
        for industry, ind_events in by_industry.items():
            if len(ind_events) == 0:
                continue
            clusters = _greedy_cluster(ind_events)
            for cluster in clusters:
                if not cluster["events"]:
                    continue
                stream = _build_stream_record(industry, cluster, now)
                # 跳过 closed 状态（避免和已归档重复）
                if stream["state"] == "closed":
                    continue
                if _upsert_stream(stream):
                    total_streams += 1
                    if stream["state"] in ("emerging", "fermenting", "peak"):
                        active_streams += 1

        logger.info(
            f"[event_stream] 完成：写入 {total_streams} 条 streams，"
            f"其中活跃 {active_streams} 条"
        )
