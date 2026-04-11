"""ReviewRepository SQLAlchemy 实现"""
import logging
from datetime import date, timedelta

from sqlalchemy import text

from src.domain.reflection.repositories.review_repository import ReviewRepository
from src.infrastructure.connections import get_session

logger = logging.getLogger(__name__)


class ReviewRepositoryImpl(ReviewRepository):
    def find_unreviewed_trades(self, lookback_days: int = 5) -> list[dict]:
        since = (date.today() - timedelta(days=lookback_days)).isoformat()
        try:
            with get_session() as s:
                rows = s.execute(
                    text("""
                        SELECT t.id, t.fund_code, t.fund_name, t.action, t.amount, t.shares,
                               t.dry_run, t.decision_id, t.trade_date, t.reason
                        FROM ft_trades t
                        LEFT JOIN ft_reviews r
                          ON r.decision_id = t.id
                          AND r.decision_source = 'event_driven'
                        WHERE t.trade_date >= :since
                          AND t.trade_date <= CURRENT_DATE - INTERVAL '1 day'
                          AND r.id IS NULL
                        ORDER BY t.trade_date ASC
                    """),
                    {"since": since},
                ).fetchall()
                return [
                    {
                        "id": r[0],
                        "fund_code": r[1],
                        "fund_name": r[2],
                        "action": r[3],
                        "amount": float(r[4]) if r[4] is not None else None,
                        "shares": float(r[5]) if r[5] is not None else None,
                        "dry_run": r[6],
                        "decision_id": r[7],
                        "trade_date": r[8],
                        "reason": r[9],
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"find_unreviewed_trades 失败: {e}")
            return []

    def upsert_review(self, review: dict) -> bool:
        try:
            with get_session() as s:
                s.execute(
                    text("""
                        INSERT INTO ft_reviews (
                            decision_id, fund_code, decision_date,
                            decision_action, decision_reason,
                            nav_at_decision, nav_t1, nav_t2,
                            change_t1_pct, change_t2_pct,
                            outcome, review_notes,
                            decision_source, dry_run
                        ) VALUES (
                            :decision_id, :fund_code, :decision_date,
                            :decision_action, :decision_reason,
                            :nav_at_decision, :nav_t1, :nav_t2,
                            :change_t1_pct, :change_t2_pct,
                            :outcome, :review_notes,
                            :decision_source, :dry_run
                        )
                        ON CONFLICT (decision_id, fund_code) WHERE decision_id IS NOT NULL
                        DO UPDATE SET
                            nav_t1 = EXCLUDED.nav_t1,
                            nav_t2 = EXCLUDED.nav_t2,
                            change_t1_pct = EXCLUDED.change_t1_pct,
                            change_t2_pct = EXCLUDED.change_t2_pct,
                            outcome = EXCLUDED.outcome,
                            reviewed_at = NOW()
                    """),
                    {
                        "decision_id": review["decision_id"],
                        "fund_code": review["fund_code"],
                        "decision_date": review["decision_date"],
                        "decision_action": review["decision_action"],
                        "decision_reason": review.get("decision_reason", ""),
                        "nav_at_decision": review.get("nav_at_decision"),
                        "nav_t1": review.get("nav_t1"),
                        "nav_t2": review.get("nav_t2"),
                        "change_t1_pct": review.get("change_t1_pct"),
                        "change_t2_pct": review.get("change_t2_pct"),
                        "outcome": review["outcome"],
                        "review_notes": review.get("review_notes", ""),
                        "decision_source": review.get("decision_source", "event_driven"),
                        "dry_run": review.get("dry_run", True),
                    },
                )
                return True
        except Exception as e:
            logger.warning(f"upsert_review 失败: {e}")
            return False

    def winrate_stats_30d(self) -> dict:
        try:
            with get_session() as s:
                row = s.execute(
                    text("""
                        SELECT
                            COUNT(*) AS total,
                            COUNT(*) FILTER (WHERE outcome='correct') AS correct,
                            COUNT(*) FILTER (WHERE outcome='wrong') AS wrong,
                            COUNT(*) FILTER (WHERE outcome='neutral') AS neutral
                        FROM ft_reviews
                        WHERE decision_date >= CURRENT_DATE - INTERVAL '30 days'
                          AND outcome != 'pending'
                          AND decision_source = 'event_driven'
                    """)
                ).fetchone()
                if not row:
                    return {"total": 0, "correct": 0, "wrong": 0, "neutral": 0, "winrate": 0.5}
                total, correct, wrong, neutral = row
                decided = (correct or 0) + (wrong or 0)
                winrate = correct / decided if decided > 0 else 0.5
                return {
                    "total": total or 0,
                    "correct": correct or 0,
                    "wrong": wrong or 0,
                    "neutral": neutral or 0,
                    "winrate": round(winrate, 3),
                }
        except Exception as e:
            logger.warning(f"winrate_stats_30d 失败: {e}")
            return {"total": 0, "correct": 0, "wrong": 0, "neutral": 0, "winrate": 0.5}
