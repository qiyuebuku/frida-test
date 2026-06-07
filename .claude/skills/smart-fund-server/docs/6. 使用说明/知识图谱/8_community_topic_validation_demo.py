#!/usr/bin/env python3
"""验证：Cognitive Card -> Community Card 高维索引归档方案。

本脚本对应设计文档：

    docs/5. 设计方案/1. 知识图谱/16. Community Topic高维信号提取验证方案.md

它不是正式写入链路，也不会写 PG / Milvus。它用于用真实 ft_news 样本验证：

    ft_news
      -> chunk context chain
      -> Cognitive Card 局部信号抽取
      -> 每个 topic_intent 召回候选 L0/L1/L2 community TopK
      -> Assignment LLM 裁决 attach_existing / create_new_l0
      -> 内存中构建 community 草案
      -> 可选生成 community report 草案

运行方式：

    python "docs/6. 使用说明/知识图谱/8_community_topic_validation_demo.py"

可通过环境变量调整：

    COMMUNITY_TOPIC_NEWS_LIMIT=15
    COMMUNITY_TOPIC_CANDIDATE_LIMIT=80
    COMMUNITY_TOPIC_GENERATE_REPORTS=1
    COMMUNITY_TOPIC_USE_FIXED_FT_NEWS=1
    COMMUNITY_TOPIC_MAX_INTENTS_PER_NEWS=10
    COMMUNITY_TOPIC_CHUNK_MAX_CHARS=1600
    COMMUNITY_TOPIC_RESUME=0
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from pprint import pprint
from typing import Any

from sqlalchemy import inspect, select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(line_buffering=True)


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

from src.application.services.knowledge_llm_config import resolve_kg_llm_model  # noqa: E402
from src.infrastructure.connections import get_session  # noqa: E402
from src.infrastructure.llm_proxy.service import get_llm_gateway_service  # noqa: E402
from src.infrastructure.llm_proxy.types import LLMProxyRequest  # noqa: E402
from src.infrastructure.observability.langfuse_tracing import (  # noqa: E402
    langfuse_flush,
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.collection import News  # noqa: E402


TARGET = "prod"
ADAPTER = "financial"
USE_FIXED_FT_NEWS = os.getenv("COMMUNITY_TOPIC_USE_FIXED_FT_NEWS", "1").strip() not in {"0", "false", "False"}
FIXED_FT_NEWS_IDS: tuple[int, ...] = (
    83904,  # A股并购重组 / 产业整合
    59604,  # AI 应用 / 人工智能 ETF
    74425,  # AI 芯片短缺 / 自建产能
    66308,  # 储能出海 / 海外建厂
    76578,  # 金砖产业合作 / 企业出海服务
    68104,  # 国务院生产性服务业贷款贴息
    74339,  # A股公司被证监会立案
    59605,  # 美债 / 美联储政策路径
    74419,  # 硫酸短缺 / 铜价 / 美伊风险
    72799,  # 广东区域股权市场培育企业
    73304,  # 拓斯达业绩 / 工业机器人
    77551,  # 美股午盘 / 中东风险
    77549,  # Pimco / 海湾地区借款
    77547,  # 霍尔木兹海峡 / 美伊风险
    75461,  # 华勤技术港股上市 / AI 算力
)
NEWS_LIMIT = int(os.getenv("COMMUNITY_TOPIC_NEWS_LIMIT", str(len(FIXED_FT_NEWS_IDS))))
CANDIDATE_LIMIT = int(os.getenv("COMMUNITY_TOPIC_CANDIDATE_LIMIT", "80"))
MIN_TEXT_CHARS = int(os.getenv("COMMUNITY_TOPIC_MIN_TEXT_CHARS", "180"))
GENERATE_REPORTS = os.getenv("COMMUNITY_TOPIC_GENERATE_REPORTS", "1").strip() not in {"0", "false", "False"}
MAX_REPORT_COMMUNITIES = int(os.getenv("COMMUNITY_TOPIC_MAX_REPORT_COMMUNITIES", "8"))
EXTRACTION_CONCURRENCY = int(os.getenv("COMMUNITY_TOPIC_EXTRACTION_CONCURRENCY", "4"))
MAX_INTENTS_PER_NEWS = int(os.getenv("COMMUNITY_TOPIC_MAX_INTENTS_PER_NEWS", "10"))
CHUNK_MAX_CHARS = int(os.getenv("COMMUNITY_TOPIC_CHUNK_MAX_CHARS", "1600"))
RESUME_FROM_OUTPUT = os.getenv("COMMUNITY_TOPIC_RESUME", "0").strip() in {"1", "true", "True"}
OUTPUT_FILE = Path(__file__).with_name("generated_community_topic_validation.json")

MIN_TOP_K = 5
DEFAULT_TOP_K = 8
MAX_TOP_K = 15
DEFAULT_MAX_ATTACH = 3
COMPLEX_MAX_ATTACH = 5
ASSIGNMENT_ACTIONS = {"attach_existing", "create_new_l0"}
ASSIGNMENT_UPDATE_MODES = {"append_reference", "update_delta", "rewrite_summary"}
COGNITIVE_CARD_SCHEMA_VERSION = "cognitive_card_v1_chunk_local_signals_20260607"
IMPLEMENTATION_PERSPECTIVE_TERMS: tuple[str, ...] = (
    "当前chunk",
    "当前 chunk",
    "本chunk",
    "本 chunk",
    "该chunk",
    "该 chunk",
    "这段chunk",
    "这段 chunk",
    "chunk text",
    "Cognitive Card",
)
MARKET_NOISE_TERMS: tuple[str, ...] = (
    "涨",
    "跌",
    "拉升",
    "走高",
    "跳水",
    "异动",
    "领涨",
    "领跌",
    "创新高",
    "迭创新高",
    "资金净流入",
    "资金净流出",
    "午盘",
    "早盘",
    "收盘",
    "盘中",
)
WEAK_IMPACT_TARGET_TERMS: tuple[str, ...] = (
    "创业板",
    "科创板",
    "ETF",
    "指数",
    "板块",
    "市场",
    "A股市场",
    "港股市场",
)

PARENT_TOPIC_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("A股并购重组", ("并购", "重组", "资产注入", "横向整合", "垂直整合", "产业整合", "集中度提升"), ("A股", "上市公司", "科创板", "创业板", "券商", "并购六条", "科创板八条")),
    ("AI算力链", ("算力", "光模块", "gpu", "芯片", "数据中心", "超节点"), ()),
    ("资本市场融资", ("港股", "a+h", "ipo", "上市融资", "港股上市", "再融资", "reits", "etf", "资本市场融资", "股权融资"), ()),
    ("实体经济金融支持", ("中小微", "小微", "贷款", "贴息", "再贷款", "存贷款", "社会融资", "养老金融", "金融支持"), ()),
    ("新能源出海", ("新能源", "储能", "电池", "光伏"), ("出海", "海外", "欧洲", "西班牙")),
    ("企业出海服务", ("出海", "海外", "金砖", "供应链后备基地", "工业园区"), ()),
    ("中东地缘政治风险", ("中东", "伊朗", "以色列", "霍尔木兹", "美伊", "海湾", "战争"), ()),
    ("美联储政策路径", ("美联储", "美债", "降息", "沃什"), ()),
    ("大宗商品供应风险", ("铜", "硫酸", "原油", "黄金", "有色", "大宗商品"), ()),
    ("资本市场监管", ("证监会", "立案", "信披", "退市", "资本市场监管", "上市公司监管"), ()),
    ("区域资本市场建设", ("区域性股权市场", "股权交易中心", "挂牌", "专精特新"), ()),
    ("高端装备与机器人", ("工业机器人", "自动化", "智能制造", "高端装备"), ()),
)

PARENT_TOPIC_SCOPES: dict[str, str] = {
    "A股并购重组": "围绕A股并购重组、资产注入、产业整合、监管政策和上市公司质量改善的主题索引。",
    "AI算力链": "围绕AI应用、算力基础设施、AI芯片、光模块、数据中心和相关资金行情的主题索引。",
    "资本市场融资": "围绕IPO、再融资、REITs、ETF、港股/A+H上市和资本市场融资工具的主题索引。",
    "实体经济金融支持": "围绕贷款、贴息、再贷款、小微企业融资、养老金融和金融支持实体经济政策的主题索引。",
    "新能源出海": "围绕新能源企业海外建厂、产能布局、供应链本地化、贸易政策和执行风险的主题索引。",
    "企业出海服务": "围绕企业出海服务、跨境产业合作、海外供应链后备基地和地方政策支持的主题索引。",
    "中东地缘政治风险": "围绕中东冲突、伊朗与以色列局势、霍尔木兹海峡、能源通道和全球市场冲击的主题索引。",
    "美联储政策路径": "围绕美联储人事、利率路径、通胀、美元流动性和美债市场预期的主题索引。",
    "大宗商品供应风险": "围绕原油、有色、化工原料等大宗商品供应扰动、价格波动和产业链影响的主题索引。",
    "资本市场监管": "围绕上市公司信披、证监会立案、退市风险、公司治理和资本市场监管事件的主题索引。",
    "区域资本市场建设": "围绕区域性股权市场、地方资本市场服务、企业培育和专精特新融资支持的主题索引。",
    "高端装备与机器人": "围绕工业机器人、智能制造、高端装备、自动化产线和制造业升级的主题索引。",
}

SEMANTIC_TOKEN_GROUPS: dict[str, tuple[str, ...]] = {
    "merger_restructuring": ("并购", "重组", "收购", "资产注入", "控制权", "横向整合", "垂直整合", "产业整合", "集中度提升"),
    "ai_compute": ("ai", "人工智能", "算力", "光模块", "gpu", "芯片", "数据中心", "超节点", "cpo"),
    "new_energy": ("新能源", "储能", "电池", "光伏", "绿电"),
    "overseas": ("出海", "海外", "欧洲", "西班牙", "金砖", "供应链"),
    "policy": ("政策", "国务院", "证监会", "央行", "美联储", "监管", "贴息"),
    "capital_market": ("a股", "港股", "a+h", "ipo", "etf", "再融资", "股权市场", "上市"),
    "financial_support": ("中小微", "小微", "贷款", "贴息", "再贷款", "存贷款", "社会融资", "养老金融"),
    "geopolitics": ("中东", "伊朗", "以色列", "霍尔木兹", "美伊", "海湾", "战争", "制裁"),
    "commodity": ("铜", "硫酸", "原油", "黄金", "有色", "大宗商品"),
    "robotics": ("工业机器人", "自动化", "智能制造", "高端装备"),
}

COGNITIVE_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "title_candidates": {"type": "array", "items": {"type": "string"}},
        "topic_intents": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "raw_theme": {"type": "string"},
                    "title_candidate": {"type": "string"},
                    "driver": {"type": "array", "items": {"type": "string"}},
                    "impact_target": {"type": "array", "items": {"type": "string"}},
                    "risk_type": {"type": "array", "items": {"type": "string"}},
                    "event_thread": {"type": "array", "items": {"type": "string"}},
                    "event_action": {"type": "array", "items": {"type": "string"}},
                    "actors": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "impact_direction": {
                        "type": "string",
                        "enum": ["positive", "negative", "mixed", "uncertain"],
                    },
                    "event_stage": {"type": "string"},
                    "timeline_position": {
                        "type": "string",
                        "enum": [
                            "trigger",
                            "reaction",
                            "escalation",
                            "deescalation",
                            "resolution",
                            "follow_up",
                            "uncertain",
                        ],
                    },
                    "event_time": {"type": "string"},
                    "summary": {"type": "string"},
                    "supporting_text": {"type": "string"},
                },
                "required": [
                    "raw_theme",
                    "title_candidate",
                    "driver",
                    "impact_target",
                    "risk_type",
                    "event_thread",
                    "event_action",
                    "actors",
                    "importance",
                    "impact_direction",
                    "event_stage",
                    "timeline_position",
                    "event_time",
                    "summary",
                    "supporting_text",
                ],
                "additionalProperties": False,
            },
        },
        "risk_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "risk_type": {"type": "string"},
                    "risk_direction": {
                        "type": "string",
                        "enum": ["increasing", "decreasing", "neutral", "uncertain"],
                    },
                    "risk_scope": {"type": "string"},
                    "risk_severity": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncertainty": {"type": "string"},
                    "supporting_text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "risk_type",
                    "risk_direction",
                    "risk_scope",
                    "risk_severity",
                    "uncertainty",
                    "supporting_text",
                    "confidence",
                    "importance",
                ],
                "additionalProperties": False,
            },
        },
        "local_impact_signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "local_impact_mentions": {"type": "string"},
                    "local_impact_target": {"type": "array", "items": {"type": "string"}},
                    "local_impact_direction": {
                        "type": "string",
                        "enum": ["positive", "negative", "mixed", "neutral", "uncertain"],
                    },
                    "local_impact_mechanism_text": {"type": "string"},
                    "supporting_text": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": [
                    "local_impact_mentions",
                    "local_impact_target",
                    "local_impact_direction",
                    "local_impact_mechanism_text",
                    "supporting_text",
                    "confidence",
                    "importance",
                ],
                "additionalProperties": False,
            },
        },
        "actor_signals": {
            "type": "object",
            "properties": {
                "actors": {"type": "array", "items": {"type": "string"}},
                "companies": {"type": "array", "items": {"type": "string"}},
                "industries": {"type": "array", "items": {"type": "string"}},
                "regions": {"type": "array", "items": {"type": "string"}},
                "policies": {"type": "array", "items": {"type": "string"}},
                "commodities": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["actors", "companies", "industries", "regions", "policies", "commodities"],
            "additionalProperties": False,
        },
        "supporting_text": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "title_candidates",
        "topic_intents",
        "risk_signals",
        "local_impact_signals",
        "actor_signals",
        "supporting_text",
    ],
    "additionalProperties": False,
}

ASSIGNMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["attach_existing", "create_new_l0"]},
                    "community_id": {"type": ["string", "null"]},
                    "weight": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "matched_reason": {"type": "string"},
                    "update_mode": {"type": "string", "enum": ["append_reference", "update_delta", "rewrite_summary"]},
                    "reason": {"type": "string"},
                    "new_community": {
                        "type": ["object", "null"],
                        "properties": {
                            "level": {"type": "number"},
                            "title": {"type": "string"},
                            "scope": {"type": "string"},
                            "title_quality": {"type": "string", "enum": ["broad_topic"]},
                        },
                        "required": ["level", "title", "scope", "title_quality"],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "action",
                    "community_id",
                    "weight",
                    "confidence",
                    "matched_reason",
                    "update_mode",
                    "reason",
                    "new_community",
                ],
                "additionalProperties": False,
            },
        },
        "rejected_candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "community_id": {"type": "string"},
                    "reason_code": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["community_id", "reason_code", "reason"],
                "additionalProperties": False,
            },
        },
        "maintenance_hints": {
            "type": "object",
            "properties": {
                "suggest_split": {"type": "boolean"},
                "suggest_merge_community_ids": {"type": "array", "items": {"type": "string"}},
                "reason": {"type": "string"},
            },
            "required": ["suggest_split", "suggest_merge_community_ids", "reason"],
            "additionalProperties": False,
        },
    },
    "required": ["assignments", "rejected_candidates", "maintenance_hints"],
    "additionalProperties": False,
}

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "main_signals": {"type": "array", "items": {"type": "string"}},
        "timeline": {"type": "array", "items": {"type": "string"}},
        "watch_points": {"type": "array", "items": {"type": "string"}},
        "maturity": {"type": "string", "enum": ["single_evidence", "multi_evidence", "mature_topic"]},
        "cited_source_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "schema_version",
        "title",
        "summary",
        "main_signals",
        "timeline",
        "watch_points",
        "maturity",
        "cited_source_ids",
    ],
    "additionalProperties": False,
}


@dataclass
class ChunkRecord:
    source_id: str
    evidence_id: str
    chunk_id: str
    chunk_index: int
    text: str
    start_offset: int
    end_offset: int
    previous_chunk_id: str = ""
    next_chunk_id: str = ""
    text_hash: str = ""
    chunker_version: str = "validation_recursive_zh_v1"

    def pointer_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "evidence_id": self.evidence_id,
            "primary_chunk_id": self.chunk_id,
            "chunk_ids": [self.chunk_id],
            "chunk_index": self.chunk_index,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "previous_chunk_id": self.previous_chunk_id,
            "next_chunk_id": self.next_chunk_id,
            "text_hash": self.text_hash,
            "chunker_version": self.chunker_version,
        }


@dataclass
class DraftCommunity:
    community_id: str
    title: str
    scope: str
    level: int = 0
    parent_community_id: str = ""
    summary: str = ""
    source_ids: list[str] = field(default_factory=list)
    news_ids: list[int] = field(default_factory=list)
    assigned_intents: list[dict[str, Any]] = field(default_factory=list)
    assignments: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] | None = None
    created_from_source_id: str = ""

    def signal_values(self) -> dict[str, list[str]]:
        keys = [
            "raw_theme",
            "title_candidate",
            "driver",
            "impact_target",
            "risk_type",
            "event_thread",
            "event_action",
            "actors",
        ]
        result: dict[str, list[str]] = {key: [] for key in keys}
        for card in self.assigned_intents:
            for key in keys:
                result[key].extend(_as_list(card.get(key)))
        return {key: _dedupe(values) for key, values in result.items()}

    def to_candidate_payload(self, score: float = 0.0, lane: str = "") -> dict[str, Any]:
        signals = self.signal_values()
        return {
            "community_id": self.community_id,
            "title": self.title,
            "level": self.level,
            "parent_community_id": self.parent_community_id,
            "scope": self.scope,
            "summary": self.summary,
            "source_count": len(set(self.source_ids)),
            "news_count": len(set(self.news_ids)),
            "score": round(score, 4),
            "lane": lane,
            "signals": signals,
        }

    def to_assignment_candidate_payload(self, score: float = 0.0, lane: str = "") -> dict[str, Any]:
        signals = self.signal_values()
        canonical_labels = _dedupe(
            [
                self.title,
                *signals.get("raw_theme", []),
                *signals.get("title_candidate", []),
                *signals.get("impact_target", []),
                *signals.get("event_thread", []),
                *signals.get("risk_type", []),
            ]
        )[:12]
        recent_examples = [
            {
                "source_id": str(card.get("source_id") or ""),
                "title": str(card.get("title_candidate") or card.get("raw_theme") or ""),
                "summary": clip_text(str(card.get("summary") or ""), 120),
            }
            for card in self.assigned_intents[-3:]
        ]
        return {
            "community_id": self.community_id,
            "title": self.title,
            "level": self.level,
            "parent_community_id": self.parent_community_id,
            "summary": clip_text(self.summary, 240),
            "canonical_labels": canonical_labels,
            "maturity": maturity_label(len(set(self.source_ids))),
            "retrieval_score": round(float(score or 0), 4),
            "retrieval_lane": lane,
            "recent_examples": [item for item in recent_examples if item["source_id"] or item["summary"]],
        }

    def to_output(self) -> dict[str, Any]:
        signals = self.signal_values()
        return {
            "community_id": self.community_id,
            "title": self.title,
            "level": self.level,
            "parent_community_id": self.parent_community_id,
            "scope": self.scope,
            "summary": self.summary,
            "source_count": len(set(self.source_ids)),
            "news_count": len(set(self.news_ids)),
            "signals": signals,
            "subtopics": community_subtopics(self),
            "source_ids": self.source_ids,
            "news_ids": self.news_ids,
            "created_from_source_id": self.created_from_source_id,
            "assignment_count": len(self.assignments),
            "assigned_intent_count": len(self.assigned_intents),
            "report": self.report,
        }


async def main() -> None:
    session_id = os.getenv("COMMUNITY_TOPIC_LANGFUSE_SESSION_ID", "").strip() or (
        f"kg-community-topic-validation:{datetime.now().strftime('%Y%m%d')}"
    )
    with langfuse_propagation_context(
        trace_name="kg-community-topic-validation",
        session_id=session_id,
        tags=["kg", "community-topic", "validation"],
        metadata={
            "news_limit": NEWS_LIMIT,
            "max_intents_per_news": MAX_INTENTS_PER_NEWS,
            "generate_reports": GENERATE_REPORTS,
        },
        version="community-topic-validation",
    ):
        with langfuse_observation(
            name="kg.community_topic.validation",
            as_type="span",
            input={
                "news_limit": NEWS_LIMIT,
                "max_intents_per_news": MAX_INTENTS_PER_NEWS,
                "use_fixed_ft_news": USE_FIXED_FT_NEWS,
            },
            metadata={"session_id": session_id},
        ):
            try:
                await run_validation()
                langfuse_update_span(status_message="completed")
            except Exception as exc:
                langfuse_update_span(
                    level="ERROR",
                    status_message=str(exc),
                    metadata={"error_type": exc.__class__.__name__},
                )
                raise
            finally:
                langfuse_flush()


async def run_validation() -> None:
    started = time.perf_counter()
    rows = fetch_news_rows(limit=CANDIDATE_LIMIT)
    selected = select_representative_rows(rows, limit=NEWS_LIMIT)
    print_section("Step 0. 配置")
    pprint(
        {
            "target": TARGET,
            "adapter": ADAPTER,
            "use_fixed_ft_news": USE_FIXED_FT_NEWS,
            "fixed_ft_news_count": len(FIXED_FT_NEWS_IDS),
            "candidate_limit": CANDIDATE_LIMIT,
            "news_limit": NEWS_LIMIT,
            "selected": len(selected),
            "generate_reports": GENERATE_REPORTS,
            "extraction_concurrency": EXTRACTION_CONCURRENCY,
            "max_intents_per_news": MAX_INTENTS_PER_NEWS,
            "chunk_max_chars": CHUNK_MAX_CHARS,
            "resume_from_output": RESUME_FROM_OUTPUT,
            "output_file": str(OUTPUT_FILE),
        }
    )
    if not selected:
        raise RuntimeError("ft_news 没有可用样本")

    rows_output, communities, completed_news_ids = load_resume_state() if RESUME_FROM_OUTPUT else ([], {}, set())
    pending = [row for row in selected if int(row["id"]) not in completed_news_ids]
    llm = get_llm_gateway_service()
    extraction_model = resolve_kg_llm_model("kg_cognitive_card")
    assignment_model = resolve_kg_llm_model("kg_community_assignment")
    report_model = resolve_kg_llm_model("kg_community_report")

    print_section("Step 1A. Cognitive Card 并发抽取")
    cards_by_news_id = await extract_cognitive_cards(
        llm,
        pending,
        model=extraction_model,
        concurrency=EXTRACTION_CONCURRENCY,
    )

    print_section("Step 1B. Community 顺序裁决")
    for offset, row in enumerate(pending, start=1):
        index = len(rows_output) + 1
        print(f"[{index}/{len(selected)}] news_id={row['id']} title={row['title'][:50]}")
        cards = cards_by_news_id[row["id"]]
        intents = topic_intents_from_cards(cards, row)[: max(1, MAX_INTENTS_PER_NEWS)]
        intent_results: list[dict[str, Any]] = []
        applied_community_ids: list[str] = []
        for intent_index, intent in enumerate(intents, start=1):
            print(
                f"  [intent {intent_index}/{len(intents)}] "
                f"{intent.get('title_candidate') or intent.get('raw_theme')}"
            )
            candidates, recall_plan = recall_candidates(intent, list(communities.values()))
            assignment_error = ""
            decision: dict[str, Any] | None = None
            applied = {"community_ids": [], "effective_actions": [], "update_modes": []}
            try:
                decision = await decide_assignment(
                    llm,
                    row,
                    intent,
                    candidates,
                    recall_plan=recall_plan,
                    model=assignment_model,
                )
                applied = apply_assignment(communities, row, intent, decision)
                applied_community_ids.extend(applied["community_ids"])
            except Exception as exc:
                assignment_error = str(exc)
                print(f"    [assignment validation failed] {assignment_error[:240]}")
            intent_results.append(
                {
                    "intent_index": intent_index,
                    "topic_intent": intent,
                    "recall_plan": recall_plan,
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                    "decision": decision,
                    "decision_source": "llm_assignment",
                    "assignment_error": assignment_error,
                    "applied": applied,
                }
            )
        rows_output.append(
            {
                "news_id": row["id"],
                "source_id": source_id(row),
                "title": row["title"],
                "chunk_count": len(cards),
                "cognitive_cards": cards,
                "intent_count": len(intent_results),
                "intent_results": intent_results,
                "applied": {"community_ids": _dedupe(applied_community_ids)},
            }
        )
        write_output_checkpoint(
            rows_output,
            communities,
            started=started,
            completed=False,
            stage=f"after_item_{index}",
        )

    if GENERATE_REPORTS and communities:
        print_section("Step 2. Community Report 草案")
        for community in list(communities.values())[:MAX_REPORT_COMMUNITIES]:
            print(f"[report] {community.community_id} {community.title}")
            community.report = await generate_report(llm, community, model=report_model)
            community.summary = community.report.get("summary") or community.summary

    output = build_output_payload(
        rows_output,
        communities,
        started=started,
        completed=True,
        stage="completed",
    )
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print_section("Step 3. 结果摘要")
    pprint(output["stats"])
    print(f"[output] {OUTPUT_FILE}")


def write_output_checkpoint(
    rows_output: list[dict[str, Any]],
    communities: dict[str, DraftCommunity],
    *,
    started: float,
    completed: bool,
    stage: str,
) -> None:
    output = build_output_payload(
        rows_output,
        communities,
        started=started,
        completed=completed,
        stage=stage,
    )
    OUTPUT_FILE.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def build_output_payload(
    rows_output: list[dict[str, Any]],
    communities: dict[str, DraftCommunity],
    *,
    started: float,
    completed: bool,
    stage: str,
) -> dict[str, Any]:
    return {
        "run_at": datetime.now().isoformat(),
        "duration_seconds": round(time.perf_counter() - started, 2),
        "completed": completed,
        "stage": stage,
        "config": {
            "target": TARGET,
            "adapter": ADAPTER,
            "use_fixed_ft_news": USE_FIXED_FT_NEWS,
            "fixed_ft_news_ids": list(FIXED_FT_NEWS_IDS[:NEWS_LIMIT]) if USE_FIXED_FT_NEWS else [],
            "news_limit": NEWS_LIMIT,
            "candidate_limit": CANDIDATE_LIMIT,
            "min_top_k": MIN_TOP_K,
            "default_top_k": DEFAULT_TOP_K,
            "max_top_k": MAX_TOP_K,
            "default_max_attach": DEFAULT_MAX_ATTACH,
            "complex_max_attach": COMPLEX_MAX_ATTACH,
            "extraction_concurrency": EXTRACTION_CONCURRENCY,
            "max_intents_per_news": MAX_INTENTS_PER_NEWS,
            "chunk_max_chars": CHUNK_MAX_CHARS,
            "resume_from_output": RESUME_FROM_OUTPUT,
            "assignment_mode": "llm_for_each_topic_intent",
            "cognitive_card_schema_version": COGNITIVE_CARD_SCHEMA_VERSION,
        },
        "stats": build_stats(rows_output, communities),
        "items": rows_output,
        "communities": [community.to_output() for community in communities.values()],
    }


def load_resume_state() -> tuple[list[dict[str, Any]], dict[str, DraftCommunity], set[int]]:
    if not OUTPUT_FILE.exists():
        return [], {}, set()
    data = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
    rows_output = data.get("items") or []
    if not isinstance(rows_output, list):
        return [], {}, set()
    communities: dict[str, DraftCommunity] = {}
    completed_news_ids: set[int] = set()
    for item in rows_output:
        if not isinstance(item, dict) or "intent_results" not in item:
            continue
        news_id = int(item["news_id"])
        completed_news_ids.add(news_id)
        row = {"id": news_id, "title": item.get("title") or ""}
        for result in item.get("intent_results") or []:
            if not isinstance(result, dict):
                continue
            intent = result.get("topic_intent")
            decision = result.get("decision")
            if isinstance(intent, dict) and isinstance(decision, dict):
                validate_assignment_decision(decision, assignment_candidates_from_communities(communities))
                apply_assignment(communities, row, intent, decision)
    if rows_output:
        print(f"[resume] loaded processed_news={len(completed_news_ids)} communities={len(communities)}")
    return rows_output, communities, completed_news_ids


def fetch_news_rows(*, limit: int) -> list[dict[str, Any]]:
    if USE_FIXED_FT_NEWS:
        return fetch_news_rows_by_ids(FIXED_FT_NEWS_IDS[:NEWS_LIMIT])
    with get_session(TARGET) as session:
        inspector = inspect(session.bind)
        if not inspector.has_table(News.__tablename__):
            return []
        rows = session.scalars(
            select(News)
            .order_by(News.created_at.desc().nullslast(), News.id.desc())
            .limit(max(1, int(limit)))
        ).all()
        return [news_to_row(row) for row in rows]


def fetch_news_rows_by_ids(row_ids: tuple[int, ...]) -> list[dict[str, Any]]:
    if not row_ids:
        return []
    with get_session(TARGET) as session:
        inspector = inspect(session.bind)
        if not inspector.has_table(News.__tablename__):
            return []
        rows = session.scalars(select(News).where(News.id.in_(list(row_ids)))).all()
        rows_by_id = {int(row.id): news_to_row(row) for row in rows}
        missing = [row_id for row_id in row_ids if row_id not in rows_by_id]
        if missing:
            print(f"[warn] fixed ft_news missing ids={missing}")
        return [rows_by_id[row_id] for row_id in row_ids if row_id in rows_by_id]


def news_to_row(row: News) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "title": row.title or "",
        "content": row.content or "",
        "summary": row.summary or "",
        "source": row.source or "",
        "source_name": row.source_name or "",
        "category": row.category or "",
        "tags": row.tags or [],
        "related_stocks": row.related_stocks or [],
        "published_at": row.published_at.isoformat() if row.published_at else "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
    }


def select_representative_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    usable = [row for row in rows if len(news_text(row)) >= MIN_TEXT_CHARS]
    if USE_FIXED_FT_NEWS:
        return usable[:limit]
    selected: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for row in usable:
        key = normalize_label(row["title"])
        if key in seen_titles:
            continue
        selected.append(row)
        seen_titles.add(key)
        if len(selected) >= limit:
            break
    return selected


async def extract_cognitive_card(
    llm: Any,
    row: dict[str, Any],
    chunk: ChunkRecord,
    *,
    model: str,
) -> dict[str, Any]:
    prompt = {
        "chunk_text": clip_text(chunk.text, CHUNK_MAX_CHARS + 200),
    }
    response = await llm.generate(
        LLMProxyRequest(
            model=model,
            system_prompt=COGNITIVE_CARD_SYSTEM_PROMPT,
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            temperature=0,
            max_tokens=2200,
            json_schema=COGNITIVE_CARD_SCHEMA,
            metadata={
                "task": "kg_cognitive_card",
                "source_id": source_id(row),
                "chunk_id": chunk.chunk_id,
                "chunk_index": chunk.chunk_index,
            },
            use_cache=True,
        )
    )
    card = response.structured_output
    if not isinstance(card, dict):
        raise RuntimeError(f"cognitive card output is not object: chunk_id={chunk.chunk_id}")
    card = normalize_cognitive_card_text(card)
    card = complete_local_impact_signals(card)
    validate_cognitive_card(card, chunk)
    card["schema_version"] = COGNITIVE_CARD_SCHEMA_VERSION
    card["cognitive_card_id"] = f"validation_cognitive_card:{stable_digest(chunk.chunk_id)}"
    card["news_id"] = int(row["id"])
    card["source_title"] = str(row.get("title") or "")
    card["system_pointers"] = chunk.pointer_payload()
    if not isinstance(card.get("topic_intents"), list) or not card["topic_intents"]:
        raise RuntimeError(f"cognitive card has no topic_intents: chunk_id={chunk.chunk_id}")
    return card


async def extract_cognitive_cards(
    llm: Any,
    rows: list[dict[str, Any]],
    *,
    model: str,
    concurrency: int,
) -> dict[int, list[dict[str, Any]]]:
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    tasks: list[tuple[int, int, dict[str, Any], ChunkRecord]] = []
    for row_index, row in enumerate(rows, start=1):
        chunks = build_chunk_chain(row)
        for chunk in chunks:
            tasks.append((row_index, len(rows), row, chunk))

    async def _extract(row_index: int, row_count: int, row: dict[str, Any], chunk: ChunkRecord) -> tuple[int, dict[str, Any]]:
        async with semaphore:
            print(
                f"[extract {row_index}/{row_count}] "
                f"news_id={row['id']} chunk={chunk.chunk_index} title={row['title'][:50]}"
            )
            return row["id"], await extract_cognitive_card(llm, row, chunk, model=model)

    pairs = await asyncio.gather(*[_extract(row_index, row_count, row, chunk) for row_index, row_count, row, chunk in tasks])
    result: dict[int, list[dict[str, Any]]] = {}
    for news_id, card in pairs:
        result.setdefault(news_id, []).append(card)
    for cards in result.values():
        cards.sort(key=lambda item: int((item.get("system_pointers") or {}).get("chunk_index") or 0))
    return result


def validate_cognitive_card(card: dict[str, Any], chunk: ChunkRecord) -> None:
    if not isinstance(card.get("summary"), str):
        raise RuntimeError(f"cognitive card summary must be string: chunk_id={chunk.chunk_id}; card={card}")
    title_candidates = card.get("title_candidates")
    if not isinstance(title_candidates, list) or not all(isinstance(item, str) for item in title_candidates):
        raise RuntimeError(f"title_candidates must be string array: chunk_id={chunk.chunk_id}; card={card}")
    intents = card.get("topic_intents")
    if not isinstance(intents, list) or not intents:
        raise RuntimeError(f"topic_intents must be non-empty array: chunk_id={chunk.chunk_id}; card={card}")
    required_intent_fields = [
        "raw_theme",
        "title_candidate",
        "driver",
        "impact_target",
        "risk_type",
        "event_thread",
        "event_action",
        "actors",
        "importance",
        "impact_direction",
        "event_stage",
        "timeline_position",
        "event_time",
        "summary",
        "supporting_text",
    ]
    for index, intent in enumerate(intents, start=1):
        if not isinstance(intent, dict):
            raise RuntimeError(
                f"topic_intents[{index}] must be object, not {type(intent).__name__}: "
                f"chunk_id={chunk.chunk_id}; item={intent}; card={card}"
            )
        missing = [key for key in required_intent_fields if key not in intent]
        if missing:
            raise RuntimeError(f"topic_intents[{index}] missing fields {missing}: chunk_id={chunk.chunk_id}; card={card}")
        for key in ["raw_theme", "title_candidate", "impact_direction", "event_stage", "timeline_position", "event_time", "summary", "supporting_text"]:
            if not isinstance(intent.get(key), str):
                raise RuntimeError(f"topic_intents[{index}].{key} must be string: chunk_id={chunk.chunk_id}; card={card}")
        for key in ["driver", "impact_target", "risk_type", "event_thread", "event_action", "actors"]:
            if not isinstance(intent.get(key), list) or not all(isinstance(item, str) for item in intent.get(key)):
                raise RuntimeError(f"topic_intents[{index}].{key} must be string array: chunk_id={chunk.chunk_id}; card={card}")
        importance = intent.get("importance")
        if not isinstance(importance, (int, float)) or isinstance(importance, bool) or not 0 <= float(importance) <= 1:
            raise RuntimeError(f"topic_intents[{index}].importance must be number 0..1: chunk_id={chunk.chunk_id}; card={card}")
    actor_signals = card.get("actor_signals")
    if not isinstance(actor_signals, dict):
        raise RuntimeError(f"actor_signals must be object: chunk_id={chunk.chunk_id}; card={card}")
    for key in ["actors", "companies", "industries", "regions", "policies", "commodities"]:
        if not isinstance(actor_signals.get(key), list) or not all(isinstance(item, str) for item in actor_signals.get(key)):
            raise RuntimeError(f"actor_signals.{key} must be string array: chunk_id={chunk.chunk_id}; card={card}")
    for key in ["risk_signals", "local_impact_signals", "supporting_text"]:
        if not isinstance(card.get(key), list):
            raise RuntimeError(f"{key} must be array: chunk_id={chunk.chunk_id}; card={card}")
    if contains_implementation_perspective(card.get("summary")):
        raise RuntimeError(f"cognitive card summary contains implementation perspective: chunk_id={chunk.chunk_id}; card={card}")
    for index, intent in enumerate(intents, start=1):
        if contains_implementation_perspective(intent.get("summary")):
            raise RuntimeError(f"topic_intents[{index}].summary contains implementation perspective: chunk_id={chunk.chunk_id}; card={card}")
        if contains_implementation_perspective(intent.get("supporting_text")):
            raise RuntimeError(f"topic_intents[{index}].supporting_text contains implementation perspective: chunk_id={chunk.chunk_id}; card={card}")


def normalize_cognitive_card_text(card: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(card)
    normalized["summary"] = clean_model_text(normalized.get("summary"))
    normalized["title_candidates"] = [clean_model_text(item) for item in _as_list(normalized.get("title_candidates"))]
    intents: list[dict[str, Any]] = []
    for raw_intent in normalized.get("topic_intents") or []:
        if not isinstance(raw_intent, dict):
            intents.append(raw_intent)
            continue
        intent = dict(raw_intent)
        for key in ["raw_theme", "title_candidate", "impact_direction", "event_stage", "timeline_position", "event_time", "summary", "supporting_text"]:
            intent[key] = clean_model_text(intent.get(key))
        for key in ["driver", "impact_target", "risk_type", "event_thread", "event_action", "actors"]:
            intent[key] = [clean_model_text(item) for item in _as_list(intent.get(key)) if clean_model_text(item)]
        intent["impact_target"] = clean_impact_targets(intent.get("impact_target"))
        intent["event_action"] = clean_event_actions(intent.get("event_action"))
        intents.append(intent)
    normalized["topic_intents"] = intents
    for signal_key in ["risk_signals", "local_impact_signals"]:
        signals: list[dict[str, Any]] = []
        for raw_signal in normalized.get(signal_key) or []:
            if not isinstance(raw_signal, dict):
                signals.append(raw_signal)
                continue
            signal = dict(raw_signal)
            for key, value in list(signal.items()):
                if isinstance(value, str):
                    signal[key] = clean_model_text(value)
                elif isinstance(value, list):
                    signal[key] = [clean_model_text(item) for item in value if clean_model_text(item)]
            if "local_impact_target" in signal:
                signal["local_impact_target"] = clean_impact_targets(signal.get("local_impact_target"))
            signals.append(signal)
        normalized[signal_key] = signals
    actor_signals = normalized.get("actor_signals")
    if isinstance(actor_signals, dict):
        normalized["actor_signals"] = {
            key: [clean_model_text(item) for item in _as_list(value) if clean_model_text(item)]
            for key, value in actor_signals.items()
        }
    normalized["supporting_text"] = [clean_model_text(item) for item in _as_list(normalized.get("supporting_text")) if clean_model_text(item)]
    return normalized


def complete_local_impact_signals(card: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(card)
    existing = normalized.get("local_impact_signals")
    if isinstance(existing, list) and existing:
        return normalized
    derived: list[dict[str, Any]] = []
    for intent in normalized.get("topic_intents") or []:
        if not isinstance(intent, dict):
            continue
        targets = [item for item in _as_list(intent.get("impact_target")) if item]
        direction = clean_model_text(intent.get("impact_direction"))
        if not targets or direction not in {"positive", "negative", "mixed"}:
            continue
        supporting_text = clean_model_text(intent.get("supporting_text"))
        summary = clean_model_text(intent.get("summary"))
        if not supporting_text and not summary:
            continue
        mechanisms = _dedupe([*_as_list(intent.get("driver")), *_as_list(intent.get("event_action"))])[:4]
        derived.append(
            {
                "local_impact_mentions": summary or supporting_text,
                "local_impact_target": targets,
                "local_impact_direction": direction,
                "local_impact_mechanism_text": join_terms([clean_model_text(item) for item in mechanisms if clean_model_text(item)]),
                "supporting_text": supporting_text or summary,
                "confidence": min(0.78, max(0.55, float(intent.get("importance") or 0.65))),
                "importance": min(1.0, max(0.0, float(intent.get("importance") or 0.65))),
            }
        )
    normalized["local_impact_signals"] = derived
    return normalized


def clean_model_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"^(当前\s*chunk|本\s*chunk|该\s*chunk|这段\s*chunk|当前文本|本段|该段|这段文本)"
        r"(主要)?(描述|显示|提到|指出|反映|支撑|说明|涉及)?[:：，,、\s]*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    text = re.sub(
        r"^(该|这个)?topic_intent(的)?(短)?摘要[:：，,、\s]*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def clean_impact_targets(values: Any) -> list[str]:
    result: list[str] = []
    for value in _as_list(values):
        text = clean_model_text(value)
        if not text:
            continue
        if is_weak_impact_target(text):
            continue
        result.append(text)
    return _dedupe(result)


def clean_event_actions(values: Any) -> list[str]:
    result: list[str] = []
    for value in _as_list(values):
        text = clean_model_text(value)
        if not text:
            continue
        if is_market_noise_action(text):
            continue
        result.append(text)
    return _dedupe(result)


def is_market_noise_action(text: str) -> bool:
    key = normalize_label(text)
    if not key:
        return True
    if len(key) <= 2 and any(normalize_label(term) == key for term in MARKET_NOISE_TERMS):
        return True
    return any(normalize_label(term) == key for term in MARKET_NOISE_TERMS)


def is_weak_impact_target(text: str) -> bool:
    key = normalize_label(text)
    if not key:
        return True
    return any(normalize_label(term) == key for term in WEAK_IMPACT_TARGET_TERMS)


def contains_implementation_perspective(value: Any) -> bool:
    text = str(value or "").lower()
    return any(term.lower() in text for term in IMPLEMENTATION_PERSPECTIVE_TERMS)


async def decide_assignment(
    llm: Any,
    row: dict[str, Any],
    intent: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    recall_plan: dict[str, Any],
    model: str,
) -> dict[str, Any]:
    max_attach = COMPLEX_MAX_ATTACH if recall_plan["is_complex"] else DEFAULT_MAX_ATTACH
    topic_intent = build_assignment_topic_intent(row, intent, max_attach=max_attach)
    prompt = {
        "max_attach": max_attach,
        "candidate_communities": candidates,
        "source": {
            "news_title": row["title"],
        },
        "topic_intent": topic_intent,
    }
    response = await llm.generate(
        LLMProxyRequest(
            model=model,
            system_prompt=ASSIGNMENT_SYSTEM_PROMPT,
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            temperature=0,
            max_tokens=1800,
            json_schema=ASSIGNMENT_SCHEMA,
            metadata={"task": "kg_community_assignment", "source_id": source_id(row)},
            use_cache=True,
        )
    )
    decision = response.structured_output
    if not isinstance(decision, dict):
        raise RuntimeError(f"assignment output is not object: source_id={source_id(row)}")
    validate_assignment_decision(decision, candidates)
    return decision


def build_assignment_topic_intent(row: dict[str, Any], intent_payload: dict[str, Any], *, max_attach: int) -> dict[str, Any]:
    title_candidate = normalize_display_title(intent_payload.get("title_candidate") or intent_payload.get("raw_theme"))
    raw_theme = normalize_display_title(intent_payload.get("raw_theme") or title_candidate)
    intent: dict[str, Any] = {
        "raw_theme": raw_theme,
        "title_candidate": title_candidate,
        "summary": clip_text(str(intent_payload.get("summary") or row.get("summary") or row.get("title") or ""), 280),
        "driver": _dedupe(_as_list(intent_payload.get("driver")))[:6],
        "impact_target": _dedupe(_as_list(intent_payload.get("impact_target")))[:8],
        "event_thread": _dedupe(_as_list(intent_payload.get("event_thread")))[:5],
        "importance": round(float(intent_payload.get("importance") or infer_topic_importance(intent_payload)), 2),
        "max_attach": max_attach,
    }
    optional_fields = {
        "risk_type": _dedupe(_as_list(intent_payload.get("risk_type")))[:5],
        "event_action": _dedupe(_as_list(intent_payload.get("event_action")))[:6],
        "actors": _dedupe(_as_list(intent_payload.get("actors")))[:8],
    }
    impact_direction = str(intent_payload.get("impact_direction") or "").strip()
    if impact_direction and impact_direction != "uncertain":
        intent["impact_direction"] = impact_direction
    for key, values in optional_fields.items():
        if values:
            intent[key] = values
    return intent


def topic_intents_from_cards(cards: list[dict[str, Any]], row: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    sequence = 0
    for card in cards:
        pointers = card.get("system_pointers") if isinstance(card.get("system_pointers"), dict) else {}
        for raw_intent in card.get("topic_intents") or []:
            sequence += 1
            if not isinstance(raw_intent, dict):
                raise RuntimeError(f"topic_intent must be object: source_id={source_id(row)} item={raw_intent}")
            intent = dict(raw_intent)
            intent["source_id"] = str(pointers.get("source_id") or source_id(row))
            intent["evidence_id"] = str(pointers.get("evidence_id") or "")
            intent["primary_chunk_id"] = str(pointers.get("primary_chunk_id") or "")
            intent["chunk_ids"] = _as_list(pointers.get("chunk_ids"))
            intent["chunk_index"] = int(pointers.get("chunk_index") or 0)
            intent["cognitive_card_id"] = str(card.get("cognitive_card_id") or "")
            intent["news_id"] = int(row["id"])
            intent["intent_id"] = f"{source_id(row)}:intent:{sequence}"
            intent["card_summary"] = str(card.get("summary") or "")
            result.append(intent)
    return sorted(result, key=lambda item: -float(item.get("importance") or 0))


async def generate_report(llm: Any, community: DraftCommunity, *, model: str) -> dict[str, Any]:
    prompt = {
        "community_id": community.community_id,
        "title": community.title,
        "scope": community.scope,
        "source_ids": community.source_ids,
        "assigned_intents": community.assigned_intents[:30],
    }
    response = await llm.generate(
        LLMProxyRequest(
            model=model,
            system_prompt=REPORT_SYSTEM_PROMPT,
            prompt=json.dumps(prompt, ensure_ascii=False, indent=2),
            temperature=0,
            max_tokens=1800,
            json_schema=REPORT_SCHEMA,
            metadata={"task": "kg_community_topic_report", "community_id": community.community_id},
            use_cache=True,
        )
    )
    report = response.structured_output
    if not isinstance(report, dict):
        raise RuntimeError(f"community report output is not object: community_id={community.community_id}")
    return report


def recall_candidates(intent: dict[str, Any], communities: list[DraftCommunity]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    is_complex = is_complex_topic_intent(intent)
    theme_k = 8 if is_complex else 5
    thread_k = 7 if is_complex else 3
    max_k = MAX_TOP_K if is_complex else DEFAULT_TOP_K
    max_k = min(MAX_TOP_K, max(MIN_TOP_K, max_k))
    scored: list[tuple[DraftCommunity, float, str]] = []
    for community in communities:
        theme_score = candidate_score(intent, community, lane="theme")
        thread_score = candidate_score(intent, community, lane="thread")
        if theme_score > 0:
            scored.append((community, theme_score, "theme"))
        if thread_score > 0:
            scored.append((community, thread_score, "thread"))

    theme = sorted(
        [(community, score, lane) for community, score, lane in scored if lane == "theme"],
        key=lambda item: item[1],
        reverse=True,
    )[:theme_k]
    thread = sorted(
        [(community, score, lane) for community, score, lane in scored if lane == "thread"],
        key=lambda item: item[1],
        reverse=True,
    )[:thread_k]

    merged: dict[str, tuple[DraftCommunity, float, str]] = {}
    for community, score, lane in [*theme, *thread]:
        current = merged.get(community.community_id)
        if current is None or score > current[1]:
            merged[community.community_id] = (community, score, lane)

    if len(merged) < min(MIN_TOP_K, len(communities)):
        overall = sorted(
            [
                (community, candidate_score(intent, community, lane="overall"), "overall")
                for community in communities
            ],
            key=lambda item: item[1],
            reverse=True,
        )
        for community, score, lane in overall:
            if score <= 0:
                continue
            merged.setdefault(community.community_id, (community, score, lane))
            if len(merged) >= min(MIN_TOP_K, len(communities)):
                break

    ranked = sorted(merged.values(), key=lambda item: item[1], reverse=True)[:max_k]
    candidates = [community.to_assignment_candidate_payload(score=score, lane=lane) for community, score, lane in ranked]
    return candidates, {
        "is_complex": is_complex,
        "theme_k": theme_k,
        "thread_k": thread_k,
        "max_k": max_k,
        "candidate_count": len(candidates),
    }


def candidate_score(intent: dict[str, Any], community: DraftCommunity, *, lane: str) -> float:
    signals = community.signal_values()
    if lane == "theme":
        left = labels_for(intent, ["raw_theme", "title_candidate", "impact_target", "driver", "risk_type"])
        right = labels_for(signals, ["raw_theme", "title_candidate", "impact_target", "driver", "risk_type"])
        title_text = f"{community.title} {community.scope} {community.summary}"
        return (
            overlap_score(left, right) * 0.50
            + text_hit_score(left, title_text) * 0.20
            + semantic_token_score(left, [*right, title_text]) * 0.30
        )
    if lane == "thread":
        left = labels_for(intent, ["event_thread", "actors", "event_action"])
        right = labels_for(signals, ["event_thread", "actors", "event_action"])
        title_text = f"{community.title} {community.scope} {community.summary}"
        return (
            overlap_score(left, right) * 0.50
            + text_hit_score(left, title_text) * 0.20
            + semantic_token_score(left, [*right, title_text]) * 0.30
        )
    left = labels_for(
        intent,
        ["raw_theme", "title_candidate", "impact_target", "driver", "risk_type", "event_thread", "actors", "event_action"],
    )
    right = labels_for(
        signals,
        ["raw_theme", "title_candidate", "impact_target", "driver", "risk_type", "event_thread", "actors", "event_action"],
    )
    return overlap_score(left, right) * 0.65 + semantic_token_score(left, right) * 0.35


def assignment_candidates_from_communities(communities: dict[str, DraftCommunity]) -> list[dict[str, Any]]:
    return [community.to_assignment_candidate_payload() for community in communities.values()]


def apply_assignment(
    communities: dict[str, DraftCommunity],
    row: dict[str, Any],
    intent: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    applied_ids: list[str] = []
    effective_actions: list[str] = []
    update_modes: list[str] = []
    for assignment in sorted(decision.get("assignments") or [], key=lambda item: -float(item.get("weight") or 0)):
        if assignment["action"] == "create_new_l0":
            community, created = get_or_create_community_from_assignment(communities, assignment, intent)
            effective_action = "create_new_l0" if created else "attach_existing_by_canonical_title"
        else:
            community_id = str(assignment["community_id"])
            community = communities.get(community_id)
            if community is None:
                raise RuntimeError(f"LLM selected unknown community_id={community_id}")
            effective_action = "attach_existing"
        if community.community_id in applied_ids:
            continue
        add_intent_to_community(community, row, intent, assignment)
        applied_ids.append(community.community_id)
        effective_actions.append(effective_action)
        update_modes.append(assignment["update_mode"])

    for rejected in decision.get("rejected_candidates") or []:
        community = communities.get(str(rejected.get("community_id")))
        if community is not None:
            community.rejected.append({"source_id": source_id(row), **rejected})

    return {
        "community_ids": applied_ids,
        "effective_actions": effective_actions,
        "update_modes": update_modes,
    }


def get_or_create_community_from_assignment(
    communities: dict[str, DraftCommunity],
    assignment: dict[str, Any],
    intent: dict[str, Any],
) -> tuple[DraftCommunity, bool]:
    payload = assignment.get("new_community")
    if not isinstance(payload, dict):
        raise RuntimeError(f"create_new_l0 requires validated new_community payload: assignment={assignment}")
    title = normalize_display_title(payload.get("title"))
    if not title:
        raise RuntimeError(f"create_new_l0 requires non-empty title: assignment={assignment}")
    existing = find_community_by_title(communities, title)
    if existing is not None:
        raise RuntimeError(
            f"LLM created a duplicate community title instead of attaching existing one: "
            f"title={title} existing_id={existing.community_id}"
        )
    community_id = f"validation_community:{stable_digest(title)}"
    scope = normalize_display_title(payload.get("scope"))
    community = DraftCommunity(
        community_id=community_id,
        title=title,
        scope=scope,
        summary="",
        created_from_source_id=str(intent.get("source_id") or ""),
    )
    communities[community_id] = community
    return community, True


def add_intent_to_community(
    community: DraftCommunity,
    row: dict[str, Any],
    intent: dict[str, Any],
    assignment: dict[str, Any],
) -> None:
    sid = source_id(row)
    if sid not in community.source_ids:
        community.source_ids.append(sid)
    if row["id"] not in community.news_ids:
        community.news_ids.append(row["id"])
    intent_id = str(intent.get("intent_id") or f"{sid}:intent:{len(community.assigned_intents) + 1}")
    if not any(existing.get("intent_id") == intent_id for existing in community.assigned_intents):
        community.assigned_intents.append(intent)
    community.assignments.append({"source_id": sid, "intent_id": intent_id, **assignment})
    community.summary = build_community_summary(community)


def build_community_summary(community: DraftCommunity) -> str:
    signals = community.signal_values()
    drivers = select_summary_terms(signals.get("driver", []), limit=4)
    targets = select_summary_terms(signals.get("impact_target", []), limit=5)
    actions = select_summary_terms(clean_event_actions(signals.get("event_action", [])), limit=4)
    risks = select_summary_terms(signals.get("risk_type", []), limit=3)
    threads = select_summary_terms(signals.get("event_thread", []), limit=3)

    parts: list[str] = []
    if drivers:
        parts.append(f"主要驱动包括{join_terms(drivers)}")
    if targets:
        parts.append(f"影响对象覆盖{join_terms(targets)}")
    if actions:
        parts.append(f"关键动作包括{join_terms(actions)}")
    if risks:
        parts.append(f"需要关注{join_terms(risks)}等风险线索")
    if threads:
        parts.append(f"可串联到{join_terms(threads)}等事件线")
    if not parts:
        fallback = select_summary_terms(
            [
                *_as_list(signals.get("raw_theme")),
                *_as_list(signals.get("title_candidate")),
            ],
            limit=4,
        )
        if fallback:
            parts.append(f"聚合{join_terms(fallback)}等主题线索")
    if not parts:
        return f"{community.title}用于聚合相关新闻中的主题、影响对象和后续变化。"
    summary = f"{community.title}主题下，" + "；".join(parts) + "。"
    return clean_model_text(summary)


def select_summary_terms(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    for value in _dedupe(values):
        text = clean_model_text(value)
        if not text:
            continue
        if contains_implementation_perspective(text):
            continue
        if is_market_noise_action(text):
            continue
        if normalize_label(text) in {normalize_label(item) for item in result}:
            continue
        result.append(text)
        if len(result) >= limit:
            break
    return result


def join_terms(values: list[str]) -> str:
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return "、".join(values)


def validate_assignment_decision(decision: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    required_top = ["assignments", "rejected_candidates", "maintenance_hints"]
    missing_top = [key for key in required_top if key not in decision]
    if missing_top:
        raise RuntimeError(f"assignment decision missing top-level fields: {missing_top}; decision={decision}")
    extra_top = sorted(set(decision) - set(required_top))
    if extra_top:
        raise RuntimeError(f"assignment decision has extra top-level fields: {extra_top}; decision={decision}")
    if not isinstance(decision["assignments"], list) or not decision["assignments"]:
        raise RuntimeError(f"assignments must be non-empty array; decision={decision}")
    if len(decision["assignments"]) > COMPLEX_MAX_ATTACH:
        raise RuntimeError(f"too many assignments: {len(decision['assignments'])}; decision={decision}")
    if not isinstance(decision["rejected_candidates"], list):
        raise RuntimeError(f"rejected_candidates must be array; decision={decision}")
    if not isinstance(decision["maintenance_hints"], dict):
        raise RuntimeError(f"maintenance_hints must be object; decision={decision}")

    candidate_ids = {str(candidate["community_id"]) for candidate in candidates}
    seen_assignment_keys: set[str] = set()
    create_count = 0
    for index, assignment in enumerate(decision["assignments"], start=1):
        if not isinstance(assignment, dict):
            raise RuntimeError(f"assignment must be object: {assignment}; decision={decision}")
        _validate_assignment_payload(
            assignment,
            candidates=candidate_ids,
            path=f"assignments[{index}]",
            decision=decision,
        )
        if assignment["action"] == "create_new_l0":
            create_count += 1
            dedupe_key = "create:" + normalize_label((assignment.get("new_community") or {}).get("title"))
        else:
            dedupe_key = "attach:" + str(assignment.get("community_id"))
        if dedupe_key in seen_assignment_keys:
            raise RuntimeError(f"duplicate assignment target: {dedupe_key}; decision={decision}")
        seen_assignment_keys.add(dedupe_key)
    if create_count > 1:
        raise RuntimeError(f"only one create_new_l0 assignment is allowed per intent; decision={decision}")

    for item in decision["rejected_candidates"]:
        if not isinstance(item, dict):
            raise RuntimeError(f"rejected candidate must be object: {item}; decision={decision}")
        missing_rejected = [key for key in ["community_id", "reason_code", "reason"] if key not in item]
        if missing_rejected:
            raise RuntimeError(f"rejected candidate missing fields: {missing_rejected}; item={item}")
        _validate_non_empty_string(item.get("community_id"), "rejected_candidate.community_id", decision)
        if str(item.get("community_id")) not in candidate_ids:
            raise RuntimeError(f"rejected candidate not in candidates: {item.get('community_id')}; decision={decision}")
        _validate_non_empty_string(item.get("reason_code"), "rejected_candidate.reason_code", decision)
        _validate_non_empty_string(item.get("reason"), "rejected_candidate.reason", decision)

    maintenance = decision["maintenance_hints"]
    missing_maintenance = [
        key for key in ["suggest_split", "suggest_merge_community_ids", "reason"] if key not in maintenance
    ]
    if missing_maintenance:
        raise RuntimeError(f"maintenance_hints missing fields: {missing_maintenance}; decision={decision}")
    if not isinstance(maintenance.get("suggest_split"), bool):
        raise RuntimeError(f"maintenance_hints.suggest_split must be bool; decision={decision}")
    if not isinstance(maintenance.get("suggest_merge_community_ids"), list):
        raise RuntimeError(f"maintenance_hints.suggest_merge_community_ids must be array; decision={decision}")
    for item in maintenance.get("suggest_merge_community_ids") or []:
        _validate_non_empty_string(item, "maintenance_hints.suggest_merge_community_ids[]", decision)
    if not isinstance(maintenance.get("reason"), str):
        raise RuntimeError(f"maintenance_hints.reason must be string; decision={decision}")


def _validate_assignment_payload(
    payload: dict[str, Any],
    *,
    candidates: set[str],
    path: str,
    decision: dict[str, Any],
) -> None:
    required_assignment = [
        "action",
        "community_id",
        "weight",
        "confidence",
        "matched_reason",
        "update_mode",
        "reason",
        "new_community",
    ]
    missing = [key for key in required_assignment if key not in payload]
    if missing:
        raise RuntimeError(f"{path} missing fields: {missing}; decision={decision}")
    action = payload.get("action")
    if action not in ASSIGNMENT_ACTIONS:
        raise RuntimeError(f"{path}.action invalid: {action}; decision={decision}")
    update_mode = payload.get("update_mode")
    if update_mode not in ASSIGNMENT_UPDATE_MODES:
        raise RuntimeError(f"{path}.update_mode invalid: {update_mode}; decision={decision}")
    weight = payload.get("weight")
    if not isinstance(weight, (int, float)) or isinstance(weight, bool) or not 0 <= float(weight) <= 1:
        raise RuntimeError(f"{path}.weight must be number between 0 and 1; decision={decision}")
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
        raise RuntimeError(f"{path}.confidence must be number between 0 and 1; decision={decision}")
    _validate_non_empty_string(payload.get("matched_reason"), f"{path}.matched_reason", decision)
    _validate_non_empty_string(payload.get("reason"), f"{path}.reason", decision)
    community_id = payload.get("community_id")
    new_community = payload.get("new_community")
    if action == "attach_existing":
        _validate_non_empty_string(community_id, f"{path}.community_id", decision)
        if str(community_id) not in candidates:
            raise RuntimeError(f"{path}.community_id not in candidates: {community_id}; decision={decision}")
        if new_community is not None:
            raise RuntimeError(f"{path}.new_community must be null when attaching; decision={decision}")
    else:
        if community_id is not None:
            raise RuntimeError(f"{path}.community_id must be null when creating new L0; decision={decision}")
        validate_new_community_payload(new_community, decision)

def validate_new_community_payload(payload: Any, decision: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise RuntimeError(f"create_new_l0 requires new_community object; decision={decision}")
    for key in ["level", "title", "scope", "title_quality"]:
        if key not in payload:
            raise RuntimeError(f"new_community missing field {key}; decision={decision}")
    level = payload.get("level")
    if not isinstance(level, (int, float)) or isinstance(level, bool) or float(level) != 0:
        raise RuntimeError(f"new_community.level must be numeric 0; decision={decision}")
    _validate_non_empty_string(payload.get("title"), "new_community.title", decision)
    _validate_non_empty_string(payload.get("scope"), "new_community.scope", decision)
    if payload.get("title_quality") != "broad_topic":
        raise RuntimeError(f"new_community.title_quality must be broad_topic; decision={decision}")


def _validate_non_empty_string(value: Any, field_name: str, decision: dict[str, Any]) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{field_name} must be non-empty string; decision={decision}")


def build_stats(rows_output: list[dict[str, Any]], communities: dict[str, DraftCommunity]) -> dict[str, Any]:
    intent_results = [result for item in rows_output for result in item.get("intent_results", [])]
    assignments = [
        assignment
        for item in intent_results
        for assignment in ((item.get("decision") or {}).get("assignments", []) if isinstance(item.get("decision"), dict) else [])
    ]
    actions = Counter(item["action"] for item in assignments)
    decision_sources = Counter(str(item.get("decision_source") or "unknown") for item in intent_results)
    effective_actions = Counter(
        action
        for item in intent_results
        for action in _as_list(item.get("applied", {}).get("effective_actions"))
    )
    update_modes = Counter(item["update_mode"] for item in assignments)
    maturity = Counter()
    level_distribution = Counter()
    for community in communities.values():
        level_distribution[f"L{community.level}"] += 1
        if community.report and community.report.get("maturity"):
            maturity[str(community.report["maturity"])] += 1
        elif len(set(community.source_ids)) <= 1:
            maturity["single_evidence"] += 1
        elif len(set(community.source_ids)) <= 5:
            maturity["multi_evidence"] += 1
        else:
            maturity["mature_topic"] += 1
    return {
        "news_count": len(rows_output),
        "intent_count": len(intent_results),
        "assignment_count": len(assignments),
        "assignment_validation_error_count": sum(1 for item in intent_results if item.get("assignment_error")),
        "community_count": len(communities),
        "community_level_distribution": dict(level_distribution),
        "community_split_count": 0,
        "assignment_actions": dict(actions),
        "decision_sources": dict(decision_sources),
        "effective_actions": dict(effective_actions),
        "assignment_update_modes": dict(update_modes),
        "community_maturity": dict(maturity),
        "multi_attach_news": sum(1 for item in rows_output if len(item["applied"]["community_ids"]) > 1),
        "multi_intent_news": sum(1 for item in rows_output if int(item.get("intent_count") or 0) > 1),
        "communities": [
            {
                "title": community.title,
                "source_count": len(set(community.source_ids)),
                "news_count": len(set(community.news_ids)),
                "maturity": (
                    community.report.get("maturity")
                    if community.report and community.report.get("maturity")
                    else ("single_evidence" if len(set(community.source_ids)) <= 1 else "multi_evidence")
                ),
                "subtopic_count": len(community_subtopics(community)),
            }
            for community in communities.values()
        ],
    }


def community_subtopics(community: DraftCommunity) -> list[dict[str, Any]]:
    values: list[str] = []
    for card in community.assigned_intents:
        values.extend(_as_list(card.get("title_candidate")))
        values.extend(_as_list(card.get("raw_theme")))
        values.extend(_as_list(card.get("event_thread")))
    community_key = normalize_label(community.title)
    result: list[dict[str, Any]] = []
    for value in _dedupe(values):
        raw_title = normalize_display_title(value)
        canonical_title = canonicalize_broad_title(raw_title)
        if not raw_title:
            continue
        if normalize_label(raw_title) == community_key:
            continue
        title = raw_title if normalize_label(canonical_title) == community_key else canonical_title
        if not title or normalize_label(title) == community_key:
            continue
        result.append({"title": title, "source_count": subtopic_source_count(community, raw_title)})
    return result[:12]


def subtopic_source_count(community: DraftCommunity, value: str) -> int:
    key = normalize_label(value)
    sources = {
        str(card.get("source_id") or "")
        for card in community.assigned_intents
        if any(
            key == normalize_label(candidate) or key in normalize_label(candidate) or normalize_label(candidate) in key
            for candidate in [
                *_as_list(card.get("title_candidate")),
                *_as_list(card.get("raw_theme")),
                *_as_list(card.get("event_thread")),
            ]
        )
    }
    return len({item for item in sources if item})


def is_complex_topic_intent(intent: dict[str, Any]) -> bool:
    return (
        len(_as_list(intent.get("impact_target"))) >= 4
        or len(_as_list(intent.get("actors"))) >= 4
        or (bool(_as_list(intent.get("risk_type"))) and bool(_as_list(intent.get("event_thread"))))
    )


def infer_topic_importance(intent: dict[str, Any]) -> float:
    score = 0.58
    if _as_list(intent.get("raw_theme")) or _as_list(intent.get("title_candidate")):
        score += 0.1
    if _as_list(intent.get("impact_target")):
        score += 0.1
    if _as_list(intent.get("driver")):
        score += 0.08
    if _as_list(intent.get("event_thread")):
        score += 0.08
    if _as_list(intent.get("risk_type")):
        score += 0.06
    return round(min(0.95, score), 2)


def maturity_label(source_count: int) -> str:
    if source_count <= 1:
        return "single_evidence"
    if source_count <= 5:
        return "multi_evidence"
    return "mature_topic"


def broad_title_from_card(intent: dict[str, Any]) -> str:
    parent_topic = parent_topic_from_intent(intent)
    if parent_topic:
        return parent_topic
    candidates = [normalize_display_title(item) for item in _as_list(intent.get("title_candidate"))]
    for candidate in candidates:
        candidate = canonicalize_broad_title(candidate)
        if is_broad_title(candidate):
            return candidate
    themes = [normalize_display_title(item) for item in _as_list(intent.get("raw_theme"))]
    threads = [normalize_display_title(item) for item in _as_list(intent.get("event_thread"))]
    targets = [normalize_display_title(item) for item in _as_list(intent.get("impact_target"))]
    for candidate in [*themes, *threads, *targets]:
        candidate = canonicalize_broad_title(candidate)
        if is_broad_title(candidate):
            return candidate
    parts = [part for part in [*(themes[:1]), *(targets[:1])] if part]
    return canonicalize_broad_title(" / ".join(parts)) if parts else "未命名金融主题"


def is_broad_title(title: str) -> bool:
    if not title or len(title) < 4:
        return False
    if len(title) > 24:
        return False
    specific_markers = (
        "公司",
        "项目",
        "建厂",
        "签约",
        "发布",
        "公告",
        "季度",
        "一季度",
        "半年报",
        "午盘",
        "早盘",
        "收盘",
        "最近",
        "今日",
        "盘中",
    )
    return not any(marker in title for marker in specific_markers)


def canonicalize_broad_title(title: str) -> str:
    text = normalize_display_title(title)
    replacements = (
        ("市场动态", ""),
        ("行业动态", ""),
        ("主题动态", ""),
        ("相关动态", ""),
        ("相关事件", ""),
        ("事件线索", ""),
        ("市场主线", ""),
    )
    for old, new in replacements:
        text = text.replace(old, new)
    text = re.sub(r"\s+", "", text)
    for parent, required, optional in PARENT_TOPIC_RULES:
        if _matches_topic_rule(text, required, optional):
            return parent
    return text.strip(" /，。；;、")


def parent_topic_from_intent(intent: dict[str, Any]) -> str:
    weighted_fields = (
        ("title_candidate", 3.0),
        ("raw_theme", 3.0),
        ("event_thread", 4.0),
        ("risk_type", 1.5),
        ("driver", 1.5),
        ("impact_target", 1.0),
        ("actors", 1.0),
        ("event_action", 1.2),
    )
    scored: list[tuple[float, str]] = []
    all_text = normalize_label(" ".join(labels_for(intent, [field for field, _ in weighted_fields])))
    for parent, required, optional in PARENT_TOPIC_RULES:
        if optional and not any(keyword_in_text(item, all_text) for item in optional):
            continue
        score = 0.0
        required_score = 0.0
        for field, weight in weighted_fields:
            for label in _as_list(intent.get(field)):
                text = normalize_label(label)
                if any(keyword_in_text(item, text) for item in required):
                    score += weight
                    required_score += weight
                if optional and any(keyword_in_text(item, text) for item in optional):
                    score += weight * 0.25
        if required_score <= 0:
            continue
        if score > 0:
            scored.append((score, parent))
    if not scored:
        return ""
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _matches_topic_rule(text: str, required: tuple[str, ...], optional: tuple[str, ...]) -> bool:
    lowered = normalize_label(text)
    if not any(keyword_in_text(item, lowered) for item in required):
        return False
    if optional and not any(keyword_in_text(item, lowered) for item in optional):
        return False
    return True


def semantic_token_score(left: list[str], right: list[str]) -> float:
    left_tokens = semantic_tokens(left)
    right_tokens = semantic_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    return len(overlap) / max(len(left_tokens), len(right_tokens))


def semantic_tokens(values: list[str]) -> set[str]:
    text = normalize_label(" ".join(str(item) for item in values))
    tokens: set[str] = set()
    for token, keywords in SEMANTIC_TOKEN_GROUPS.items():
        if any(keyword_in_text(keyword, text) for keyword in keywords):
            tokens.add(token)
    return tokens


def keyword_in_text(keyword: str, normalized_text: str) -> bool:
    key = normalize_label(keyword)
    if not key:
        return False
    if re.fullmatch(r"[a-z0-9.+#-]{1,4}", key):
        return re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", normalized_text) is not None
    return key in normalized_text


def find_community_by_title(communities: dict[str, DraftCommunity], title: str) -> DraftCommunity | None:
    normalized = normalize_label(title)
    for community in communities.values():
        if normalize_label(community.title) == normalized:
            return community
    return None


def labels_for(payload: dict[str, Any], keys: list[str]) -> list[str]:
    values: list[str] = []
    for key in keys:
        values.extend(_as_list(payload.get(key)))
    return _dedupe(values)


def overlap_score(left: list[str], right: list[str]) -> float:
    left_set = {normalize_label(item) for item in left if normalize_label(item)}
    right_set = {normalize_label(item) for item in right if normalize_label(item)}
    if not left_set or not right_set:
        return 0.0
    overlap = left_set & right_set
    if not overlap:
        partial = 0
        for l_item in left_set:
            for r_item in right_set:
                if l_item in r_item or r_item in l_item:
                    partial += 1
                    break
        return partial / max(len(left_set), len(right_set)) * 0.65
    return len(overlap) / max(len(left_set), len(right_set))


def text_hit_score(labels: list[str], text: str) -> float:
    if not labels or not text:
        return 0.0
    normalized_text = normalize_label(text)
    hits = sum(1 for label in labels if normalize_label(label) and normalize_label(label) in normalized_text)
    return hits / max(1, len(labels))


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = normalize_display_title(value)
        key = normalize_label(item)
        if not item or key in seen:
            continue
        result.append(item)
        seen.add(key)
    return result


def first_non_empty(values: list[str]) -> str:
    for value in values:
        text = normalize_display_title(value)
        if text:
            return text
    return ""


def normalize_label(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def normalize_display_title(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text.strip(" ，。；;、/")


def stable_digest(text: str) -> str:
    return hashlib.sha1(normalize_label(text).encode("utf-8")).hexdigest()[:16]


def source_id(row: dict[str, Any]) -> str:
    return f"ft_news:{row['id']}"


def news_text(row: dict[str, Any]) -> str:
    return "\n".join(part for part in [row.get("title"), row.get("summary"), row.get("content")] if part)


def build_chunk_chain(row: dict[str, Any]) -> list[ChunkRecord]:
    text = str(row.get("content") or "").strip()
    if not text:
        text = news_text(row)
    if not text:
        raise RuntimeError(f"news has no text: news_id={row.get('id')}")
    source = source_id(row)
    evidence = f"validation_evidence:{stable_digest(source)}"
    spans = split_text_spans(text, max_chars=CHUNK_MAX_CHARS)
    chunks: list[ChunkRecord] = []
    for index, (start, end, chunk_text) in enumerate(spans):
        chunk_id = f"validation_chunk:{stable_digest(f'{evidence}:{index}:{start}:{end}')}"
        chunks.append(
            ChunkRecord(
                source_id=source,
                evidence_id=evidence,
                chunk_id=chunk_id,
                chunk_index=index,
                text=chunk_text,
                start_offset=start,
                end_offset=end,
                text_hash=hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
            )
        )
    for index, chunk in enumerate(chunks):
        if index > 0:
            chunk.previous_chunk_id = chunks[index - 1].chunk_id
        if index < len(chunks) - 1:
            chunk.next_chunk_id = chunks[index + 1].chunk_id
    return chunks


def split_text_spans(text: str, *, max_chars: int) -> list[tuple[int, int, str]]:
    max_chars = max(300, int(max_chars))
    if len(text) <= max_chars:
        return [(0, len(text), text)]
    separators = ["\n\n", "\n", "。", "；", "，"]
    spans: list[tuple[int, int, str]] = []
    cursor = 0
    while cursor < len(text):
        hard_end = min(len(text), cursor + max_chars)
        end = hard_end
        window = text[cursor:hard_end]
        for sep in separators:
            pos = window.rfind(sep)
            if pos >= max_chars * 0.45:
                end = cursor + pos + len(sep)
                break
        chunk_text = text[cursor:end].strip()
        if chunk_text:
            start = cursor + (len(text[cursor:end]) - len(text[cursor:end].lstrip()))
            spans.append((start, end, chunk_text))
        cursor = end
    return spans


def clip_text(text: str, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def print_section(title: str) -> None:
    print("\n" + "=" * 12 + f" {title} " + "=" * 12)


COGNITIVE_CARD_SYSTEM_PROMPT = """你是金融知识图谱的 Cognitive Card 抽取器。

任务：把当前 chunk text 抽成局部认知信号，供 Community、事件流、风险簇、影响链等高阶索引复用。

要求：
- 只基于当前 chunk text，不添加外部事实，不跨 chunk 推断全局结论。
- 不要输出 source_id、evidence_id、chunk_id、offset、previous_chunk_id、next_chunk_id、text_hash、chunker_version；这些证据定位字段由系统注入。
- 必须输出 topic_intents，数量 1-10 个；每个 topic_intent 表示当前 chunk 支撑的一个主题意图。
- topic_intents 必须是对象数组，禁止输出字符串数组。
- 每个 topic_intent 对象必须包含 raw_theme、title_candidate、driver、impact_target、risk_type、event_thread、event_action、actors、importance、impact_direction、event_stage、timeline_position、event_time、summary、supporting_text。
- raw_theme / event_thread 必须是可复用的大词，不要直接照抄新闻标题。
- title_candidate 必须是适合作为 community 的大词标题。
- impact_target 只写当前 chunk 明确提到的行业、资产、公司、商品、产业链环节。
- event_thread 用于给后续事件流提供局部线索，例如“特朗普关税政策”“新能源海外产能布局”。
- timeline_position / event_time 继续抽取并保留给后续事件流；但它们不会传给 Community Assignment LLM。
- risk_signals 只抽当前 chunk 明确支撑的风险线索；证据不足时保守输出。
- local_impact_signals 只抽当前 chunk 明确提到的局部影响线索，不要改写成完整影响链。
- 如果 topic_intents 中任一 intent 的 impact_target 非空，必须尝试输出 local_impact_signals；只有原文没有影响方向或影响机制时才允许为空。
- 当文本出现“第一/第二/第三”“一方面/另一方面”“从...看”“主要包括”等多条变化、多个影响对象或多个机制时，应拆成多个 topic_intents，而不是压成一个总主题。
- summary、topic_intent.summary、local_impact_mentions 不允许出现“当前chunk”“当前 chunk”“本chunk”“该chunk”“这段chunk”等实现视角词。
- actor_signals 只抽当前 chunk 明确出现的主体、公司、行业、区域、政策、商品。
- supporting_text 只写当前 chunk 中支撑判断的短句，不要整段复制。
- title_candidates 给 3-5 个全局候选大词标题。
- title_candidates 第一个值应优先给父主题大词；如果当前 chunk 只能支撑细主题，可以先给细主题，但必须避免新闻标题化。
- title_candidates 必须是主题名，不要使用新闻标题、单一公司项目名、单一交易名、盘面描述或“动态/事件/项目/公告”这类尾词。
- 标题质量判断标准：可被多条新闻复用、能表达市场主线、不是单一主体动作、不是单条新闻标题、不是短期盘面描述。
- 示例只用于边界说明，不是候选集合：主题名可以是“某行业链”“某政策影响”“某地缘风险”；新闻标题化表达和单一项目名不合格。
- 只有当主题驱动、影响对象、事件线或风险类型明显不同，才拆成多个 topic_intents；不要为了凑数量过拆。
- 不要输出 primary / secondary 之类主次判断；主题强弱由后续 assignment weight 表达。
- 输出必须符合 JSON Schema，不要 Markdown。

输出模板必须稳定遵守：
{
  "summary": "面向业务的事实摘要，不包含当前chunk等实现视角词",
  "title_candidates": ["可复用父主题大词", "可选细主题"],
  "topic_intents": [
    {
      "raw_theme": "当前文本支撑的主题表达",
      "title_candidate": "适合归档的可复用主题名",
      "driver": ["明确提到的驱动因素"],
      "impact_target": ["明确提到的影响对象"],
      "risk_type": [],
      "event_thread": ["可能复用的事件线名称"],
      "event_action": ["明确提到的动作"],
      "actors": ["明确出现的主体"],
      "importance": 0.0,
      "impact_direction": "positive / negative / mixed / uncertain",
      "event_stage": "当前文本能支撑的事件阶段；不确定写 uncertain",
      "timeline_position": "trigger / reaction / escalation / deescalation / resolution / follow_up / uncertain",
      "event_time": "明确提到的事件时间；没有则空字符串",
      "summary": "面向业务事实的主题意图摘要",
      "supporting_text": "支撑该判断的原文短句"
    }
  ],
  "risk_signals": [
    {
      "risk_type": "明确提到的风险类型；没有风险线索时整个 risk_signals 用空数组",
      "risk_direction": "increasing / decreasing / neutral / uncertain",
      "risk_scope": "风险影响范围",
      "risk_severity": 0.0,
      "uncertainty": "不确定性来源；没有则空字符串",
      "supporting_text": "支撑风险判断的原文短句",
      "confidence": 0.0,
      "importance": 0.0
    }
  ],
  "local_impact_signals": [
    {
      "local_impact_mentions": "明确出现的影响描述",
      "local_impact_target": ["明确提到的影响对象"],
      "local_impact_direction": "positive / negative / mixed / neutral / uncertain",
      "local_impact_mechanism_text": "原文中的局部影响机制短句",
      "supporting_text": "支撑影响判断的原文短句",
      "confidence": 0.0,
      "importance": 0.0
    }
  ],
  "actor_signals": {
    "actors": [],
    "companies": [],
    "industries": [],
    "regions": [],
    "policies": [],
    "commodities": []
  },
  "supporting_text": ["最关键的原文支撑短句"]
}
"""

ASSIGNMENT_SYSTEM_PROMPT = """你是金融知识图谱的 Community 归档裁决器。

你会收到 compact topic_intent、轻量新闻标题，以及系统召回的候选 L0/L1/L2 community。

你的任务：
- 在候选 community 中判断是否应该挂入已有主题；
- 如果候选都不适合，第一阶段只能创建新的 L0 community；
- 一个 topic_intent 可以归属到一个或多个 community；
- 不区分 primary / secondary；
- 每条归属必须输出 weight，表示这个 topic_intent 和 community 的关联强度；
- assignments 数量不要超过 max_attach 限制；
- 不要输出 uncertain，不走人工 pending；
- 低置信也必须在 attach_existing 或 create_new_l0 中二选一；
- 如果创建新 L0，title 必须是大词，不能是新闻标题、公司项目名、单一交易名；
- 如果当前 topic_intent 是子主题，优先挂入已有 L1/L2；如果没有合适子层级，再挂入或新建父主题 L0。
- 不要直接为单条新消息创建 L1/L2；L1/L2 由 Community Maintenance 从 L0 历史 Cognitive Cards 中拆分生成。
- 不要因为两个 topic_intent 出现在同一条新闻里，就把它们互相归入对方的 community；归属必须由当前 topic_intent 自身的 raw_theme、title_candidate、driver、impact_target、event_thread、actors 支持。
- maintenance_hints 只提示 split / merge，不直接执行。
- 输入不会包含 timeline_position / event_time；这两个字段不参与 community 归属裁决。
- 不要要求完整新闻正文，也不要因为缺少时间线字段而降低归属判断质量。

update_mode 选择：
- append_reference：只是补充材料，不改变主题理解；
- update_delta：代表近期新增、升级、反复出现的变化；
- rewrite_summary：改变了主题主线，需要重写概览。

输出必须符合 JSON Schema，不要 Markdown。

强制输出格式：
- 顶层必须且只能包含 assignments、rejected_candidates、maintenance_hints。
- 禁止把 attach_existing、create_new_l0、create_new 作为顶层字段。
- assignments、rejected_candidates 必须是数组。
- assignments 数组元素必须是对象；每个对象必须包含 action、community_id、weight、confidence、matched_reason、update_mode、reason、new_community。
- weight 表示归属强度，不是置信度；confidence 表示你对这个裁决的确定程度。
- weight 建议：同一父主题或同一事件线 0.85-1.0；同一产业/政策链条但角度不同 0.65-0.84；低于 0.65 的弱相关候选不要输出为 assignment。
- rejected_candidates 数组元素必须是对象，不能是字符串；每个对象必须包含 community_id、reason_code、reason。
- 如果 assignment.action=create_new_l0，assignment.new_community 必须是对象，且包含 level/title/scope/title_quality。
- 如果 assignment.action=create_new_l0，assignment.new_community.level 必须输出数字 0，不能输出 "L0"、"theme" 等字符串。
- 如果 assignment.action=create_new_l0，assignment.new_community.title_quality 必须输出 "broad_topic"，不能输出 "good"、"direct_match" 等其他值。
- 如果 assignment.action=create_new_l0，assignment.new_community.title 优先使用 Topic Intent 的父主题大词；不要把子主题当 L0。
- 如果 assignment.action=attach_existing，assignment.new_community 必须是 null。

输出模板必须稳定遵守：
{
  "assignments": [
    {
      "action": "attach_existing 或 create_new_l0",
      "community_id": "attach_existing 时必须是候选 community_id；create_new_l0 时为 null",
      "weight": 0.0,
      "confidence": 0.0,
      "matched_reason": "same_event_thread / shared_raw_theme / shared_impact_target / no_suitable_candidate 等",
      "update_mode": "append_reference / update_delta / rewrite_summary",
      "reason": "简短说明",
      "new_community": null
    }
  ],
  "rejected_candidates": [],
  "maintenance_hints": {
    "suggest_split": false,
    "suggest_merge_community_ids": [],
    "reason": ""
  }
}
"""

REPORT_SYSTEM_PROMPT = """你是金融知识图谱的 Community Report 生成器。

你会收到一个 community 草案和它引用的 assigned topic_intents。

任务：
- 生成能让上层系统快速理解该主题的概览；
- 不要把单条新闻标题当作主题标题；
- cited_source_ids 必须来自输入 source_ids；
- timeline 只写输入中能支持的事件进展；
- maturity 按证据数量判断：1 条为 single_evidence，2-5 条为 multi_evidence，更多且主线稳定为 mature_topic。

输出必须符合 JSON Schema，不要 Markdown。
"""


if __name__ == "__main__":
    asyncio.run(main())
