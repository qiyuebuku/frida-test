#!/usr/bin/env python3
"""Seed Community Topic 候选分析脚本。

这个脚本只读数据库，不写业务表。它用于从真实 ft_news、已有
kg_cognitive_cards、kg_community_assignments 和 kg_graph_communities 中提取
候选长期主题，辅助人工决定第一批 seed community topic。

运行：

    python "docs/6. 使用说明/知识图谱/9_seed_topic_analysis.py"

输出：

    generated_seed_topic_candidates.json
    generated_seed_topic_candidates.md
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, select

import jieba


def _project_root() -> Path:
    root = Path(__file__).resolve()
    while root.name != "smart-fund-server" and root.parent != root:
        root = root.parent
    if root.name != "smart-fund-server":
        raise RuntimeError("cannot locate smart-fund-server project root")
    return root


PROJECT_ROOT = _project_root()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.persistence.models.collection import News  # noqa: E402
from src.infrastructure.persistence.models.knowledge import (  # noqa: E402
    KnowledgeCognitiveCard,
    KnowledgeCommunityAssignment,
    KnowledgeGraphCommunity,
)


TARGET = "prod"
ADAPTER = "financial"
CARD_LIMIT = int(os.environ.get("SEED_TOPIC_CARD_LIMIT", "20000"))
FT_NEWS_LIMIT = int(os.environ.get("SEED_TOPIC_FT_NEWS_LIMIT", "20000"))
TOP_SEED_CANDIDATES = int(os.environ.get("SEED_TOPIC_TOP_N", "80"))
SIMILARITY_THRESHOLD = 0.62

OUTPUT_JSON = Path(__file__).with_name("generated_seed_topic_candidates.json")
OUTPUT_MD = Path(__file__).with_name("generated_seed_topic_candidates.md")

FIELD_WEIGHTS = {
    "parent_themes": 4.0,
    "broad_topics": 3.5,
    "event_thread": 3.0,
    "title_candidate": 2.4,
    "raw_theme": 1.8,
    "mid_topics": 1.4,
    "impact_target": 1.2,
    "risk_type": 1.2,
    "driver": 1.0,
}

GENERIC_LABELS = {
    "科技",
    "政策",
    "风险",
    "行业",
    "市场",
    "A股市场",
    "美股市场",
    "上市公司",
    "政策驱动",
    "科技行业",
    "电力行业",
    "市场动态",
    "公司动态",
    "行业动态",
    "资本市场",
    "产业发展",
    "经济数据",
    "经营表现",
}

FIELD_ORDER = (
    "parent_themes",
    "broad_topics",
    "event_thread",
    "title_candidate",
    "raw_theme",
    "mid_topics",
    "impact_target",
    "risk_type",
    "driver",
)

RAW_TOPIC_LENSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AI算力链", ("AI", "人工智能", "算力", "光模块", "CPO", "GPU", "数据中心", "液冷", "服务器", "芯片")),
    ("半导体国产替代", ("半导体", "芯片", "晶圆", "先进封装", "国产替代", "光刻", "功率器件", "设备材料")),
    ("新能源出海", ("新能源", "储能", "电池", "光伏", "风电", "出海", "海外建厂", "海外订单", "关税")),
    ("电力系统与能源转型", ("绿电", "电网", "储能", "新能源发电", "电力", "算电协同", "能源转型", "并网")),
    ("A股并购重组", ("并购", "重组", "收购", "资产注入", "控制权", "重大资产重组", "产业整合")),
    ("资本市场改革", ("IPO", "再融资", "股权市场", "资本市场", "上市", "投行", "券商", "交易制度")),
    ("宏观流动性与汇率利率", ("央行", "利率", "汇率", "降息", "加息", "社融", "存贷款", "流动性", "美联储")),
    ("地缘政治与能源风险", ("地缘", "战争", "制裁", "霍尔木兹", "中东", "伊朗", "俄罗斯", "乌克兰", "油气", "供应中断")),
    ("大宗商品供需冲击", ("原油", "黄金", "铜", "煤炭", "有色", "稀土", "硫酸", "矿", "大宗商品")),
    ("政策监管与产业扶持", ("政策", "监管", "证监会", "发改委", "工信部", "国务院", "贴息", "扶持", "处罚")),
    ("公司业绩与产业景气", ("业绩", "净利润", "营收", "订单", "财报", "一季报", "增长", "亏损")),
    ("消费与服务业修复", ("消费", "服务业", "文旅", "影视", "餐饮", "外卖", "零售", "本地生活")),
)

RAW_TERM_STOPWORDS = {
    "公司",
    "今日",
    "午后",
    "开盘",
    "收盘",
    "涨停",
    "跌停",
    "上涨",
    "下跌",
    "表示",
    "公告",
    "消息",
    "日讯",
    "记者",
    "市场",
    "行业",
    "板块",
    "概念",
    "相关",
    "方面",
    "数据",
    "显示",
    "预计",
    "同比",
    "环比",
    "一季度",
    "2026",
    "2025",
}


@dataclass
class NewsPreview:
    news_id: int
    title: str
    summary: str
    content_preview: str
    published_at: str
    source: str
    category: str
    tags: list[str] = field(default_factory=list)


@dataclass
class LabelStats:
    label: str
    normalized: str
    occurrences: int = 0
    weighted_occurrences: float = 0.0
    field_counts: Counter[str] = field(default_factory=Counter)
    source_ids: set[str] = field(default_factory=set)
    evidence_ids: set[str] = field(default_factory=set)
    card_ids: set[str] = field(default_factory=set)
    news_ids: set[int] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)
    co_labels: Counter[str] = field(default_factory=Counter)
    examples: list[dict[str, Any]] = field(default_factory=list)


def main() -> None:
    rows = _load_data()
    cards: list[KnowledgeCognitiveCard] = rows["cards"]
    assignments: list[KnowledgeCommunityAssignment] = rows["assignments"]
    communities: list[KnowledgeGraphCommunity] = rows["communities"]
    news_by_id: dict[int, NewsPreview] = rows["news_by_id"]

    label_stats = _collect_label_stats(cards, news_by_id)
    candidates = _rank_seed_candidates(label_stats)
    candidate_groups = _cluster_candidates(candidates)
    raw_news_analysis = _analyze_raw_ft_news(news_by_id)
    community_review = _review_existing_communities(communities, assignments, news_by_id)
    result = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "target": TARGET,
        "adapter": ADAPTER,
        "input": {
            "ft_news_limit": FT_NEWS_LIMIT,
            "card_limit": CARD_LIMIT,
            "news_rows_loaded": len(news_by_id),
            "cognitive_cards_loaded": len(cards),
            "community_assignments_loaded": len(assignments),
            "graph_communities_loaded": len(communities),
        },
        "raw_ft_news_analysis": raw_news_analysis,
        "top_seed_candidates": candidates[:TOP_SEED_CANDIDATES],
        "candidate_similarity_groups": candidate_groups[:30],
        "existing_community_review": community_review,
        "notes": [
            "本脚本只读数据库，不写入 seed community。",
            "候选分数用于人工筛选，不是系统自动裁决规则。",
            "建议先审核 top 15-30 个候选，再写入正式 seed 配置。",
        ],
    }
    OUTPUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    OUTPUT_MD.write_text(_render_markdown(result), encoding="utf-8")
    _print_summary(result)


def _load_data() -> dict[str, Any]:
    with get_session(TARGET) as session:
        inspector = inspect(session.bind)
        news_by_id: dict[int, NewsPreview] = {}
        if inspector.has_table(News.__tablename__):
            news_rows = session.execute(
                select(
                    News.id,
                    News.title,
                    News.summary,
                    News.content,
                    News.source,
                    News.category,
                    News.tags,
                    News.published_at,
                    News.created_at,
                )
                .order_by(News.created_at.desc().nullslast(), News.id.desc())
                .limit(FT_NEWS_LIMIT)
            ).all()
            news_by_id = {
                int(row.id): NewsPreview(
                    news_id=int(row.id),
                    title=str(row.title or ""),
                    summary=str(row.summary or ""),
                    content_preview=_clip(str(row.content or ""), 1200),
                    published_at=_date_text(row.published_at),
                    source=str(row.source or ""),
                    category=str(row.category or ""),
                    tags=[str(item) for item in row.tags or [] if item],
                )
                for row in news_rows
            }

        cards = session.scalars(
            select(KnowledgeCognitiveCard)
            .where(
                KnowledgeCognitiveCard.adapter_name == ADAPTER,
                KnowledgeCognitiveCard.status == "active",
            )
            .order_by(KnowledgeCognitiveCard.created_at.desc().nullslast())
            .limit(CARD_LIMIT)
        ).all()
        assignments = session.scalars(
            select(KnowledgeCommunityAssignment)
            .where(
                KnowledgeCommunityAssignment.adapter_name == ADAPTER,
                KnowledgeCommunityAssignment.status == "active",
            )
            .order_by(KnowledgeCommunityAssignment.created_at.desc().nullslast())
            .limit(CARD_LIMIT * 2)
        ).all()
        communities = session.scalars(
            select(KnowledgeGraphCommunity)
            .where(
                KnowledgeGraphCommunity.adapter_name == ADAPTER,
                KnowledgeGraphCommunity.status == "active",
            )
            .order_by(KnowledgeGraphCommunity.updated_at.desc().nullslast())
            .limit(1000)
        ).all()

    return {
        "news_by_id": news_by_id,
        "cards": list(cards),
        "assignments": list(assignments),
        "communities": list(communities),
    }


def _collect_label_stats(cards: list[KnowledgeCognitiveCard], news_by_id: dict[int, NewsPreview]) -> dict[str, LabelStats]:
    stats: dict[str, LabelStats] = {}
    for card in cards:
        news_id = _extract_news_id(card.source_id)
        news = news_by_id.get(news_id) if news_id else None
        intents = [item for item in card.topic_intents or [] if isinstance(item, dict)]
        for intent in intents:
            intent_labels = _intent_labels(intent)
            normalized_labels = [item["normalized"] for item in intent_labels]
            for item in intent_labels:
                label = item["label"]
                normalized = item["normalized"]
                if not _usable_label(label):
                    continue
                entry = stats.setdefault(normalized, LabelStats(label=label, normalized=normalized))
                entry.occurrences += 1
                entry.weighted_occurrences += FIELD_WEIGHTS.get(item["field"], 0.8)
                entry.field_counts[item["field"]] += 1
                entry.source_ids.add(card.source_id)
                entry.evidence_ids.add(card.evidence_id)
                entry.card_ids.add(card.cognitive_card_id)
                if news_id:
                    entry.news_ids.add(news_id)
                if news and news.published_at:
                    entry.dates.add(news.published_at[:10])
                for co_label in normalized_labels:
                    if co_label != normalized:
                        entry.co_labels[co_label] += 1
                if len(entry.examples) < 6:
                    entry.examples.append(
                        {
                            "source_id": card.source_id,
                            "news_id": news_id,
                            "news_title": news.title if news else str(card.payload.get("title") or ""),
                            "summary": _clip(str(intent.get("summary") or card.summary or ""), 140),
                            "field": item["field"],
                        }
                    )
    return stats


def _analyze_raw_ft_news(news_by_id: dict[int, NewsPreview]) -> dict[str, Any]:
    lens_stats = _raw_lens_stats(news_by_id)
    term_stats = _raw_term_stats(news_by_id)
    cooccurrence = _raw_lens_cooccurrence(news_by_id)
    return {
        "news_count": len(news_by_id),
        "topic_lens_hits": lens_stats,
        "top_terms": term_stats[:120],
        "lens_cooccurrence": cooccurrence[:80],
        "notes": [
            "topic_lens_hits 是 seed 规划辅助观察，不是正式归档规则。",
            "top_terms 来自 ft_news 标题、摘要和正文预览的分词统计。",
            "如果 raw ft_news 与 Cognitive Card 结果冲突，应优先扩大抽卡样本再复盘。",
        ],
    }


def _raw_lens_stats(news_by_id: dict[int, NewsPreview]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for lens_title, keywords in RAW_TOPIC_LENSES:
        matched: list[tuple[int, int, NewsPreview, list[str]]] = []
        date_set: set[str] = set()
        source_set: set[str] = set()
        for news in news_by_id.values():
            text = _news_text(news)
            hits = [keyword for keyword in keywords if keyword.lower() in text.lower()]
            if not hits:
                continue
            score = sum(text.lower().count(keyword.lower()) for keyword in hits)
            matched.append((score, news.news_id, news, hits))
            source_set.add(news.source)
            if news.published_at:
                date_set.add(news.published_at[:10])
        matched.sort(key=lambda item: (item[0], item[1]), reverse=True)
        results.append(
            {
                "title": lens_title,
                "matched_news_count": len(matched),
                "source_count": len(source_set),
                "date_count": len(date_set),
                "hit_keywords": _top_keywords([hit for _, _, _, hits in matched for hit in hits]),
                "example_news": [
                    {
                        "news_id": news.news_id,
                        "title": news.title,
                        "published_at": news.published_at,
                        "hits": hits[:8],
                    }
                    for _, _, news, hits in matched[:8]
                ],
            }
        )
    return sorted(results, key=lambda item: (item["matched_news_count"], item["date_count"]), reverse=True)


def _raw_term_stats(news_by_id: dict[int, NewsPreview]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    source_map: dict[str, set[int]] = defaultdict(set)
    example_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for news in news_by_id.values():
        terms = _extract_terms(_news_text(news))
        for term in set(terms):
            source_map[term].add(news.news_id)
            if len(example_map[term]) < 4:
                example_map[term].append(
                    {
                        "news_id": news.news_id,
                        "title": news.title,
                        "published_at": news.published_at,
                    }
                )
        counter.update(terms)
    rows: list[dict[str, Any]] = []
    for term, count in counter.most_common(500):
        if not _usable_raw_term(term):
            continue
        rows.append(
            {
                "term": term,
                "count": count,
                "news_count": len(source_map[term]),
                "examples": example_map[term],
            }
        )
    return sorted(rows, key=lambda item: (item["news_count"], item["count"]), reverse=True)


def _raw_lens_cooccurrence(news_by_id: dict[int, NewsPreview]) -> list[dict[str, Any]]:
    pair_counts: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for news in news_by_id.values():
        text = _news_text(news).lower()
        matched = [
            title
            for title, keywords in RAW_TOPIC_LENSES
            if any(keyword.lower() in text for keyword in keywords)
        ]
        for index, left in enumerate(matched):
            for right in matched[index + 1 :]:
                pair = tuple(sorted((left, right)))
                pair_counts[pair] += 1
                if len(examples[pair]) < 3:
                    examples[pair].append({"news_id": news.news_id, "title": news.title})
    return [
        {
            "left": left,
            "right": right,
            "news_count": count,
            "examples": examples[(left, right)],
        }
        for (left, right), count in pair_counts.most_common()
    ]


def _intent_labels(intent: dict[str, Any]) -> list[dict[str, str]]:
    labels: list[dict[str, str]] = []
    for field_name in FIELD_ORDER:
        value = intent.get(field_name)
        values = value if isinstance(value, list) else [value]
        for raw in values:
            label = _clean_label(raw)
            if not label:
                continue
            labels.append({"field": field_name, "label": label, "normalized": _normalize_label(label)})
    return labels


def _rank_seed_candidates(stats: dict[str, LabelStats]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in stats.values():
        source_count = len(entry.source_ids)
        date_count = len(entry.dates)
        field_strength = sum(FIELD_WEIGHTS.get(field, 0.8) * count for field, count in entry.field_counts.items())
        high_level_hits = (
            entry.field_counts["parent_themes"]
            + entry.field_counts["broad_topics"]
            + entry.field_counts["event_thread"]
        )
        low_signal_penalty = 8.0 if high_level_hits == 0 else 0.0
        seed_score = round(
            source_count * 5.0
            + date_count * 2.5
            + min(entry.occurrences, 20) * 1.2
            + field_strength
            - _generic_penalty(entry.label)
            - low_signal_penalty,
            3,
        )
        if source_count < 1:
            continue
        rows.append(
            {
                "title": entry.label,
                "normalized": entry.normalized,
                "seed_score": seed_score,
                "occurrences": entry.occurrences,
                "source_count": source_count,
                "evidence_count": len(entry.evidence_ids),
                "date_count": date_count,
                "field_counts": dict(entry.field_counts.most_common()),
                "co_labels": [
                    {"label": _display_label(label, stats), "count": count}
                    for label, count in entry.co_labels.most_common(10)
                ],
                "examples": entry.examples[:5],
                "suggested_scope": _suggest_scope(entry),
                "review_hint": _review_hint(entry),
            }
        )
    return sorted(rows, key=lambda item: (item["seed_score"], item["source_count"], item["occurrences"]), reverse=True)


def _cluster_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    used: set[str] = set()
    limited = candidates[: min(len(candidates), 120)]
    for candidate in limited:
        norm = candidate["normalized"]
        if norm in used:
            continue
        members = [candidate]
        used.add(norm)
        for other in limited:
            other_norm = other["normalized"]
            if other_norm in used:
                continue
            similarity = _label_similarity(norm, other_norm)
            if similarity >= SIMILARITY_THRESHOLD:
                members.append({**other, "similarity_to_lead": round(similarity, 3)})
                used.add(other_norm)
        if len(members) <= 1:
            continue
        groups.append(
            {
                "lead_title": candidate["title"],
                "member_count": len(members),
                "combined_source_count": len({example.get("source_id") for item in members for example in item.get("examples", [])}),
                "members": [
                    {
                        "title": item["title"],
                        "seed_score": item["seed_score"],
                        "source_count": item["source_count"],
                        "occurrences": item["occurrences"],
                        **({"similarity_to_lead": item["similarity_to_lead"]} if "similarity_to_lead" in item else {}),
                    }
                    for item in members
                ],
            }
        )
    return sorted(groups, key=lambda item: (item["member_count"], item["combined_source_count"]), reverse=True)


def _review_existing_communities(
    communities: list[KnowledgeGraphCommunity],
    assignments: list[KnowledgeCommunityAssignment],
    news_by_id: dict[int, NewsPreview],
) -> dict[str, Any]:
    assignment_counts = Counter(row.community_id for row in assignments)
    community_rows: list[dict[str, Any]] = []
    for community in communities:
        metrics = dict(community.metrics or {})
        source_ids = _as_list(metrics.get("source_ids")) or _source_ids_from_community(community)
        news_ids = [_extract_news_id(source_id) for source_id in source_ids]
        news_ids = [item for item in news_ids if item]
        community_rows.append(
            {
                "community_id": community.community_id,
                "title": community.title,
                "level": community.level,
                "source_count": len(set(source_ids)),
                "evidence_count": len(community.evidence_ids or []),
                "chunk_count": len(community.chunk_ids or []),
                "assignment_count": assignment_counts[community.community_id],
                "maturity": metrics.get("maturity") or _maturity(len(set(source_ids))),
                "example_titles": [news_by_id[news_id].title for news_id in news_ids[:5] if news_id in news_by_id],
            }
        )
    similar_pairs = _similar_community_pairs(community_rows)
    return {
        "community_count": len(community_rows),
        "top_by_source_count": sorted(
            community_rows,
            key=lambda item: (item["source_count"], item["assignment_count"], item["evidence_count"]),
            reverse=True,
        )[:30],
        "similar_title_pairs": similar_pairs[:30],
    }


def _similar_community_pairs(communities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for idx, left in enumerate(communities):
        for right in communities[idx + 1 :]:
            score = _label_similarity(_normalize_label(left["title"]), _normalize_label(right["title"]))
            if score < SIMILARITY_THRESHOLD:
                continue
            pairs.append(
                {
                    "left": left["title"],
                    "right": right["title"],
                    "similarity": round(score, 3),
                    "left_source_count": left["source_count"],
                    "right_source_count": right["source_count"],
                }
            )
    return sorted(pairs, key=lambda item: item["similarity"], reverse=True)


def _render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Seed Community Topic 候选分析结果",
        "",
        f"- 生成时间：{result['generated_at']}",
        f"- target：`{result['target']}`",
        f"- adapter：`{result['adapter']}`",
        f"- ft_news rows：{result['input']['news_rows_loaded']}",
        f"- cognitive cards：{result['input']['cognitive_cards_loaded']}",
        f"- assignments：{result['input']['community_assignments_loaded']}",
        f"- active communities：{result['input']['graph_communities_loaded']}",
        "",
        "## Raw ft_news Lens Analysis",
        "",
        "这部分直接基于 `ft_news` 原始新闻统计，不依赖 Cognitive Card。",
        "",
        "| # | Lens | matched news | date | top keywords | examples |",
        "|---|---|---:|---:|---|---|",
    ]
    raw = result.get("raw_ft_news_analysis") or {}
    for idx, item in enumerate((raw.get("topic_lens_hits") or [])[:30], start=1):
        keywords = "、".join(f"{row['keyword']}({row['count']})" for row in item.get("hit_keywords", [])[:6])
        examples = "；".join(row["title"] for row in item.get("example_news", [])[:2])
        lines.append(
            f"| {idx} | {item['title']} | {item['matched_news_count']} | {item['date_count']} | "
            f"{_md_cell(keywords)} | {_md_cell(examples)} |"
        )
    lines.extend(
        [
            "",
            "## Raw ft_news High-Frequency Terms",
            "",
            "| # | term | news | count | examples |",
            "|---|---|---:|---:|---|",
        ]
    )
    for idx, item in enumerate((raw.get("top_terms") or [])[:50], start=1):
        examples = "；".join(row["title"] for row in item.get("examples", [])[:2])
        lines.append(
            f"| {idx} | {item['term']} | {item['news_count']} | {item['count']} | {_md_cell(examples)} |"
        )
    lines.extend(
        [
            "",
            "## Cognitive Card Seed Candidates",
            "",
            "这部分基于已经生成的 `kg_cognitive_cards` 统计。当前如果卡片数量少，它只能作为辅助校验。",
            "",
            "## Top Seed Candidates",
            "",
            "| # | 候选主题 | score | source | occurrence | 主要字段 | 建议 scope |",
            "|---|---|---:|---:|---:|---|---|",
        ]
    )
    for idx, item in enumerate(result["top_seed_candidates"][:TOP_SEED_CANDIDATES], start=1):
        fields = ", ".join(f"{k}:{v}" for k, v in list(item["field_counts"].items())[:4])
        lines.append(
            f"| {idx} | {item['title']} | {item['seed_score']} | {item['source_count']} | "
            f"{item['occurrences']} | {fields} | {_md_cell(item['suggested_scope'])} |"
        )
    lines.extend(["", "## Candidate Similarity Groups", ""])
    if result["candidate_similarity_groups"]:
        for group in result["candidate_similarity_groups"][:20]:
            member_text = "、".join(item["title"] for item in group["members"])
            lines.append(f"- **{group['lead_title']}**：{member_text}")
    else:
        lines.append("- 暂无相似候选组。")
    lines.extend(["", "## Existing Community Review", ""])
    review = result["existing_community_review"]
    lines.append(f"- active community count：{review['community_count']}")
    lines.append("")
    lines.append("### Top Communities")
    lines.append("")
    lines.append("| title | source | assignment | maturity | examples |")
    lines.append("|---|---:|---:|---|---|")
    for item in review["top_by_source_count"][:20]:
        examples = "；".join(item["example_titles"][:2])
        lines.append(
            f"| {item['title']} | {item['source_count']} | {item['assignment_count']} | "
            f"{item['maturity']} | {_md_cell(examples)} |"
        )
    lines.append("")
    lines.append("### Similar Community Titles")
    lines.append("")
    if review["similar_title_pairs"]:
        for pair in review["similar_title_pairs"][:20]:
            lines.append(f"- {pair['left']} ↔ {pair['right']}，similarity={pair['similarity']}")
    else:
        lines.append("- 暂无明显相似 community 标题对。")
    lines.extend(
        [
            "",
            "## 使用建议",
            "",
            "- 先人工审核 Top 15-30 个候选主题，不要直接全量写成 seed。",
            "- source_count 高、date_count 高、且 parent/broad/event_thread 权重高的候选优先。",
            "- 如果多个候选属于同一相似组，应合并成一个更稳定的大词 seed。",
            "- 过粗主题和单点事实不应进入 seed，可以保留为 emergent community。",
        ]
    )
    return "\n".join(lines) + "\n"


def _print_summary(result: dict[str, Any]) -> None:
    print("============ Seed Topic Analysis ============")
    print(json.dumps(result["input"], ensure_ascii=False, indent=2))
    print("\nRaw ft_news lens hits:")
    for item in (result.get("raw_ft_news_analysis") or {}).get("topic_lens_hits", [])[:12]:
        print(
            f"- {item['title']} news={item['matched_news_count']} "
            f"dates={item['date_count']}"
        )
    print("\nTop candidates:")
    for item in result["top_seed_candidates"][:15]:
        print(
            f"- {item['title']} score={item['seed_score']} "
            f"sources={item['source_count']} occurrences={item['occurrences']}"
        )
    print(f"\n[output] {OUTPUT_JSON}")
    print(f"[output] {OUTPUT_MD}")


def _suggest_scope(entry: LabelStats) -> str:
    fields = set(entry.field_counts)
    co_labels = [_display_label(label, {}) for label, _ in entry.co_labels.most_common(5)]
    if "risk_type" in fields:
        prefix = "承接该风险主题下的多来源风险线索、影响对象和后续变化"
    elif "event_thread" in fields:
        prefix = "承接该事件线下的触发、发酵、回应、升级和结果"
    elif "parent_themes" in fields or "broad_topics" in fields:
        prefix = "承接该长期主题下的政策、产业、资金、风险和主体变化"
    else:
        prefix = "承接围绕该主题的持续新闻、主体动作和影响线索"
    return f"{prefix}。相关信号：{'、'.join(co_labels[:4])}" if co_labels else prefix


def _review_hint(entry: LabelStats) -> str:
    if _normalize_label(entry.label) in {_normalize_label(item) for item in GENERIC_LABELS}:
        return "过粗分类，通常不应直接作为 seed。"
    if len(entry.source_ids) >= 3 and (entry.field_counts["parent_themes"] or entry.field_counts["broad_topics"]):
        return "优先审核：跨 source 且出现在父级/宽主题字段。"
    if len(entry.source_ids) == 1:
        return "单 source 线索，除非业务价值明确，否则先保留为 emergent。"
    return "可作为候选，需人工确认粒度和边界。"


def _source_ids_from_community(community: KnowledgeGraphCommunity) -> list[str]:
    metrics = dict(community.metrics or {})
    values = _as_list(metrics.get("source_ids"))
    if values:
        return [str(item) for item in values if item]
    source_ids: list[str] = []
    for evidence_id in community.evidence_ids or []:
        text = str(evidence_id)
        match = re.search(r"(?:usage_demo_write_path:)?ft_news:(\d+)", text)
        if match:
            source_ids.append(f"ft_news:{match.group(1)}")
    return source_ids


def _news_text(news: NewsPreview) -> str:
    return "\n".join(
        item
        for item in [
            news.title,
            news.summary,
            news.content_preview,
            " ".join(news.tags),
        ]
        if item
    )


def _extract_terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in jieba.lcut(text):
        token = _clean_label(token)
        if _usable_raw_term(token):
            terms.append(token)
    # 标题里常见复合词可能被切散，额外保留部分 4-10 字连续片段。
    for phrase in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{4,12}", text):
        phrase = _clean_label(phrase)
        if _usable_raw_term(phrase) and not phrase.isdigit():
            terms.append(phrase)
    return terms


def _usable_raw_term(term: str) -> bool:
    term = _clean_label(term)
    if len(term) < 2 or len(term) > 16:
        return False
    if term in RAW_TERM_STOPWORDS:
        return False
    if term.isdigit():
        return False
    if re.fullmatch(r"\d+(\.\d+)?%?", term):
        return False
    if re.fullmatch(r"[A-Za-z]{1,2}", term):
        return False
    return True


def _top_keywords(values: list[str]) -> list[dict[str, Any]]:
    return [
        {"keyword": keyword, "count": count}
        for keyword, count in Counter(values).most_common(12)
    ]


def _extract_news_id(source_id: str) -> int | None:
    match = re.search(r"ft_news:(\d+)", str(source_id or ""))
    if not match:
        return None
    return int(match.group(1))


def _clean_label(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", "", text)
    text = text.strip("，。；;、:：()（）[]【】")
    return text


def _normalize_label(value: str) -> str:
    text = _clean_label(value).lower()
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "", text)
    return text


def _usable_label(label: str) -> bool:
    text = _clean_label(label)
    if len(text) < 2:
        return False
    if len(text) > 32:
        return False
    if text.isdigit():
        return False
    return True


def _generic_penalty(label: str) -> float:
    normalized = _normalize_label(label)
    if normalized in {_normalize_label(item) for item in GENERIC_LABELS}:
        return 12.0
    if len(normalized) <= 2:
        return 4.0
    return 0.0


def _label_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if shorter in longer:
        ratio = min(len(shorter), len(longer)) / max(len(shorter), len(longer))
        if ratio >= 0.58 and not _generic_fragment(shorter):
            return min(0.92, 0.55 + ratio * 0.4)
    sequence_score = SequenceMatcher(None, left, right).ratio()
    bigram_score = _bigram_jaccard(left, right)
    if sequence_score >= 0.72 and bigram_score >= 0.35:
        return sequence_score
    if sequence_score >= 0.62 and bigram_score >= 0.48:
        return round((sequence_score + bigram_score) / 2, 4)
    return 0.0


def _generic_fragment(value: str) -> bool:
    if value in {_normalize_label(item) for item in GENERIC_LABELS}:
        return True
    generic_fragments = {"基础设施", "市场", "行业", "风险", "政策", "产业", "业务"}
    return value in {_normalize_label(item) for item in generic_fragments}


def _bigram_jaccard(left: str, right: str) -> float:
    left_bigrams = _bigrams(left)
    right_bigrams = _bigrams(right)
    if not left_bigrams or not right_bigrams:
        return 0.0
    return len(left_bigrams & right_bigrams) / len(left_bigrams | right_bigrams)


def _bigrams(value: str) -> set[str]:
    value = _normalize_label(value)
    if len(value) < 2:
        return set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _display_label(normalized: str, stats: dict[str, LabelStats]) -> str:
    entry = stats.get(normalized)
    return entry.label if entry else normalized


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple | set):
        return list(value)
    return [value]


def _maturity(source_count: int) -> str:
    if source_count >= 5:
        return "mature_topic"
    if source_count >= 2:
        return "multi_evidence"
    return "single_evidence"


def _date_text(value: Any) -> str:
    if not value:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _md_cell(text: str) -> str:
    return str(text or "").replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    main()
