"""IndustryMappingRepository SQLAlchemy 实现"""
import json
import logging

from sqlalchemy import select, text

from src.domain.trading.repositories.industry_mapping_repository import (
    IndustryMappingRepository,
)
from src.infrastructure.connections import get_session
from src.infrastructure.persistence.models.trading import (
    IndexFund, IndustryIndex, IndustryMapping,
)

logger = logging.getLogger(__name__)


class IndustryMappingRepositoryImpl(IndustryMappingRepository):
    def resolve_industry_to_funds(self, industry_name: str) -> list[dict]:
        try:
            with get_session() as s:
                # 1. 精确匹配 industry_name 或 aliases (JSONB contains)
                mapping = s.execute(
                    text("""
                        SELECT industry_id, industry_name, priority
                        FROM ft_industry_mapping
                        WHERE industry_name = :name
                           OR aliases @> CAST(:name_arr AS jsonb)
                        LIMIT 1
                    """),
                    {"name": industry_name, "name_arr": json.dumps([industry_name], ensure_ascii=False)},
                ).fetchone()

                if not mapping:
                    # 2. keywords 模糊匹配
                    mapping = s.execute(
                        text("""
                            SELECT industry_id, industry_name, priority
                            FROM ft_industry_mapping
                            WHERE keywords @> CAST(:name_arr AS jsonb)
                            ORDER BY priority DESC
                            LIMIT 1
                        """),
                        {"name_arr": json.dumps([industry_name], ensure_ascii=False)},
                    ).fetchone()

                if not mapping:
                    return []

                industry_id = mapping[0]
                priority = float(mapping[2] or 0.5)

                # 3. industry_id → index_code → fund_code
                rows = s.execute(
                    text("""
                        SELECT f.fund_code, f.fund_name, f.fund_type, f.fee_rate, f.liquidity_score,
                               i.index_code, i.index_name, i.weight
                        FROM ft_industry_index i
                        JOIN ft_index_fund f USING (index_code)
                        WHERE i.industry_id = :iid
                        ORDER BY f.liquidity_score DESC, f.fee_rate ASC
                    """),
                    {"iid": industry_id},
                ).fetchall()
                return [
                    {
                        "fund_code": r[0],
                        "fund_name": r[1],
                        "fund_type": r[2],
                        "fee_rate": float(r[3] or 0),
                        "liquidity_score": float(r[4] or 0),
                        "index_code": r[5],
                        "index_name": r[6],
                        "weight": float(r[7] or 1),
                        "industry_priority": priority,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"resolve_industry_to_funds({industry_name}) 失败: {e}")
            return []

    def fund_to_industry_keywords(self, fund_code: str) -> list[str]:
        try:
            with get_session() as s:
                rows = s.execute(
                    select(IndustryMapping)
                    .join(IndustryIndex, IndustryMapping.industry_id == IndustryIndex.industry_id)
                    .join(IndexFund, IndexFund.index_code == IndustryIndex.index_code)
                    .where(IndexFund.fund_code == fund_code)
                ).scalars().all()

                names: set[str] = set()
                for r in rows:
                    names.add(r.industry_name)
                    for alias in r.aliases or []:
                        names.add(alias)
                    for kw in r.keywords or []:
                        names.add(kw)
                return list(names)
        except Exception as e:
            logger.warning(f"fund_to_industry_keywords({fund_code}) 失败: {e}")
            return []
