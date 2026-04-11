"""复盘应用服务

1 个 use case:
- run_review: 扫描历史 trade,拿 ETF 净值,计算 T+1/T+2 收益,
  写 ft_reviews,刷新胜率统计 (review_decision task,盘后 16:00)
"""
import logging

from src.application.dto.reflection_dto import ReviewResult

logger = logging.getLogger(__name__)


class ReflectionAppService:
    """复盘 use case 入口"""

    async def run_review(self) -> ReviewResult:
        from src.domain.reflection.services.review_engine import ReviewEngine
        result = await ReviewEngine().review_all() or {}
        return ReviewResult(
            trades=result.get("trades", 0),
            reviewed=result.get("reviewed", 0),
            skipped=result.get("skipped", 0),
            winrate_stats=result.get("winrate_stats", {}),
        )
