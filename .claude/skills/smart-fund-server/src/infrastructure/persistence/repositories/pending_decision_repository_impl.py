"""PendingDecisionRepository SQLAlchemy 实现"""
import json
import logging
from datetime import date

from sqlalchemy import and_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from src.domain.trading.repositories.pending_decision_repository import (
    PendingDecisionRepository,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.trading import PendingDecision

logger = logging.getLogger(__name__)


class PendingDecisionRepositoryImpl(PendingDecisionRepository):
    def insert_event_driven(self, decision: dict) -> bool:
        """ON CONFLICT (event_stream_id, fund_code, decision_date) WHERE source='event_driven'
        DO NOTHING — partial unique index 由 schema/99_indexes.sql 维护"""
        try:
            with get_session() as s:
                # 由于 partial unique 不能直接用 on_conflict_do_nothing(index_elements=...)
                # 我们用 RAW SQL 包裹,但通过 SQLAlchemy session 执行
                stmt = text("""
                    INSERT INTO ft_pending_decisions (
                        fund_code, fund_name, action, amount, sell_pct,
                        reason, confidence, market_view, risk_notes,
                        status, decision_source, dry_run,
                        event_stream_id, source_event_ids, score, score_breakdown
                    ) VALUES (
                        :fund_code, :fund_name, :action, :amount, :sell_pct,
                        :reason, :confidence, :market_view, :risk_notes,
                        'pending', 'event_driven', :dry_run,
                        :event_stream_id, :source_event_ids, :score, CAST(:score_breakdown AS jsonb)
                    )
                    ON CONFLICT (event_stream_id, fund_code, decision_date)
                    WHERE decision_source = 'event_driven'
                    DO NOTHING
                """)
                result = s.execute(stmt, {
                    "fund_code": decision["fund_code"],
                    "fund_name": decision.get("fund_name") or "",
                    "action": decision["action"],
                    "amount": decision.get("amount"),
                    "sell_pct": decision.get("sell_pct"),
                    "reason": decision.get("reason") or "",
                    "confidence": decision.get("confidence") or "medium",
                    "market_view": decision.get("market_view") or "",
                    "risk_notes": decision.get("risk_notes") or "",
                    "dry_run": decision.get("dry_run", True),
                    "event_stream_id": decision.get("event_stream_id"),
                    "source_event_ids": decision.get("source_event_ids") or [],
                    "score": decision.get("score"),
                    "score_breakdown": json.dumps(
                        decision.get("score_breakdown") or {}, ensure_ascii=False,
                    ),
                })
                return (result.rowcount or 0) > 0
        except Exception as e:
            logger.warning(f"insert_event_driven 失败: {e}")
            return False

    def insert_monitor_decision(self, decision: dict) -> bool:
        """monitor 决策 event_stream_id=NULL,幂等检查 (fund_code, action, today)"""
        try:
            with get_session() as s:
                # 先查 today 是否已有相同的 monitor 决策
                exists_q = s.execute(
                    text("""
                        SELECT 1 FROM ft_pending_decisions
                        WHERE fund_code = :fc
                          AND action = :ac
                          AND decision_date = CURRENT_DATE
                          AND decision_source = 'event_driven'
                          AND event_stream_id IS NULL
                        LIMIT 1
                    """),
                    {"fc": decision["fund_code"], "ac": decision["action"]},
                ).fetchone()
                if exists_q:
                    return False

                related = decision.get("related_stream_id")
                reason = decision.get("reason") or ""
                if related:
                    reason += f" [related stream#{related}]"

                s.execute(
                    text("""
                        INSERT INTO ft_pending_decisions (
                            fund_code, fund_name, action, amount, sell_pct,
                            reason, confidence, market_view, risk_notes,
                            status, decision_source, dry_run, event_stream_id
                        ) VALUES (
                            :fund_code, :fund_name, :action, :amount, :sell_pct,
                            :reason, 'high', '', :risk_notes,
                            'pending', 'event_driven', TRUE, NULL
                        )
                    """),
                    {
                        "fund_code": decision["fund_code"],
                        "fund_name": decision.get("fund_name") or "",
                        "action": decision["action"],
                        "amount": decision.get("amount"),
                        "sell_pct": decision.get("sell_pct"),
                        "reason": reason,
                        "risk_notes": decision.get("risk_notes") or "",
                    },
                )
                return True
        except Exception as e:
            logger.warning(f"insert_monitor_decision 失败: {e}")
            return False

    def find_pending_event_driven(self, limit: int = 20) -> list[dict]:
        with get_session() as s:
            rows = s.scalars(
                select(PendingDecision)
                .where(
                    PendingDecision.status == "pending",
                    PendingDecision.decision_source == "event_driven",
                    PendingDecision.action.in_(["buy", "sell"]),
                    PendingDecision.decision_date == date.today(),
                )
                .order_by(PendingDecision.score.desc().nullslast())
                .limit(limit)
            ).all()
            return [
                {
                    "id": r.id,
                    "fund_code": r.fund_code,
                    "fund_name": r.fund_name,
                    "action": r.action,
                    "amount": float(r.amount) if r.amount is not None else None,
                    "sell_pct": float(r.sell_pct) if r.sell_pct is not None else None,
                    "reason": r.reason,
                    "score": r.score,
                    "dry_run": r.dry_run,
                    "event_stream_id": r.event_stream_id,
                    "source_event_ids": r.source_event_ids,
                }
                for r in rows
            ]

    def mark_executed(self, decision_id: int) -> None:
        try:
            with get_session() as s:
                s.execute(
                    update(PendingDecision)
                    .where(PendingDecision.id == decision_id)
                    .values(status="executed", executed_at=func.now())
                )
        except Exception as e:
            logger.warning(f"mark_executed({decision_id}) 失败: {e}")

    def mark_cancelled(self, decision_id: int, reason: str) -> None:
        try:
            with get_session() as s:
                s.execute(
                    update(PendingDecision)
                    .where(PendingDecision.id == decision_id)
                    .values(
                        status="cancelled",
                        cancelled_at=func.now(),
                        cancel_reason=(reason or "")[:200],
                    )
                )
        except Exception as e:
            logger.warning(f"mark_cancelled({decision_id}) 失败: {e}")
