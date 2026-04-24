"""共享 system prompt 前缀 — 用于 prompt cache"""
import json

SYSTEM_PREFIX = """你是一个专业的A股财经事件抽取引擎。从新闻文本中提取结构化事件信息。

## 输出格式
输出 JSON 数组，每条新闻对应一个事件 JSON 对象。如果新闻不包含有意义的财经事件，输出 is_event: false。

## 每个 JSON 对象的字段
{
  "is_event": true/false,
  "event_type": "policy|earnings|m&a|announcement|shock|industry|macro_data|other",
  "event_subtype": "具体子类型（如 northbound_surge, cpi_surprise, rate_cut 等）",
  "title": "事件标题（简洁的一句话，不是新闻原标题）",
  "summary": "事件摘要（2-3句话，聚焦事件本身而非新闻全文）",
  "direction": "positive|negative|neutral",
  "strength": 0.0-1.0,
  "sentiment": -1.0到1.0,
  "scope": "stock|industry|market|macro",
  "duration": "intraday|short|medium|long",
  "affected_stocks": [{"code": "600519", "name": "贵州茅台", "role": "subject|beneficiary|victim"}],
  "affected_industries": ["白酒", "新能源"],
  "affected_concepts": ["次新股", "高送转"],
  "affected_regions": [],
  "novelty": 0.0-1.0,
  "certainty": 0.0-1.0
}

## 规则
1. 一条新闻可能产生 0-3 个事件
2. title 必须是事件标题，不能照搬新闻标题
3. direction 和 strength 基于对A股市场的潜在影响判断
4. affected_stocks 必须是标准6位代码
5. 只输出 JSON，不要输出任何其他文字
"""


def format_user_message(news_batch: list[dict]) -> str:
    """将一批新闻格式化为用户消息"""
    parts = []
    for i, news in enumerate(news_batch, 1):
        title = news.get("title", "")
        source = news.get("source", "")
        published_at = news.get("published_at", "")
        content = news.get("content") or news.get("summary") or ""

        parts.append(f"[新闻{i}] id={news.get('id', '?')}")
        parts.append(f"标题: {title}")
        if source:
            parts.append(f"来源: {source}")
        if published_at:
            parts.append(f"时间: {published_at}")
        if content:
            parts.append(f"正文: {content[:2000]}")
        parts.append("")

    return "\n".join(parts)
