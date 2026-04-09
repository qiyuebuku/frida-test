"""新闻采集 API"""

import hashlib

from fastapi import APIRouter, Query

import routers._utils as _utils
from services.db import fund_db

router = APIRouter(prefix="/api/news", tags=["新闻采集"])


@router.post("/crawl/cls", summary="触发财联社快讯采集")
async def crawl_cls(rn: int = Query(20, description="采集条数")):
    """手动触发一次财联社快讯采集，返回新入库数量"""
    items = await _utils.cls.get_telegraph_list(rn=rn)

    news_items = []
    for item in items:
        title = item.get("title", "")
        if not title:
            continue
        subjects = item.get("subjects") or []
        news_items.append({
            "title": title,
            "content": item.get("content", ""),
            "source": "cls",
            "category": _classify_cls(item),
            "url": item.get("shareurl", ""),
            "tags": [s.get("subject_name") for s in subjects if s.get("subject_name")],
            "fingerprint": hashlib.sha256(f"{title}|cls".encode()).hexdigest(),
            "published_at": item.get("ctime"),
        })

    new_ids = fund_db.save_news_batch(news_items)
    return {
        "fetched": len(items),
        "new": len(new_ids),
        "new_ids": new_ids,
        "duplicates": len(news_items) - len(new_ids),
    }


@router.get("/recent", summary="查询最近采集的新闻")
async def get_recent_news(
    source: str = Query(None, description="数据源过滤: cls/ths/eastmoney"),
    hours: int = Query(24, description="最近N小时"),
    limit: int = Query(50, description="返回条数"),
):
    rows = fund_db.get_recent_news(source=source, hours=hours, limit=limit)
    return {"count": len(rows), "data": rows}


def _classify_cls(item: dict) -> str:
    """根据财联社标签推断分类"""
    subjects = [s.get("subject_name", "") for s in (item.get("subjects") or [])]
    subject_text = " ".join(subjects)

    if any(k in subject_text for k in ["政策", "国务院", "证监会", "发改委"]):
        return "policy"
    if any(k in subject_text for k in ["宏观", "央行", "GDP", "CPI"]):
        return "macro"
    if item.get("stock_list"):
        return "company"
    if any(k in subject_text for k in ["行业", "板块", "概念"]):
        return "industry"
    return "news"
