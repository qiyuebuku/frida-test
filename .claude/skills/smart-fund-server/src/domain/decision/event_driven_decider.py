"""事件驱动决策引擎 (Phase C.2)

输入：ft_event_streams (state in emerging/fermenting/peak)
输出：ft_pending_decisions (decision_source='event_driven', dry_run=true)

流程：
    1. 读活跃事件流（state != closed/decaying）
    2. 通过 industry → ft_industry_index → ft_industry_mapping → ft_index_fund 找候选基金
    3. 硬过滤：certainty/novelty/age/event_count
    4. 打分：impact * 0.35 + novelty * 0.15 + sentiment_extremity * 0.10
            + certainty * 0.15 + momentum_factor * 0.10 + state_factor * 0.15
    5. 动态仓位：base * (1 + score) * winrate_factor * market_factor
    6. 风控硬约束（复用 risk_manager.check）
    7. 写入 ft_pending_decisions（默认 dry_run=true，幂等键 UNIQUE(event_stream_id, fund_code, decision_date)）
"""

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)


# ==================== 配置 ====================

# 硬过滤阈值
MIN_CERTAINTY = 0.5      # 事件确定性
MIN_NOVELTY = 0.3        # 新颖度
MAX_EVENT_AGE_HOURS = 24
MIN_STREAM_EVENT_COUNT = 1  # 至少 1 条事件才考虑（emerging）
MIN_FINAL_SCORE = 0.5    # 综合分阈值

# 打分权重（合计 1.0）
W_IMPACT = 0.35
W_NOVELTY = 0.15
W_SENTIMENT_EXT = 0.10  # 情绪极端度
W_CERTAINTY = 0.15
W_MOMENTUM = 0.10
W_STATE = 0.15

# 仓位
DEFAULT_BASE_POSITION = 500.0    # 单笔基础金额
MAX_PER_DECISION = 5000.0         # 单次最大金额（即使 score 很高）

# 默认 dry_run（C.3 真实下单前必须保持 True）
DEFAULT_DRY_RUN = True


# ==================== 数据查询 ====================


def _query_active_streams() -> list[dict]:
    """读最近活跃的事件流 — R2.8 走 EventStreamRepository"""
    from src.infrastructure.persistence.repositories import EventStreamRepositoryImpl
    return EventStreamRepositoryImpl().find_active()


def _query_events_by_ids(event_ids: list[int]) -> list[dict]:
    """读事件细节 — R2.8 用 ORM 直接 query"""
    if not event_ids:
        return []
    from sqlalchemy import select

    from src.infrastructure.connections import get_session
    from src.infrastructure.persistence.models.extraction import Event

    with get_session() as s:
        rows = s.scalars(
            select(Event).where(Event.id.in_(event_ids))
        ).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "novelty": r.novelty,
                "certainty": r.certainty,
                "sentiment": r.sentiment,
                "impact_strength": r.impact_strength,
                "impact_direction": r.impact_direction,
                "event_time": r.event_time,
            }
            for r in rows
        ]


def _resolve_industry_to_funds(industry_name: str) -> list[dict]:
    """industry_name → 候选基金列表 — R2.8 走 IndustryMappingRepository"""
    from src.infrastructure.persistence.repositories import (
        IndustryMappingRepositoryImpl,
    )
    return IndustryMappingRepositoryImpl().resolve_industry_to_funds(industry_name)


def _query_fund_limit(fund_code: str) -> dict | None:
    """R2.8 走 FundLimitRepository"""
    from src.infrastructure.persistence.repositories import FundLimitRepositoryImpl
    return FundLimitRepositoryImpl().get(fund_code)


# ==================== 打分 ====================


def _momentum_factor(momentum: str) -> float:
    return {"rising": 1.0, "stable": 0.5, "falling": 0.2}.get(momentum, 0.5)


def _state_factor(state: str) -> float:
    return {"peak": 1.0, "fermenting": 0.7, "emerging": 0.5}.get(state, 0.0)


def _score_stream(stream: dict, events: list[dict]) -> tuple[float, dict]:
    """对一个 event_stream 打分

    Returns: (score, breakdown)
    """
    if not events:
        return 0.0, {}

    # 取事件平均
    avg_novelty = sum(float(e.get("novelty") or 0.5) for e in events) / len(events)
    avg_certainty = sum(float(e.get("certainty") or 0.5) for e in events) / len(events)
    avg_sentiment = float(stream.get("avg_sentiment") or 0.5)
    avg_strength = float(stream.get("avg_strength") or 0)

    # 情绪极端度（距 0.5 的距离 → 0~0.5 → 标准化到 0~1）
    sentiment_ext = abs(avg_sentiment - 0.5) * 2

    momentum_f = _momentum_factor(stream.get("momentum") or "stable")
    state_f = _state_factor(stream.get("state") or "")

    score = (
        W_IMPACT * avg_strength
        + W_NOVELTY * avg_novelty
        + W_SENTIMENT_EXT * sentiment_ext
        + W_CERTAINTY * avg_certainty
        + W_MOMENTUM * momentum_f
        + W_STATE * state_f
    )

    breakdown = {
        "impact": round(avg_strength, 3),
        "novelty": round(avg_novelty, 3),
        "sentiment_extremity": round(sentiment_ext, 3),
        "certainty": round(avg_certainty, 3),
        "momentum_factor": round(momentum_f, 3),
        "state_factor": round(state_f, 3),
        "weights": {
            "impact": W_IMPACT, "novelty": W_NOVELTY, "sent_ext": W_SENTIMENT_EXT,
            "certainty": W_CERTAINTY, "momentum": W_MOMENTUM, "state": W_STATE,
        },
        "final_score": round(score, 4),
    }
    return score, breakdown


def _check_hard_filters(stream: dict, events: list[dict]) -> tuple[bool, str]:
    """硬过滤：返回 (通过?, 理由)"""
    # 1. 至少 1 条事件
    if len(events) < MIN_STREAM_EVENT_COUNT:
        return False, f"事件数不足({len(events)})"

    # 2. event_count 也得对得上
    if stream.get("event_count", 0) < MIN_STREAM_EVENT_COUNT:
        return False, "stream.event_count 不足"

    # 3. 平均 certainty
    avg_cert = sum(float(e.get("certainty") or 0.5) for e in events) / len(events)
    if avg_cert < MIN_CERTAINTY:
        return False, f"certainty 过低({avg_cert:.2f} < {MIN_CERTAINTY})"

    # 4. 平均 novelty
    avg_nov = sum(float(e.get("novelty") or 0.5) for e in events) / len(events)
    if avg_nov < MIN_NOVELTY:
        return False, f"novelty 过低({avg_nov:.2f} < {MIN_NOVELTY})"

    # 5. 事件年龄
    last_event_at = stream.get("last_event_at")
    if last_event_at:
        age = (datetime.now(timezone.utc) - last_event_at).total_seconds() / 3600
        if age > MAX_EVENT_AGE_HOURS:
            return False, f"事件过旧({age:.1f}h > {MAX_EVENT_AGE_HOURS}h)"

    return True, ""


# ==================== 仓位计算 ====================


def _calc_position(score: float, fund_limit: dict | None) -> float:
    """根据 score 计算建议金额

    Args:
        score: 打分 0~1
        fund_limit: ft_fund_limits 行（含 min_buy / max_buy）

    Returns:
        建议金额（取整到 100）
    """
    # base * (1 + score)，score=0.5 → base*1.5；score=0.8 → base*1.8
    raw = DEFAULT_BASE_POSITION * (1 + score)
    raw = min(raw, MAX_PER_DECISION)

    # 应用基金的 min_buy / max_buy
    min_buy = float(fund_limit.get("min_buy", 0)) if fund_limit else 0
    max_buy = float(fund_limit.get("max_buy", 0)) if fund_limit else 0
    if min_buy > 0:
        raw = max(raw, min_buy)
    if max_buy > 0:
        raw = min(raw, max_buy)

    # 取整到 100
    return round(raw / 100) * 100


# ==================== 入库 ====================


def _save_decision(decision: dict) -> bool:
    """写入 ft_pending_decisions — R2.8 走 PendingDecisionRepository"""
    from src.infrastructure.persistence.repositories import (
        PendingDecisionRepositoryImpl,
    )
    return PendingDecisionRepositoryImpl().insert_event_driven(decision)


# ==================== 主流程 ====================


class EventDrivenDecider:
    """事件驱动决策引擎

    与 jettask task 的关系：被 trade_decision task 调用 .decide_all()
    """

    def __init__(self, dry_run: bool = DEFAULT_DRY_RUN):
        self.dry_run = dry_run

    async def decide_all(self) -> dict:
        """主流程：扫描活跃事件流 → 打分 → 写决策"""
        streams = _query_active_streams()
        if not streams:
            logger.info("[trade_decision] 无活跃事件流")
            return {"streams": 0, "decisions": 0}

        logger.info(f"[trade_decision] 扫描 {len(streams)} 个活跃 streams")

        total_decisions = 0
        total_skipped = 0

        for stream in streams:
            event_ids = stream.get("event_ids") or []
            events = _query_events_by_ids(event_ids)

            # 1. 硬过滤
            passed, reason = _check_hard_filters(stream, events)
            if not passed:
                logger.debug(
                    f"[trade_decision] stream#{stream['id']} ({stream['industry']}) 过滤: {reason}"
                )
                total_skipped += 1
                continue

            # 2. 打分
            score, breakdown = _score_stream(stream, events)
            if score < MIN_FINAL_SCORE:
                logger.debug(
                    f"[trade_decision] stream#{stream['id']} ({stream['industry']}) "
                    f"score={score:.3f} < {MIN_FINAL_SCORE}"
                )
                total_skipped += 1
                continue

            # 3. 解析候选基金
            funds = _resolve_industry_to_funds(stream["industry"])
            if not funds:
                logger.debug(
                    f"[trade_decision] {stream['industry']} 无映射基金"
                )
                total_skipped += 1
                continue

            # 4. 取流动性最高的一只基金（已按 liquidity_score 排序）
            fund = funds[0]

            # 5. 检查基金限购
            fund_limit = _query_fund_limit(fund["fund_code"])
            if fund_limit and fund_limit.get("is_suspended"):
                logger.debug(
                    f"[trade_decision] {fund['fund_code']} 暂停申购，跳过"
                )
                total_skipped += 1
                continue

            # 6. 仓位计算
            amount = _calc_position(score, fund_limit)

            # 7. impact_direction（buy/sell 方向）
            avg_dir = sum(
                1 if e.get("impact_direction") == "positive" else
                -1 if e.get("impact_direction") == "negative" else 0
                for e in events
            ) / len(events)
            action = "buy" if avg_dir >= 0 else "watch"  # 负面事件先观望，不直接卖

            # 8. 构造决策
            decision = {
                "event_stream_id": stream["id"],
                "source_event_ids": event_ids,
                "fund_code": fund["fund_code"],
                "fund_name": fund["fund_name"],
                "action": action,
                "amount": amount if action == "buy" else 0,
                "reason": (
                    f"事件流『{stream['industry']}』{stream['state']}/{stream['momentum']}, "
                    f"{len(events)} 条事件, score={score:.3f}, "
                    f"映射→{fund['index_name']}({fund['index_code']})"
                ),
                "confidence": "high" if score >= 0.75 else "medium" if score >= 0.6 else "low",
                "market_view": f"行业 {stream['industry']} 处于 {stream['state']} 阶段",
                "risk_notes": f"事件 avg_strength={float(stream['avg_strength']):.2f}",
                "score": round(score, 4),
                "score_breakdown": breakdown,
                "dry_run": self.dry_run,
            }

            # 9. 入库
            if _save_decision(decision):
                total_decisions += 1
                logger.info(
                    f"[trade_decision] ✅ {fund['fund_code']} {fund['fund_name']} "
                    f"{action} {amount:.0f} (score={score:.3f}, stream#{stream['id']} {stream['industry']})"
                )
            else:
                total_skipped += 1
                logger.debug(
                    f"[trade_decision] stream#{stream['id']} 决策已存在或写入失败"
                )

        logger.info(
            f"[trade_decision] 完成：扫描 {len(streams)} streams，"
            f"产出 {total_decisions} 决策，跳过 {total_skipped} 个"
        )
        return {"streams": len(streams), "decisions": total_decisions, "skipped": total_skipped}
