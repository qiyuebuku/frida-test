"""LLM 分类器 — 关键词前置 + Planner API 轻量分类"""
import json
import logging

from src.domain.extraction.services.l1a.keyword_classifier import pre_classify
from src.infrastructure.clients.llm_client import chat

logger = logging.getLogger(__name__)

# 关键词分类置信度阈值，低于此值走 LLM 分类
KEYWORD_CONFIDENCE_THRESHOLD = 0.7

# LLM 分类 prompt
CLASSIFY_SYSTEM_PROMPT = """你是一个A股财经新闻分类器。根据新闻标题和摘要，判断其所属的事件类别。

输出 JSON 数组，每条新闻一个对象：
{"id": 新闻ID, "event_type": "类别", "confidence": 0.0-1.0}

可选类别（只能选一个）：
- policy: 政策/监管
- earnings: 业绩/财报
- m&a: 并购重组
- announcement: 公司公告
- shock: 突发事件
- industry: 行业动态
- macro_data: 宏观数据
- other: 其他

只输出 JSON 数组，不要输出其他文字。"""

CLASSIFY_USER_TEMPLATE = """请对以下新闻进行分类：

{}
"""


def classify_batch(news_list: list[dict]) -> list[tuple[str, float]]:
    """批量分类新闻

    Returns: [(event_type, confidence), ...] 长度与输入相同
    """
    if not news_list:
        return []

    results = []

    # 先用关键词预分类
    need_llm: list[tuple[int, dict, str | None, float]] = []
    for i, news in enumerate(news_list):
        title = news.get("title", "")
        summary = news.get("summary", "") or ""
        kw_type, kw_conf = pre_classify(title, summary)

        if kw_type and kw_conf >= KEYWORD_CONFIDENCE_THRESHOLD:
            results.append((kw_type, kw_conf))
        else:
            need_llm.append((i, news, kw_type, kw_conf))
            results.append(("other", 0.0))  # placeholder

    # 对低置信度的走 LLM 分类
    if need_llm:
        llm_results = _llm_classify([(item[1], item[2]) for item in need_llm])
        for j, (idx, news, kw_type, kw_conf) in enumerate(need_llm):
            if j < len(llm_results):
                llm_type, llm_conf = llm_results[j]
                # LLM 结果优先，但保留关键词结果作为参考
                if llm_conf > kw_conf:
                    results[idx] = (llm_type, llm_conf)
                elif kw_type:
                    results[idx] = (kw_type, kw_conf)

    return results


def _llm_classify(items: list[tuple[dict, str | None]]) -> list[tuple[str, float]]:
    """调 Planner API 进行 LLM 批量分类"""
    lines = []
    for news, _ in items:
        title = news.get("title", "")
        summary = (news.get("summary") or "")[:200]
        lines.append(f"[{news.get('id', '?')}] {title} | {summary}")

    user_msg = CLASSIFY_USER_TEMPLATE.format("\n".join(lines))

    resp = chat(
        prompt=user_msg,
        system_prompt=CLASSIFY_SYSTEM_PROMPT,
        model="haiku",  # 分类用轻量模型
        timeout=60,
    )

    raw = resp.get("result", "")
    if not raw:
        return [("other", 0.0)] * len(items)

    # 解析 JSON
    try:
        # 去除 markdown 代码块
        import re
        raw = raw.strip()
        if raw.startswith("```"):
            match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
            if match:
                raw = match.group(1).strip()

        data = json.loads(raw)
        if not isinstance(data, list):
            data = [data]

        results = []
        for item in data[:len(items)]:
            etype = item.get("event_type", "other")
            conf = float(item.get("confidence", 0.5))
            results.append((etype, conf))

        # 补齐
        while len(results) < len(items):
            results.append(("other", 0.0))

        return results
    except Exception as e:
        logger.debug(f"[classifier] LLM 分类解析失败: {e}")
        return [("other", 0.0)] * len(items)
