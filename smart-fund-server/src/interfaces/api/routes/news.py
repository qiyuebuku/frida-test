"""新闻采集 API"""

from fastapi import APIRouter, Query

from src.interfaces.api.routes import _utils
from src.domain.collection.services.news import NewsAggregator, normalize_cls
from src.infrastructure.persistence.repositories import NewsRepositoryImpl

router = APIRouter(prefix="/api/news", tags=["新闻采集"])


@router.post("/crawl/cls", summary="触发财联社电报采集")
async def crawl_cls(rn: int = Query(20, description="采集条数")):
    """手动触发一次财联社电报采集，返回新入库数量"""
    items = await _utils.cls.get_telegraph_list(rn=rn)

    news_items = normalize_cls(items)
    new_ids = NewsAggregator().save_normalized_items(news_items)
    return {
        "fetched": len(items),
        "new": len(new_ids),
        "new_ids": new_ids,
        "duplicates": len(news_items) - len(new_ids),
    }


@router.get("/recent", summary="查询最近采集的新闻")
async def get_recent_news(
    source: str = Query(None, description="数据源过滤: cls/ths/eastmoney"),
    news_kind: str = Query(
        None,
        description="内容类型过滤: news/market_recap/market_preview/research_report",
    ),
    hours: int = Query(24, description="最近N小时"),
    limit: int = Query(50, description="返回条数"),
):
    rows = NewsRepositoryImpl().find_recent(
        source=source,
        news_kind=news_kind,
        hours=hours,
        limit=limit,
    )
    return {"count": len(rows), "data": rows}
