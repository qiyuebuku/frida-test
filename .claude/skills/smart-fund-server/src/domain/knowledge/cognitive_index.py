"""Cognitive-card first community index.

This module owns the formal write-side flow:

    EvidenceChunk -> CognitiveCard -> CommunityCard

It intentionally does not depend on node/edge graph facts. Node/edge facts may
exist elsewhere in the KG, but community topics are built from chunk-level
cognitive signals.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Any

from src.domain.knowledge.graph_index import GraphIndexCommunity, GraphIndexVectorDocument
from src.domain.knowledge.schemas import EvidenceChunk


DEFAULT_MAX_ATTACH = 3
COMPLEX_MAX_ATTACH = 5
MAX_SEMANTIC_ASSIGNMENT_CANDIDATES = 50
MAX_ASSIGNMENT_CANDIDATES = 12
RERANK_MIN_ASSIGNMENT_CANDIDATES = MAX_ASSIGNMENT_CANDIDATES + 1
COGNITIVE_CARD_MAX_TOKENS = 5000
ASSIGNMENT_MAX_TOKENS = 5000
COGNITIVE_CARD_SCHEMA_VERSION = "cognitive_card_v1"
COMMUNITY_ASSIGNMENT_SCHEMA_VERSION = "community_assignment_v2"
COMMUNITY_PROJECTION = "cognitive_topic"
ASSIGNMENT_FIT_TYPES = {
    "existing_direction",
    "new_subtopic",
    "broader_parent",
    "adjacent_context",
    "new_parent_topic",
}
ASSIGNMENT_ACTIONS = {
    "attach_existing",
    "create_new",
    "create_parent_and_absorb_existing",
}


@dataclass(frozen=True)
class CognitiveCard:
    cognitive_card_id: str
    adapter_name: str
    source_type: str
    source_id: str
    evidence_id: str
    primary_chunk_id: str
    chunk_ids: list[str]
    chunk_index: int
    summary: str
    title_candidates: list[str]
    topic_intents: list[dict[str, Any]]
    risk_signals: list[dict[str, Any]]
    local_impact_signals: list[dict[str, Any]]
    actor_signals: dict[str, Any]
    supporting_text: list[str]
    system_pointers: dict[str, Any]
    payload: dict[str, Any]
    schema_version: str = COGNITIVE_CARD_SCHEMA_VERSION
    status: str = "active"


@dataclass(frozen=True)
class CommunityAssignment:
    assignment_id: str
    adapter_name: str
    cognitive_card_id: str
    intent_index: int
    intent_id: str
    community_id: str
    action: str
    weight: float
    confidence: float
    matched_reason: str
    update_mode: str
    reason: str
    topic_intent: dict[str, Any]
    decision: dict[str, Any]
    status: str = "active"


@dataclass
class CommunityDraft:
    community_id: str
    title: str
    scope: str
    origin: str = "emergent"
    level: int = 0
    parent_community_id: str = ""
    summary: str = ""
    include_rules: list[str] = field(default_factory=list)
    exclude_rules: list[str] = field(default_factory=list)
    canonical_labels: list[str] = field(default_factory=list)
    granularity_note: str = ""
    source_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    cognitive_card_ids: list[str] = field(default_factory=list)
    assigned_intents: list[dict[str, Any]] = field(default_factory=list)
    assignments: list[dict[str, Any]] = field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = field(default_factory=list)
    future_coverage: list[str] = field(default_factory=list)
    created_from_source_id: str = ""
    absorbed_community_ids: list[str] = field(default_factory=list)

    def signal_values(self) -> dict[str, list[str]]:
        keys = (
            "raw_theme",
            "title_candidate",
            "parent_themes",
            "broad_topics",
            "mid_topics",
            "specific_topics",
            "driver",
            "impact_target",
            "risk_type",
            "event_thread",
            "event_action",
            "actors",
        )
        result: dict[str, list[str]] = {key: [] for key in keys}
        for intent in self.assigned_intents:
            for key in keys:
                result[key].extend(_as_list(intent.get(key)))
        return {key: _dedupe(values) for key, values in result.items()}

    def to_assignment_candidate(self, *, score: float = 0.0, lane: str = "") -> dict[str, Any]:
        signals = self.signal_values()
        labels = _community_canonical_labels(self, signals)[:16]
        return {
            "community_id": self.community_id,
            "title": self.title,
            "origin": self.origin,
            "level": self.level,
            "parent_community_id": self.parent_community_id,
            "scope": _clip(self.scope, 220),
            "include_rules": [_clip(item, 120) for item in self.include_rules[:6]],
            "exclude_rules": [_clip(item, 120) for item in self.exclude_rules[:6]],
            "granularity_note": _clip(self.granularity_note, 180),
            "summary": _clip(self.summary, 240),
            "source_count": len(set(self.source_ids)),
            "assigned_intent_count": len(self.assigned_intents),
            "directory_scope": _clip(self.scope or _community_coverage_contract(self, signals), 260),
            "parent_themes": signals.get("parent_themes", [])[:8],
            "broad_topics": signals.get("broad_topics", [])[:8],
            "mid_topics": signals.get("mid_topics", [])[:10],
            "specific_topics": signals.get("specific_topics", [])[:10],
            "future_coverage": _community_future_coverage(self.future_coverage, signals, limit=12),
            "coverage_contract": _community_coverage_contract(self, signals),
            "coverage_summary": _candidate_coverage_summary(signals, self.future_coverage),
            "canonical_labels": labels,
            "maturity": "seed_reference" if self.origin == "seed" and not self.source_ids else maturity_label(len(set(self.source_ids))),
            "retrieval_score": round(float(score or 0), 4),
            "retrieval_lane": lane,
            "recent_examples": [
                {
                    "title": str(
                        (
                            _as_list(intent.get("broad_topics"))[:1]
                            or _as_list(intent.get("mid_topics"))[:1]
                            or [intent.get("title_candidate") or intent.get("raw_theme") or ""]
                        )[0]
                    ),
                    "summary": _clip(str(intent.get("summary") or ""), 120),
                }
                for intent in self.assigned_intents[-3:]
            ],
        }


@dataclass(frozen=True)
class SeedCommunityDefinition:
    title: str
    scope: str
    include_rules: tuple[str, ...]
    exclude_rules: tuple[str, ...]
    canonical_labels: tuple[str, ...]
    granularity_note: str


SEED_COMMUNITY_DEFINITIONS: tuple[SeedCommunityDefinition, ...] = (
    SeedCommunityDefinition(
        title="地缘政治与能源风险",
        scope="承接地缘冲突、军事安全、外交摩擦、制裁、航运通道、油气供应、能源价格冲击及其对市场风险偏好的影响。",
        include_rules=(
            "中东、俄乌、红海、霍尔木兹海峡等地缘冲突、军事安全、外交摩擦或通道风险。",
            "制裁、反制裁、供应中断、油气运输受阻、能源价格冲击。",
            "地缘或安全事件对权益、债券、商品、外汇、供应链或风险偏好的传导。",
        ),
        exclude_rules=(
            "单纯公司业绩、常规能源项目投产，不归入本主题。",
            "没有地缘、安全或能源供应冲击含义的普通商品价格波动，不归入本主题。",
        ),
        canonical_labels=("地缘风险", "军事安全", "能源安全", "油气供应", "航运通道风险", "制裁影响"),
        granularity_note="这是 L0 长期风险主线；具体国家、军事合作、地区安全事件、海峡、制裁轮次和油气品种作为子方向进入。",
    ),
    SeedCommunityDefinition(
        title="公司业绩与产业景气",
        scope="承接上市公司业绩、订单、盈利能力、产能利用率、行业景气度和产业经营趋势。",
        include_rules=(
            "财报、业绩预告、收入利润变化、订单和产能利用率。",
            "行业景气上行或下行、库存周期、需求恢复或疲弱。",
            "公司经营变化能反映产业趋势或交易认知变化。",
        ),
        exclude_rules=(
            "纯融资、上市、并购交易优先归入资本市场改革或并购相关新主题。",
            "纯政策文件且没有经营结果，不归入本主题。",
        ),
        canonical_labels=("公司业绩", "行业景气", "订单变化", "盈利能力", "经营趋势"),
        granularity_note="这是 L0 经营基本面主线；单家公司和单个财报季作为子方向进入。",
    ),
    SeedCommunityDefinition(
        title="AI算力链",
        scope="承接 AI 芯片、算力硬件、光模块、服务器、数据中心、算电协同、AI 应用需求对硬件链条的拉动。",
        include_rules=(
            "AI 芯片供需、算力基础设施、服务器、光模块、CPO、数据中心。",
            "AI 应用扩张带来的算力需求、硬件瓶颈、产业链机会。",
            "算电协同、绿电支撑数据中心、AI 基础设施资本开支。",
        ),
        exclude_rules=(
            "与算力链无关的泛 AI 软件、影视 IP、营销应用，不强行归入。",
            "普通半导体政策或消费电子事件，只有无 AI 算力链含义时不归入。",
        ),
        canonical_labels=("AI算力链", "人工智能基础设施", "AI芯片", "光模块", "数据中心", "算电协同"),
        granularity_note="这是 L0 科技基础设施主线；AI芯片、光模块、数据中心和算电协同作为子方向进入。",
    ),
    SeedCommunityDefinition(
        title="政策监管与产业扶持",
        scope="承接宏观政策、产业政策、监管规则、财政金融支持、区域扶持政策及其产业影响。",
        include_rules=(
            "国务院、部委、地方政府、监管部门发布的产业扶持或监管政策。",
            "贷款贴息、税费支持、市场监管、行业准入、合规约束。",
            "政策对行业、企业、区域产业和资本市场行为的影响。",
        ),
        exclude_rules=(
            "纯公司经营结果且没有政策或监管驱动，不归入本主题。",
            "单一海外地缘制裁优先归入地缘政治与能源风险。",
        ),
        canonical_labels=("政策监管", "产业扶持", "财政金融政策", "地方政策", "监管规则"),
        granularity_note="这是 L0 政策主线；具体政策文件、监管动作和地方产业政策作为子方向进入。",
    ),
    SeedCommunityDefinition(
        title="大宗商品供需冲击",
        scope="承接铜、油气、工业气体、农产品、贵金属等商品供需、价格、库存、运输和上游约束变化。",
        include_rules=(
            "商品价格大幅波动、供应短缺、库存变化、运输和生产瓶颈。",
            "上游资源约束对中下游行业成本和盈利的影响。",
            "商品供需变化对通胀、产业链和市场风险偏好的传导。",
        ),
        exclude_rules=(
            "没有商品供需含义的普通公司公告，不归入本主题。",
            "纯地缘导致的油气通道风险优先归入地缘政治与能源风险，并可同时归入本主题。",
        ),
        canonical_labels=("大宗商品", "供需冲击", "价格波动", "资源约束", "库存周期"),
        granularity_note="这是 L0 商品主线；具体品种和单次价格变化作为子方向进入。",
    ),
    SeedCommunityDefinition(
        title="资本市场改革",
        scope="承接资本市场制度改革、区域股权市场、IPO、并购重组、上市融资、投行业务和证券行业生态变化。",
        include_rules=(
            "IPO、再融资、并购重组、区域股权市场、交易制度和上市规则。",
            "券商投行、私募股权、资本市场服务实体经济。",
            "制度变化对上市公司质量、市场活跃度和风险定价的影响。",
        ),
        exclude_rules=(
            "单家公司普通业绩优先归入公司业绩与产业景气。",
            "没有资本市场制度或融资含义的产业政策，不归入本主题。",
        ),
        canonical_labels=("资本市场改革", "并购重组", "IPO", "区域股权市场", "券商投行", "上市融资"),
        granularity_note="这是 L0 资本市场制度主线；并购重组、IPO、区域股权市场和券商生态作为子方向进入。",
    ),
    SeedCommunityDefinition(
        title="新能源出海",
        scope="承接新能源企业海外产能、储能、电池、光伏、绿电、海外建厂、贸易壁垒和区域产业合作。",
        include_rules=(
            "储能、电池、光伏、绿电等新能源产业的海外产能和项目布局。",
            "企业出海、海外建厂、区域合作、贸易壁垒和本地化生产。",
            "新能源海外布局对供应链、订单、成本和政策风险的影响。",
        ),
        exclude_rules=(
            "国内普通新能源装机数据不一定归入，除非涉及出海或全球供应链。",
            "单纯电力系统政策可归入政策监管与产业扶持。",
        ),
        canonical_labels=("新能源出海", "储能出海", "海外产能", "光伏出海", "电池供应链"),
        granularity_note="这是 L0 新能源全球化主线；国家、公司、工厂和产品作为子方向进入。",
    ),
    SeedCommunityDefinition(
        title="宏观流动性与汇率利率",
        scope="承接央行操作、利率、汇率、债券市场、社会融资、信贷、流动性和宏观金融条件变化。",
        include_rules=(
            "央行干预、利率路径、汇率稳定、债券收益率、信贷和社融。",
            "宏观流动性变化对权益、债券、外汇和风险资产的影响。",
            "海外央行政策、美元流动性和本币汇率压力。",
        ),
        exclude_rules=(
            "没有宏观金融条件含义的公司融资交易，不归入本主题。",
            "纯资本市场制度改革优先归入资本市场改革。",
        ),
        canonical_labels=("宏观流动性", "汇率", "利率", "央行政策", "社会融资", "债券市场"),
        granularity_note="这是 L0 宏观金融条件主线；具体国家、币种、利率会议和数据发布作为子方向进入。",
    ),
)


@dataclass(frozen=True)
class CognitiveCommunityBuildResult:
    cards: list[CognitiveCard]
    assignments: list[CommunityAssignment]
    communities: list[GraphIndexCommunity]
    documents: list[GraphIndexVectorDocument]
    diagnostics: dict[str, Any]


COGNITIVE_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "maxLength": 180},
        "title_candidates": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 24}},
        "topic_intents": {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "raw_theme": {"type": "string", "maxLength": 48},
                    "title_candidate": {"type": "string", "maxLength": 32},
                    "parent_themes": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 24}},
                    "broad_topics": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 24}},
                    "mid_topics": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 28}},
                    "specific_topics": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 36}},
                    "topic_level_hint": {"type": "string", "enum": ["broad", "mid", "specific", "mixed", "uncertain"]},
                    "driver": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 32}},
                    "impact_target": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 28}},
                    "risk_type": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 28}},
                    "event_thread": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 32}},
                    "event_action": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 32}},
                    "actors": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 28}},
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "impact_direction": {"type": "string", "enum": ["positive", "negative", "mixed", "uncertain"]},
                    "event_stage": {"type": "string", "maxLength": 24},
                    "timeline_position": {
                        "type": "string",
                        "enum": ["trigger", "reaction", "escalation", "deescalation", "resolution", "follow_up", "uncertain"],
                    },
                    "event_time": {"type": "string", "maxLength": 32},
                    "summary": {"type": "string", "maxLength": 120},
                    "supporting_text": {"type": "string", "maxLength": 120},
                },
                "required": [
                    "raw_theme",
                    "title_candidate",
                    "parent_themes",
                    "broad_topics",
                    "mid_topics",
                    "specific_topics",
                    "topic_level_hint",
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
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "risk_type": {"type": "string", "maxLength": 32},
                    "risk_direction": {"type": "string", "enum": ["increasing", "decreasing", "neutral", "uncertain"]},
                    "risk_scope": {"type": "string", "maxLength": 48},
                    "risk_severity": {"type": "number", "minimum": 0, "maximum": 1},
                    "uncertainty": {"type": "string", "maxLength": 80},
                    "supporting_text": {"type": "string", "maxLength": 120},
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
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "local_impact_mentions": {"type": "string", "maxLength": 100},
                    "local_impact_target": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 32}},
                    "local_impact_direction": {
                        "type": "string",
                        "enum": ["positive", "negative", "mixed", "neutral", "uncertain"],
                    },
                    "local_impact_mechanism_text": {"type": "string", "maxLength": 120},
                    "supporting_text": {"type": "string", "maxLength": 120},
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
                "actors": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 32}},
                "companies": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 32}},
                "industries": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 32}},
                "regions": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 32}},
                "policies": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 48}},
                "commodities": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 32}},
            },
            "required": ["actors", "companies", "industries", "regions", "policies", "commodities"],
            "additionalProperties": False,
        },
        "supporting_text": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 120}},
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


ASSIGNMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": sorted(ASSIGNMENT_ACTIONS)},
                    "community_id": {"type": "string"},
                    "absorb_community_ids": {
                        "type": "array",
                        "maxItems": 8,
                        "items": {"type": "string"},
                    },
                    "weight": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "fit_type": {
                        "type": "string",
                        "enum": sorted(ASSIGNMENT_FIT_TYPES),
                    },
                    "reason": {"type": "string"},
                },
                "required": [
                    "action",
                    "community_id",
                    "absorb_community_ids",
                    "weight",
                    "confidence",
                    "fit_type",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
        "new_communities": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "client_id": {"type": "string"},
                    "title": {"type": "string"},
                    "scope": {"type": "string"},
                },
                "required": ["client_id", "title", "scope"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["assignments", "new_communities"],
    "additionalProperties": False,
}


COGNITIVE_CARD_SYSTEM_PROMPT = """你是金融知识图谱的 Cognitive Card 抽取器。

任务：把当前 chunk text 抽成局部认知信号，供 Community、事件流、风险簇、影响链等高阶索引复用。

要求：
- 只基于当前 chunk text，不添加外部事实，不跨 chunk 推断全局结论。
- 不要输出 source_id、evidence_id、chunk_id、offset、previous_chunk_id、next_chunk_id、text_hash、chunker_version；这些证据定位字段由系统注入。
- 必须输出 topic_intents，数量 1-10 个；每个 topic_intent 表示当前 chunk 支撑的一个主题意图。
- topic_intents 必须是对象数组，禁止输出字符串数组。
- 每个 topic_intent 对象必须包含 raw_theme、title_candidate、parent_themes、broad_topics、mid_topics、specific_topics、topic_level_hint、driver、impact_target、risk_type、event_thread、event_action、actors、importance、impact_direction、event_stage、timeline_position、event_time、summary、supporting_text。
- topic_intent 只表示高维主题意图，不表示单个公司动作、单个数字、单个项目、单条审批、单次行情或单个数据点。
- 细事实应放入 specific_topics、event_action、actors、supporting_text，不要为细事实单独创建 topic_intent。
- 不要因为出现多个公司、多个数字、多个动作就拆多个 topic_intents；除非它们属于不同父级主题、不同影响对象、不同风险类型或不同事件线。
- raw_theme 必须是当前 chunk 能支撑的主题表达，不要直接照抄新闻标题。
- title_candidate 必须是适合作为 community 的候选主题标题。
- parent_themes 写交易认知层 L0 父主题，是后续 Community 归档最重要的信号；它应该能承载多条不同来源、不同主体、不同时间的资料。
- parent_themes 不要写公司名、项目名、单一产品、单次交易或单条行情；遇到细方向时必须上提到可复用父主题。
- parent_themes 应像图书馆一级目录名：短、稳定、可复用；不要写成一句新闻摘要、原因解释或长标题。
- parent_themes 必须有稳定对象和稳定机制边界：能说明“哪类资产、产业、政策、风险、公司经营或宏观变量为什么被影响”。只描述短期表现、资金行为、交易热度或盘面状态的表达，不应作为 parent_themes。
- 如果 chunk 主要描述市场短期表现，parent_themes 必须追溯背后的驱动主题、产业链主题、政策主题、风险主题或流动性主题；无法追溯时，把短期表现放入 mid_topics / specific_topics / supporting_text。
- 如果一个表达只描述某个父主题下的供需变化、风险变化、资金变化、项目进展或单一主体动作，应放入 mid_topics / specific_topics，而不是 parent_themes。
- broad_topics 写父主题下仍然较宽的行业、政策、市场或风险主题。
- mid_topics 写父级主题下的子方向，适合未来 L1/L2。
- specific_topics 写具体项目、公司动作、单笔交易、单个产品、单次行情或单条事件线索；这些不是 L0 标题。
- impact_target 只写当前 chunk 明确提到的行业、资产、公司、商品、产业链环节。
- event_thread 用于给后续事件流提供局部线索，应是可跨多条资料复用的政策线、产业线、地缘线、公司事件线或市场事件线名称。
- timeline_position / event_time 继续抽取并保留给后续事件流；但它们不会传给 Community Assignment LLM。
- risk_signals 只抽当前 chunk 明确支撑的风险线索；证据不足时保守输出。
- local_impact_signals 只抽当前 chunk 明确提到的局部影响线索，不要改写成完整影响链。
- summary、topic_intent.summary、local_impact_mentions 不允许出现“当前chunk”“当前 chunk”“本chunk”“该chunk”“这段chunk”等实现视角词。
- supporting_text 只写当前 chunk 中支撑判断的关键短句，不要整段复制，不要超过一句。
- title_candidates 给 3-5 个候选主题标题，优先覆盖 parent_themes，其次覆盖 broad_topics 和 mid_topics。
- title_candidates 必须是主题名，不要使用新闻标题、单一公司项目名、单一交易名、盘面描述或“动态/事件/项目/公告”这类尾词。
- 不要输出 primary / secondary 之类主次判断；主题强弱由后续 assignment weight 表达。
- 输出必须符合 JSON Schema，不要 Markdown。"""


ASSIGNMENT_SYSTEM_PROMPT = """你是金融知识图谱的 Community 归档裁决器。

你会收到 compact topic_intent，以及系统维护的候选 community append log。

你的任务：
- 在候选 community 中判断是否应该挂入已有主题；
- 如果候选都不适合，创建新的 L0 community；
- 如果候选相关但粒度偏细、边界按生命周期/动作阶段拆错，创建更合适的父级 L0 并吸收这些已有候选；
- 输入里的 candidate_append_log 是唯一候选上下文；
- candidate_append_log 只追加，不在中间改写既有条目；靠前不代表更相关；
- entry_type=candidate_base 表示一个 community 第一次进入候选账本时的稳定目录信息；
- entry_type=candidate_update 表示这个 community 后续新增吸收的子方向，只包含 community_id 和 absorbed；
- entry_type=candidate_redirect 表示旧 community 已经被物理移除并合并到 to_community_id；from_community_id 已失效，禁止输出它，只能把它作为理解历史上下文的标记。
- candidate_update 只能辅助理解该目录最近吸收过哪些子方向，不得覆盖 candidate_base 中的 scope、include_rules、exclude_rules、canonical_labels 和 granularity_note；
- Cognitive Card 的 parent_themes、title_candidate、raw_theme 都只是候选信号，不是最终 community 名称；你必须在本阶段归一化主题边界。
- 输入候选 community_id 是系统真实 community ID；attach_existing 时必须原样使用候选 community_id，禁止输出标题或自造 ID。
- 一个 topic_intent 可以归属到一个或多个 community；
- 不区分 primary / secondary；
- 每条归属必须输出 weight，表示这个 topic_intent 和 community 的关联强度；
- 每条归属必须输出 fit_type，用来说明这是既有方向、新增子方向、更宽父主题、相邻上下文，还是全新的父主题；
- 每条归属必须输出 absorb_community_ids；非 create_parent_and_absorb_existing 时输出空数组 []。
- assignments 数量不要超过 max_attach 限制；
- 不要输出 uncertain，不走人工 pending；
- 低置信也必须在 attach_existing、create_new、create_parent_and_absorb_existing 中选择一个合法 action；
- action 只能是 attach_existing、create_new、create_parent_and_absorb_existing。
- action 选择顺序必须是：先判断能否 attach_existing；如果不能直接 attach，但候选中存在同一稳定对象/机制下的窄主题，必须判断 create_parent_and_absorb_existing；只有两者都不成立时才 create_new。
- create_parent_and_absorb_existing 表示：候选中已有相关 community，但它们只是当前更大父主题下的子方向、生命周期阶段、动作阶段或局部表达；你要创建一个更稳定的父级 L0，并吸收这些已有候选。
- 当已有候选只是把某个父主题下的局部阶段、动作环节或子方向当成 L0，而当前 intent 能提炼出更合适、更稳定的父级目录时，优先 create_parent_and_absorb_existing，而不是继续 create_new 一个平级主题。
- create_parent_and_absorb_existing 创建的新父主题必须保留当前 intent 的稳定对象和机制边界，不能为了吸收多个候选而上提到过粗父类。
- create_new 只适用于候选里没有可被当前新父主题稳定吸收的相关 community；如果你准备创建的新 L0 能自然覆盖某个候选的对象、风险来源或产业链边界，就不能把该候选留在旁边形成平级重复主题。
- 如果你准备 create_new 的原因是“已有候选只覆盖当前父主题下的局部子方向、生命周期阶段、动作环节、影响环节或风险暴露环节”，这通常不是普通 create_new 的理由，而是 create_parent_and_absorb_existing 的信号：应该创建覆盖完整对象/机制链条的父主题并吸收该窄候选。
- 判断是否吸收时，优先看稳定对象、风险来源、产业链位置、政策机制、市场机制或经营机制是否相同，而不是看当前新闻描述的是同一事件阶段还是同一动作环节。
- 只共享过粗上位类、但底层对象或风险来源不同的候选，不应被吸收。
- 如果候选分别属于不同稳定对象或不同风险来源，只能吸收与新父主题对象边界一致的候选；其他候选继续保留为独立 L0。
- 如果候选主题的 scope、include_rules、canonical_labels 能承接当前 intent，应优先 attach_existing。
- 候选主题不是笼子；如果当前 intent 明显不在候选 scope 或命中 exclude_rules，必须 create_new 或挂入更合适的候选 community。
- 归档判断必须综合 parent_themes、broad_topics、mid_topics、specific_topics、raw_theme、title_candidate、driver、impact_target、risk_type、event_thread、event_action、actors。
- parent_themes 是 L0 归档主信号。如果 topic_intent.parent_themes 与候选 community 的 title、canonical_labels、summary 或 scope 明显匹配，应优先 attach_existing。
- broad_topics、mid_topics、specific_topics 都只是候选信号，不是标题指令；你必须判断它们的真实层级是否适合作为 L0。
- L0 community 是可长期复用的父级主题，应该能承载多条不同来源、不同时间、不同主体的资料，并且未来可以继续拆出 L1/L2。
- 候选 community 的 scope、include_rules、exclude_rules、canonical_labels、granularity_note 用来判断长期目录边界；candidate_update.absorbed 只用于辅助理解该目录后续新增吸收过哪些子方向。
- 判断重点不是候选 summary 是否已经写过当前细节，而是候选是否能作为父级目录承接当前细节。
- 不要因为候选没有直接提到当前 chunk 的细节就判为 related_but_separate；L0 的职责就是吸收同一父主题下的新子方向。
- 如果当前细节是候选 community 的新增子方向，应 attach_existing，并把 fit_type 写成 new_subtopic；不要因为 summary 里没有当前细节而新建平级 L0。
- 如果候选 community 比当前 intent 更宽，但能承接当前 intent，应 attach_existing，并把 fit_type 写成 broader_parent。
- 只有当候选 community 的目录范围无法承接当前 intent 时，才 create_new；create_new 的 fit_type 必须是 new_parent_topic。
- create_new 时必须在 reason 中说明：为什么现有候选无法承接、这个新主题未来能持续覆盖什么边界、它与最接近候选的区别。
- 如果现有候选无法直接承接当前 intent，但它与当前 intent 属于同一更高层稳定父主题下的窄方向，不要并列 create_new；必须 create_parent_and_absorb_existing，创建共同父主题并吸收这些窄候选。
- “候选只覆盖某个子方向，所以无法承接当前 intent”不是直接 create_new 的理由；这通常意味着需要上提共同父主题并吸收候选，除非二者底层对象、机制或风险来源确实不同。
- 如果现有候选标题比当前准备创建的新标题更窄，但它的 scope、canonical_labels、assigned signals 与新标题共享同一稳定对象或风险来源，应把它作为 absorb_community_ids；不要让两个 L0 分别表达“父主题”和“父主题下的阶段/动作”。
- create_parent_and_absorb_existing 时，community_id 必须引用 new_communities 中的 client_id；absorb_community_ids 必须列出要吸收的候选 candidate_base community_id。
- create_parent_and_absorb_existing 只允许吸收 candidate_append_log 中真实存在的 candidate_base community；禁止吸收 seed community，除非 seed 的 scope 明显错误且当前输入直接证明它应被替代。
- create_parent_and_absorb_existing 不能只吸收最近或最相似的一个候选；你必须扫描全部 candidate_base，把同一父主题下的局部阶段、动作环节或子方向候选一次性列入 absorb_community_ids。
- 但如果一个候选与当前 intent 只共享过粗上位类，具体对象、机制或风险来源不同，不要吸收；过粗上位类不能作为跨对象合并的唯一依据。
- 如果你认为某个相近候选不应被吸收，新父主题的 scope 必须清楚排除它；否则应吸收。
- create_parent_and_absorb_existing 的 reason 必须说明：旧候选为什么是偏细/偏阶段化主题、新父主题如何稳定承接它们、为什么新父主题不会变成泛化桶。
- 如果 candidate_redirect 显示某个旧主题已经合并到 to_community_id，后续判断应以 to_community_id 对应的 candidate_base 为准；redirect 本身不是可挂载候选。
- 如果当前 topic_intent 是 mid/specific 层级，不能直接把细主题包装成 L0；必须先寻找已有父级或子级 community 是否可挂入，没有合适候选时再提炼更高一层的父级 L0。
- 如果创建新 L0，title 必须优先使用 parent_themes 中可长期复用的父级主题；只有 parent_themes 为空或明显不适合时，才从 broad_topics 中提炼父级主题。
- 如果当前 broad/mid/specific 是已有 parent_theme 的子方向，必须挂入该父主题，不要创建平级 L0。
- 如果创建新 L0，title 必须是可长期复用的父级主题，不能是新闻标题、公司项目名、单一交易名、单个产品、单次行情、单个技术细节。
- 上提标题时不能丢掉当前 intent 的稳定对象边界；如果输入已经明确属于某个子领域、子风险来源或子产业链，不要上提到能吞下大量异质内容的过粗父类。
- create_new 的 title 应像索引目录名，优先短标题；不要输出带有“政策与市场动态”“结构性变化”“投资机会”等摘要式尾巴的长标题，除非这是不可再压缩的稳定主题名。
- L0 标题不能只描述动作、流程、阶段或组织行为；必须带有稳定对象边界，例如产业链、资产类别、政策机制、风险来源、技术链条、供需机制或经营基本面对象。
- 如果一个候选标题只表达“转型、孵化、合作、预警、响应、扩张、布局、升级、回暖、异动”等动作或阶段，你必须判断它背后的稳定对象是什么；能挂入已有对象型父主题就 attach_existing，能覆盖多个窄对象且边界清楚才 create_parent_and_absorb_existing，不能直接创建动作型 L0。
- 政策主题只承接政策或监管机制本身；如果新闻核心是某个产业/技术/资产链条在发展，即使它来自政府报告或地方文件，也应优先归入该产业/技术/资产父主题，政策只能作为低权重相邻上下文。
- 地缘政治主题不只等于能源风险；军事安全、外交摩擦、地区安全、制裁和通道风险只要会影响市场、供应链、风险偏好或资产定价，都应先考虑地缘风险父主题，而不是把单一国家关系或单一安全动作新建为 L0。
- L0 不能只是描述市场短期状态或交易表现。合格 L0 必须同时满足：有明确覆盖对象、有稳定驱动机制、有可复用边界、有可解释的排除边界。
- 如果一个新主题可能吸收大量互不相关的新闻，只因为它们都表现为短期价格、成交、资金、情绪或热度变化，那么它不是合格 L0。
- 如果当前 intent 描述的是短期市场表现，必须先判断它背后的对象和机制。只有对象和机制边界清楚、未来不会吸收互不相关主题时才允许 create_new。
- 如果多个线索只有市场表现相似，但底层对象、驱动机制或风险来源不同，应分别挂到各自更有边界的父主题，而不是创建一个统一的表现型 L0。
- 如果只能说明“市场短期怎么动”，而不能说明“哪个稳定主题为什么被影响”，不要 create_new；应 attach_existing 到最接近的驱动主题，并用较低 weight 表示它只是相邻上下文。
- 如果无法从当前 intent 提炼出稳定驱动主题，不要创建表现型泛化桶；应挂入最接近的已有父主题并说明弱相关原因。
- 如果 action=attach_existing，community_id 必须引用 candidate_append_log 中真实存在且未被 redirect 失效的 candidate_base community_id，不能引用 candidate_redirect.from_community_id。
- 如果 action=create_new，community_id 必须引用 new_communities 中的 client_id，例如 new_1。
- 如果 action=create_parent_and_absorb_existing，community_id 必须引用 new_communities 中的 client_id，例如 new_1，并且 absorb_community_ids 至少包含 1 个候选 community_id。
- new_communities 只包含本次新建 community 的 client_id、title、scope。不要输出 level、title_quality、future_coverage、maintenance_hints、rejected_candidates 或 candidate_fit_judgements。
- 输出必须符合 JSON Schema，不要 Markdown。"""


def cognitive_card_from_llm(chunk: EvidenceChunk, data: dict[str, Any]) -> CognitiveCard:
    payload = dict(chunk.payload or {})
    source_id = str(payload.get("source_id") or "")
    source_type = str(payload.get("source_type") or "")
    pointers = {
        "source_id": source_id,
        "source_type": source_type,
        "evidence_id": chunk.evidence_id,
        "primary_chunk_id": chunk.chunk_id,
        "chunk_ids": [chunk.chunk_id],
        "chunk_index": chunk.chunk_index,
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "previous_chunk_id": chunk.previous_chunk_id or "",
        "next_chunk_id": chunk.next_chunk_id or "",
        "text_hash": chunk.text_hash or "",
        "chunker_version": chunk.chunker_version or "",
    }
    card_id = "kg_cognitive_card:" + _digest(
        [chunk.adapter_name, chunk.evidence_id, chunk.chunk_id, str(chunk.text_hash or ""), COGNITIVE_CARD_SCHEMA_VERSION]
    )
    raw_intents = data.get("topic_intents") or []
    if not isinstance(raw_intents, list) or not raw_intents:
        raise RuntimeError(f"cognitive card has no topic_intents: chunk_id={chunk.chunk_id}")
    intents: list[dict[str, Any]] = []
    for intent in raw_intents:
        if isinstance(intent, dict):
            intents.append(_clean_intent(intent))
    if not intents:
        raise RuntimeError(f"cognitive card has no valid topic_intents: chunk_id={chunk.chunk_id}")
    return CognitiveCard(
        cognitive_card_id=card_id,
        adapter_name=chunk.adapter_name,
        source_type=source_type,
        source_id=source_id,
        evidence_id=chunk.evidence_id,
        primary_chunk_id=chunk.chunk_id,
        chunk_ids=[chunk.chunk_id],
        chunk_index=chunk.chunk_index,
        summary=_clean_text(data.get("summary")),
        title_candidates=_dedupe(_as_list(data.get("title_candidates")))[:5],
        topic_intents=intents,
        risk_signals=[item for item in data.get("risk_signals") or [] if isinstance(item, dict)],
        local_impact_signals=[item for item in data.get("local_impact_signals") or [] if isinstance(item, dict)],
        actor_signals=data.get("actor_signals") if isinstance(data.get("actor_signals"), dict) else {},
        supporting_text=_dedupe(_as_list(data.get("supporting_text")))[:5],
        system_pointers=pointers,
        payload={
            **data,
            "title": payload.get("title") or "",
            "source_name": payload.get("source_name") or "",
            "source_type": source_type,
            "source_id": source_id,
            "published_at": payload.get("published_at") or "",
            "created_at": payload.get("created_at") or "",
            "observed_at": payload.get("observed_at") or "",
        },
    )


def validate_assignment_decision(
    decision: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    topic_intent: dict[str, Any] | None = None,
) -> None:
    required_top = ["assignments", "new_communities"]
    missing_top = [key for key in required_top if key not in decision]
    if missing_top:
        raise RuntimeError(f"assignment decision missing top-level fields: {missing_top}; decision={decision}")
    if not isinstance(decision["assignments"], list) or not decision["assignments"]:
        raise RuntimeError(f"assignments must be non-empty array; decision={decision}")
    if len(decision["assignments"]) > COMPLEX_MAX_ATTACH:
        raise RuntimeError(f"too many assignments: {len(decision['assignments'])}; decision={decision}")
    if not isinstance(decision["new_communities"], list):
        raise RuntimeError(f"new_communities must be array; decision={decision}")
    candidate_ids = {str(candidate["community_id"]) for candidate in candidates}
    new_communities = _validate_new_communities(decision, topic_intent=topic_intent)
    seen: set[str] = set()
    for assignment in decision["assignments"]:
        if not isinstance(assignment, dict):
            raise RuntimeError(f"assignment must be object: {assignment}; decision={decision}")
        action = assignment.get("action")
        if action not in ASSIGNMENT_ACTIONS:
            raise RuntimeError(f"assignment.action invalid: {action}; decision={decision}")
        community_id = assignment.get("community_id")
        if not isinstance(community_id, str) or not community_id.strip():
            raise RuntimeError(f"assignment.community_id must be non-empty string; decision={decision}")
        absorb_ids = _dedupe(_as_list(assignment.get("absorb_community_ids")))
        if action == "attach_existing":
            if community_id not in candidate_ids:
                raise RuntimeError(f"community_id not in candidates: {community_id}; decision={decision}")
            if absorb_ids:
                raise RuntimeError(f"attach_existing cannot absorb communities: {absorb_ids}; decision={decision}")
            dedupe = "attach:" + community_id
        elif action == "create_new":
            if community_id not in new_communities:
                raise RuntimeError(f"create_new references unknown new community: {community_id}; decision={decision}")
            if absorb_ids:
                raise RuntimeError(f"create_new cannot absorb communities: {absorb_ids}; decision={decision}")
            dedupe = "create:" + _normalize_label(str(new_communities[community_id].get("title") or ""))
        else:
            if community_id not in new_communities:
                raise RuntimeError(
                    f"create_parent_and_absorb_existing references unknown new community: {community_id}; decision={decision}"
                )
            if not absorb_ids:
                raise RuntimeError(f"create_parent_and_absorb_existing requires absorb_community_ids; decision={decision}")
            invalid_absorb_ids = [item for item in absorb_ids if item not in candidate_ids]
            if invalid_absorb_ids:
                raise RuntimeError(
                    f"absorb_community_ids must reference candidates: {invalid_absorb_ids}; decision={decision}"
                )
            dedupe = "absorb:" + _normalize_label(str(new_communities[community_id].get("title") or ""))
        if dedupe in seen:
            raise RuntimeError(f"duplicate assignment target: {dedupe}; decision={decision}")
        seen.add(dedupe)
        for numeric in ("weight", "confidence"):
            value = assignment.get(numeric)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1:
                raise RuntimeError(f"{numeric} must be number between 0 and 1; decision={decision}")
        fit_type = _clean_text(assignment.get("fit_type"))
        if fit_type not in ASSIGNMENT_FIT_TYPES:
            raise RuntimeError(f"assignment.fit_type invalid: {fit_type}; decision={decision}")
        if action in {"create_new", "create_parent_and_absorb_existing"} and fit_type != "new_parent_topic":
            raise RuntimeError(f"{action} fit_type must be new_parent_topic; decision={decision}")
        if action == "attach_existing" and fit_type == "new_parent_topic":
            raise RuntimeError(f"attach_existing fit_type cannot be new_parent_topic; decision={decision}")
        if not _clean_text(assignment.get("reason")):
            raise RuntimeError(f"assignment.reason must be non-empty; decision={decision}")


def _validate_new_communities(
    decision: dict[str, Any],
    *,
    topic_intent: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in decision["new_communities"]:
        if not isinstance(item, dict):
            raise RuntimeError(f"new_communities item must be object: {item}; decision={decision}")
        client_id = _clean_text(item.get("client_id"))
        title = _clean_text(item.get("title"))
        scope = _clean_text(item.get("scope"))
        if not client_id:
            raise RuntimeError(f"new_community.client_id must be non-empty; decision={decision}")
        if client_id in result:
            raise RuntimeError(f"duplicate new_community.client_id: {client_id}; decision={decision}")
        if not title:
            raise RuntimeError(f"new_community.title must be non-empty; decision={decision}")
        if not scope:
            raise RuntimeError(f"new_community.scope must be non-empty; decision={decision}")
        result[client_id] = {"client_id": client_id, "title": title, "scope": scope}
    return result


def _assignment_topic_intent(card: CognitiveCard, intent_payload: dict[str, Any]) -> dict[str, Any]:
    intent = {
        "raw_theme": _clean_text(intent_payload.get("raw_theme")),
        "title_candidate": _clean_text(intent_payload.get("title_candidate") or intent_payload.get("raw_theme")),
        "parent_themes": _dedupe(_as_list(intent_payload.get("parent_themes")))[:5],
        "broad_topics": _dedupe(_as_list(intent_payload.get("broad_topics")))[:5],
        "mid_topics": _dedupe(_as_list(intent_payload.get("mid_topics")))[:6],
        "specific_topics": _dedupe(_as_list(intent_payload.get("specific_topics")))[:6],
        "topic_level_hint": str(intent_payload.get("topic_level_hint") or "uncertain").strip() or "uncertain",
        "summary": _clip(str(intent_payload.get("summary") or card.summary or ""), 280),
        "driver": _dedupe(_as_list(intent_payload.get("driver")))[:6],
        "impact_target": _dedupe(_as_list(intent_payload.get("impact_target")))[:8],
        "event_thread": _dedupe(_as_list(intent_payload.get("event_thread")))[:5],
        "risk_type": _dedupe(_as_list(intent_payload.get("risk_type")))[:5],
        "event_action": _dedupe(_as_list(intent_payload.get("event_action")))[:6],
        "actors": _dedupe(_as_list(intent_payload.get("actors")))[:8],
        "importance": round(float(intent_payload.get("importance") or _infer_importance(intent_payload)), 2),
        "cognitive_card_id": card.cognitive_card_id,
        "source_id": card.source_id,
        "evidence_id": card.evidence_id,
        "chunk_ids": card.chunk_ids,
        "primary_chunk_id": card.primary_chunk_id,
        "source_published_at": (card.payload or {}).get("published_at") or "",
        "event_time": _clean_text(intent_payload.get("event_time")),
    }
    impact_direction = str(intent_payload.get("impact_direction") or "").strip()
    if impact_direction and impact_direction != "uncertain":
        intent["impact_direction"] = impact_direction
    return intent


def assignment_prompt_topic_intent(intent: dict[str, Any], *, max_attach: int) -> dict[str, Any]:
    allowed_keys = (
        "raw_theme",
        "title_candidate",
        "parent_themes",
        "broad_topics",
        "mid_topics",
        "specific_topics",
        "topic_level_hint",
        "summary",
        "driver",
        "impact_target",
        "event_thread",
        "risk_type",
        "event_action",
        "actors",
        "importance",
        "impact_direction",
    )
    return {
        **{key: intent[key] for key in allowed_keys if key in intent},
        "max_attach": max_attach,
    }


def _apply_assignment(
    *,
    adapter_name: str,
    card: CognitiveCard,
    intent_index: int,
    topic_intent: dict[str, Any],
    decision: dict[str, Any],
    communities: dict[str, CommunityDraft],
    community_id_factory: Callable[[str, int, str], str] | None = None,
) -> list[CommunityAssignment]:
    applied: list[CommunityAssignment] = []
    new_communities = {
        str(item.get("client_id")): item
        for item in decision.get("new_communities") or []
        if isinstance(item, dict) and str(item.get("client_id") or "").strip()
    }
    for assignment in decision["assignments"]:
        action = str(assignment["action"])
        intent_id = f"{card.cognitive_card_id}:intent:{intent_index}"
        if action in {"create_new", "create_parent_and_absorb_existing"}:
            payload = new_communities[str(assignment["community_id"])]
            community_id = (
                community_id_factory(adapter_name, 0, str(payload["title"]))
                if community_id_factory is not None
                else _community_id(adapter_name, str(payload["title"]))
            )
            if community_id not in communities:
                communities[community_id] = CommunityDraft(
                    community_id=community_id,
                    title=_clean_text(payload["title"]),
                    scope=_clean_text(payload["scope"]),
                    level=0,
                    future_coverage=_future_coverage_from_intent(topic_intent),
                    created_from_source_id=card.source_id,
                )
            if action == "create_parent_and_absorb_existing":
                _absorb_existing_communities(
                    parent=communities[community_id],
                    absorbed_ids=_dedupe(_as_list(assignment.get("absorb_community_ids"))),
                    communities=communities,
                )
        else:
            community_id = str(assignment["community_id"])
        assignment_id = "kg_community_assignment:" + _digest(
            [card.cognitive_card_id, str(intent_index), community_id, action]
        )
        community = communities[community_id]
        stored_intent = {**topic_intent, "intent_id": intent_id}
        stored_assignment = {
            **assignment,
            "assignment_id": assignment_id,
            "cognitive_card_id": card.cognitive_card_id,
            "intent_index": intent_index,
            "intent_id": intent_id,
            "resolved_community_id": community_id,
        }
        intent_identity = _intent_identity(stored_intent)
        community.assigned_intents = [
            item for item in community.assigned_intents if _intent_identity(item) != intent_identity
        ]
        community.assignments = [
            item
            for item in community.assignments
            if str(item.get("assignment_id") or "") != assignment_id
            and _intent_identity(item) != intent_identity
        ]
        community.source_ids = _dedupe([*community.source_ids, card.source_id])
        community.evidence_ids = _dedupe([*community.evidence_ids, card.evidence_id])
        community.chunk_ids = _dedupe([*community.chunk_ids, *card.chunk_ids])
        community.cognitive_card_ids = _dedupe([*community.cognitive_card_ids, card.cognitive_card_id])
        community.assigned_intents.append(stored_intent)
        community.assignments.append(stored_assignment)
        community.summary = _community_summary(community)
        applied.append(
            CommunityAssignment(
                assignment_id=assignment_id,
                adapter_name=adapter_name,
                cognitive_card_id=card.cognitive_card_id,
                intent_index=intent_index,
                intent_id=intent_id,
                community_id=community_id,
                action=action,
                weight=float(assignment.get("weight") or 0),
                confidence=float(assignment.get("confidence") or 0),
                matched_reason=_assignment_reason_with_fit_type(assignment),
                update_mode=_assignment_update_mode(action=action, weight=float(assignment.get("weight") or 0)),
                reason=_assignment_reason_with_fit_type(assignment),
                topic_intent=stored_intent,
                decision=decision,
            )
        )
    return applied


def _absorb_existing_communities(
    *,
    parent: CommunityDraft,
    absorbed_ids: list[str],
    communities: dict[str, CommunityDraft],
) -> None:
    for absorbed_id in absorbed_ids:
        if absorbed_id == parent.community_id:
            continue
        child = communities.get(absorbed_id)
        if child is None:
            continue
        parent.source_ids = _dedupe([*parent.source_ids, *child.source_ids])
        parent.evidence_ids = _dedupe([*parent.evidence_ids, *child.evidence_ids])
        parent.chunk_ids = _dedupe([*parent.chunk_ids, *child.chunk_ids])
        parent.cognitive_card_ids = _dedupe([*parent.cognitive_card_ids, *child.cognitive_card_ids])
        parent.assigned_intents = _dedupe_dicts_by_identity([*parent.assigned_intents, *child.assigned_intents])
        parent.assignments = _dedupe_dicts_by_key(
            [
                *parent.assignments,
                *[
                    _rewrite_absorbed_assignment_payload(
                        item,
                        parent_community_id=parent.community_id,
                        absorbed_community_id=absorbed_id,
                    )
                    for item in child.assignments
                ],
            ],
            key="assignment_id",
        )
        parent.future_coverage = _dedupe([*parent.future_coverage, *child.future_coverage])
        parent.canonical_labels = _dedupe([*parent.canonical_labels, child.title, *child.canonical_labels])
        parent.absorbed_community_ids = _dedupe(
            [*parent.absorbed_community_ids, absorbed_id, *child.absorbed_community_ids]
        )
        del communities[absorbed_id]
    parent.summary = _community_summary(parent)


def _rewrite_absorbed_assignment_payload(
    item: dict[str, Any],
    *,
    parent_community_id: str,
    absorbed_community_id: str,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {}
    copied = dict(item)
    if str(copied.get("resolved_community_id") or "") == absorbed_community_id:
        copied["resolved_community_id"] = parent_community_id
    if str(copied.get("community_id") or "") == absorbed_community_id:
        copied["community_id"] = parent_community_id
    copied["absorbed_from_community_id"] = absorbed_community_id
    return copied


def _drafts_from_existing(existing: list[GraphIndexCommunity]) -> dict[str, CommunityDraft]:
    drafts: dict[str, CommunityDraft] = {}
    for community in existing:
        if community.projection != COMMUNITY_PROJECTION or community.status != "active":
            continue
        metrics = community.metrics or {}
        assigned_intents = [
            item for item in (metrics.get("assigned_intents") or []) if isinstance(item, dict)
        ]
        drafts[community.community_id] = CommunityDraft(
            community_id=community.community_id,
            title=community.title,
            scope=str(metrics.get("scope") or community.summary or ""),
            origin=str(metrics.get("origin") or "emergent"),
            level=community.level,
            parent_community_id=community.parent_community_id,
            summary=community.summary,
            include_rules=[str(item) for item in metrics.get("include_rules") or [] if str(item).strip()],
            exclude_rules=[str(item) for item in metrics.get("exclude_rules") or [] if str(item).strip()],
            canonical_labels=[str(item) for item in metrics.get("canonical_labels") or [] if str(item).strip()],
            granularity_note=str(metrics.get("granularity_note") or ""),
            source_ids=[str(item) for item in metrics.get("source_ids") or [] if str(item).strip()],
            evidence_ids=list(community.evidence_ids or []),
            chunk_ids=list(community.chunk_ids or []),
            cognitive_card_ids=[str(item) for item in metrics.get("cognitive_card_ids") or [] if str(item).strip()],
            assigned_intents=assigned_intents,
            assignments=[item for item in (metrics.get("assignments") or []) if isinstance(item, dict)],
            future_coverage=[str(item) for item in metrics.get("future_coverage") or [] if str(item).strip()],
            absorbed_community_ids=[str(item) for item in metrics.get("absorbed_community_ids") or [] if str(item).strip()],
        )
    return drafts


def seed_community_drafts(adapter_name: str) -> dict[str, CommunityDraft]:
    drafts: dict[str, CommunityDraft] = {}
    for definition in SEED_COMMUNITY_DEFINITIONS:
        community_id = _community_id(adapter_name, definition.title)
        drafts[community_id] = CommunityDraft(
            community_id=community_id,
            title=definition.title,
            scope=definition.scope,
            origin="seed",
            level=0,
            summary=definition.scope,
            include_rules=list(definition.include_rules),
            exclude_rules=list(definition.exclude_rules),
            canonical_labels=list(definition.canonical_labels),
            granularity_note=definition.granularity_note,
            future_coverage=list(definition.canonical_labels),
        )
    return drafts


def seed_graph_communities(
    adapter_name: str,
    *,
    existing_communities: list[GraphIndexCommunity] | None = None,
    community_id_factory: Callable[[SeedCommunityDefinition], str] | None = None,
) -> list[GraphIndexCommunity]:
    drafts = _drafts_from_existing(existing_communities or [])
    seeds: dict[str, CommunityDraft] = {}
    for definition in SEED_COMMUNITY_DEFINITIONS:
        existing_id = _find_seed_draft_id_by_title(drafts, definition.title)
        community_id = existing_id or (
            community_id_factory(definition)
            if community_id_factory is not None
            else _community_id(adapter_name, definition.title)
        )
        seeds[community_id] = _seed_draft(adapter_name, definition, community_id=community_id)
    seed_ids = set(seeds)
    merge_seed_community_drafts(drafts, seeds)
    return [
        _graph_community_from_draft(adapter_name, drafts[community_id])
        for community_id in sorted(seed_ids)
        if community_id in drafts
    ]


def merge_seed_community_drafts(
    drafts: dict[str, CommunityDraft],
    seeds: dict[str, CommunityDraft],
) -> dict[str, CommunityDraft]:
    for community_id, seed in seeds.items():
        target_id = community_id if community_id in drafts else _find_seed_draft_id_by_title(drafts, seed.title)
        existing = drafts.get(target_id or community_id)
        if existing is None:
            drafts[community_id] = seed
            continue
        existing.origin = "seed"
        existing.scope = existing.scope or seed.scope
        if not existing.assigned_intents:
            existing.summary = seed.summary
        existing.include_rules = _dedupe([*existing.include_rules, *seed.include_rules])
        existing.exclude_rules = _dedupe([*existing.exclude_rules, *seed.exclude_rules])
        existing.canonical_labels = _dedupe([*existing.canonical_labels, *seed.canonical_labels])
        existing.granularity_note = existing.granularity_note or seed.granularity_note
        existing.future_coverage = _dedupe([*existing.future_coverage, *seed.future_coverage])
    return drafts


def _seed_draft(
    adapter_name: str,
    definition: SeedCommunityDefinition,
    *,
    community_id: str,
) -> CommunityDraft:
    return CommunityDraft(
        community_id=community_id,
        title=definition.title,
        scope=definition.scope,
        origin="seed",
        level=0,
        summary=definition.scope,
        include_rules=list(definition.include_rules),
        exclude_rules=list(definition.exclude_rules),
        canonical_labels=list(definition.canonical_labels),
        granularity_note=definition.granularity_note,
        future_coverage=list(definition.canonical_labels),
    )


def _find_seed_draft_id_by_title(drafts: dict[str, CommunityDraft], title: str) -> str:
    title_key = _normalize_label(title)
    for community_id, draft in drafts.items():
        if _normalize_label(draft.title) == title_key and draft.origin == "seed":
            return community_id
    for community_id, draft in drafts.items():
        if _normalize_label(draft.title) == title_key:
            return community_id
    return ""


def _community_assignment_stats(draft: CommunityDraft) -> dict[str, float | int]:
    weights: list[float] = []
    for assignment in draft.assignments:
        try:
            weights.append(float(assignment.get("weight") or 0))
        except (TypeError, ValueError):
            weights.append(0.0)
    if not weights:
        return {
            "avg_weight": 0.0,
            "max_weight": 0.0,
            "high_weight_count": 0,
            "medium_weight_count": 0,
            "low_weight_count": 0,
        }
    return {
        "avg_weight": round(sum(weights) / len(weights), 4),
        "max_weight": round(max(weights), 4),
        "high_weight_count": sum(1 for weight in weights if weight >= 0.8),
        "medium_weight_count": sum(1 for weight in weights if 0.5 <= weight < 0.8),
        "low_weight_count": sum(1 for weight in weights if weight < 0.5),
    }


def _graph_community_from_draft(adapter_name: str, draft: CommunityDraft) -> GraphIndexCommunity:
    signals = draft.signal_values()
    version_id = f"{draft.community_id}:v:{_digest([*draft.chunk_ids, *draft.cognitive_card_ids, draft.summary])}"
    assignment_stats = _community_assignment_stats(draft)
    topic_tags = _dedupe(
        [
            *signals.get("parent_themes", []),
            *signals.get("broad_topics", []),
            *signals.get("mid_topics", []),
            *signals.get("raw_theme", []),
        ]
    )[:32]
    topic_diversity_values = _dedupe(
        [
            *topic_tags,
            *signals.get("specific_topics", []),
            *signals.get("event_thread", []),
        ]
    )
    source_times = _dedupe(
        _clean_text(intent.get("source_published_at"))
        for intent in draft.assigned_intents
        if isinstance(intent, dict)
    )
    metrics = {
        "origin": draft.origin,
        "include_rules": draft.include_rules,
        "exclude_rules": draft.exclude_rules,
        "granularity_note": draft.granularity_note,
        "source_ids": draft.source_ids,
        "source_count": len(set(draft.source_ids)),
        "unique_source_count": len(set(draft.source_ids)),
        "evidence_count": len(set(draft.evidence_ids)),
        "chunk_count": len(set(draft.chunk_ids)),
        "cognitive_card_ids": draft.cognitive_card_ids,
        "cognitive_card_count": len(set(draft.cognitive_card_ids)),
        "assigned_intents": draft.assigned_intents[-80:],
        "assigned_intent_count": len(draft.assigned_intents),
        "assignments": draft.assignments[-80:],
        "assignment_count": len(draft.assignments),
        "avg_assignment_weight": assignment_stats["avg_weight"],
        "max_assignment_weight": assignment_stats["max_weight"],
        "high_weight_assignment_count": assignment_stats["high_weight_count"],
        "medium_weight_assignment_count": assignment_stats["medium_weight_count"],
        "low_weight_assignment_count": assignment_stats["low_weight_count"],
        "topic_diversity_count": len(topic_diversity_values),
        "topic_tags": topic_tags,
        "parent_themes": signals.get("parent_themes", [])[:24],
        "impact_tags": signals.get("impact_target", [])[:32],
        "risk_tags": signals.get("risk_type", [])[:24],
        "event_threads": signals.get("event_thread", [])[:24],
        "future_coverage": _community_future_coverage(draft.future_coverage, signals, limit=32),
        "canonical_labels": _community_canonical_labels(draft, signals),
        "coverage_contract": _community_coverage_contract(draft, signals),
        "maturity_level": maturity_label(len(set(draft.source_ids))),
        "scope": draft.scope,
        "community_builder": "cognitive_card_assignment_v1",
        "absorbed_community_ids": draft.absorbed_community_ids,
    }
    if source_times:
        ordered_source_times = sorted(source_times, key=_datetime_sort_key)
        metrics["earliest_source_published_at"] = ordered_source_times[0]
        metrics["latest_source_published_at"] = ordered_source_times[-1]
    return GraphIndexCommunity(
        community_id=draft.community_id,
        version_id=version_id,
        adapter_name=adapter_name,
        projection=COMMUNITY_PROJECTION,
        level=draft.level,
        parent_community_id=draft.parent_community_id,
        title=draft.title,
        summary=draft.summary or _community_summary(draft),
        member_node_ids=[],
        member_edge_ids=[],
        evidence_ids=_dedupe(draft.evidence_ids),
        chunk_ids=_dedupe(draft.chunk_ids),
        metrics=metrics,
        status="active",
        previous_version_id="",
        change_reason="cognitive_assignment",
        lineage_id="kg_community_lineage:" + _digest([draft.title]),
        previous_community_ids=draft.absorbed_community_ids,
    )


def _community_document(community: GraphIndexCommunity) -> GraphIndexVectorDocument:
    metrics = community.metrics or {}
    text = "\n".join(
        part
        for part in [
            "Document Type: Community Report",
            f"Community: {community.title}",
            f"Projection: {community.projection}",
            f"Community Level: {community.level}",
            f"Origin: {metrics.get('origin') or ''}",
            f"Maturity: {metrics.get('maturity_level') or ''}",
            f"Directory Scope: {metrics.get('scope') or ''}",
            f"Include Rules: {'；'.join(metrics.get('include_rules') or [])}",
            f"Exclude Rules: {'；'.join(metrics.get('exclude_rules') or [])}",
            f"Granularity Note: {metrics.get('granularity_note') or ''}",
            f"Coverage Contract: {metrics.get('coverage_contract') or ''}",
            f"Canonical Labels: {'；'.join(metrics.get('canonical_labels') or [])}",
            f"Assigned Intent Count: {metrics.get('assigned_intent_count') or 0}",
            f"Source Count: {metrics.get('source_count') or 0}",
            f"Average Assignment Weight: {metrics.get('avg_assignment_weight') or 0}",
            f"Topic Diversity Count: {metrics.get('topic_diversity_count') or 0}",
            f"Summary: {community.summary}",
            f"Parent Themes: {'；'.join(metrics.get('parent_themes') or [])}",
            f"Topic Tags: {'；'.join(metrics.get('topic_tags') or [])}",
            f"Future Coverage: {'；'.join(metrics.get('future_coverage') or [])}",
            f"Impact Tags: {'；'.join(metrics.get('impact_tags') or [])}",
            f"Risk Tags: {'；'.join(metrics.get('risk_tags') or [])}",
            f"Event Threads: {'；'.join(metrics.get('event_threads') or [])}",
            f"Cited Evidence: {' '.join(community.evidence_ids[:16])}",
            f"Cited Chunks: {' '.join(community.chunk_ids[:16])}",
            f"Expandable Handles: community_id={community.community_id}",
        ]
        if part and not part.endswith(": ")
    )
    return GraphIndexVectorDocument(
        document_id=community.community_id,
        document_type="community_report",
        collection_role="community",
        source_type="kg_community_report",
        source_id=community.community_id,
        evidence_id=community.evidence_ids[0] if community.evidence_ids else "",
        text=text,
        metadata={
            "community_id": community.community_id,
            "community_version_id": community.version_id,
            "community_title": community.title,
            "community_level": community.level,
            "projection": community.projection,
            "parent_community_id": community.parent_community_id,
            "cited_evidence_ids": community.evidence_ids,
            "cited_chunk_ids": community.chunk_ids,
            "edge_ids": [],
            "node_ids": [],
            "metrics": metrics,
            "maturity_level": metrics.get("maturity_level") or "",
            "cognitive_card_ids": metrics.get("cognitive_card_ids") or [],
            "earliest_source_published_at": metrics.get("earliest_source_published_at") or "",
            "latest_source_published_at": metrics.get("latest_source_published_at") or "",
            "event_time_start": metrics.get("earliest_source_published_at") or "",
            "event_time_end": metrics.get("latest_source_published_at") or "",
        },
    )


def cognitive_card_document(card: CognitiveCard) -> GraphIndexVectorDocument:
    payload = getattr(card, "payload", None) or {}
    topic_intents = [
        item for item in (getattr(card, "topic_intents", None) or []) if isinstance(item, dict)
    ]
    risk_signals = getattr(card, "risk_signals", None) or []
    local_impact_signals = getattr(card, "local_impact_signals", None) or []
    actor_signals = getattr(card, "actor_signals", None) or {}
    supporting_text = _as_list(getattr(card, "supporting_text", None))
    title_candidates = _as_list(getattr(card, "title_candidates", None))
    summary = _clean_text(getattr(card, "summary", ""))
    chunk_ids = _as_list(getattr(card, "chunk_ids", None)) or [_clean_text(getattr(card, "primary_chunk_id", ""))]
    source_type = _clean_text(getattr(card, "source_type", ""))
    source_id = _clean_text(getattr(card, "source_id", ""))
    topic_lines = []
    for intent in topic_intents[:10]:
        topic_lines.append(
            _join_non_empty(
                [
                    _clean_text(intent.get("raw_theme")),
                    "parent=" + "、".join(_as_list(intent.get("parent_themes"))[:4]),
                    "broad=" + "、".join(_as_list(intent.get("broad_topics"))[:4]),
                    "mid=" + "、".join(_as_list(intent.get("mid_topics"))[:5]),
                    "specific=" + "、".join(_as_list(intent.get("specific_topics"))[:5]),
                    "driver=" + "、".join(_as_list(intent.get("driver"))[:5]),
                    "impact=" + "、".join(_as_list(intent.get("impact_target"))[:6]),
                    "risk=" + "、".join(_as_list(intent.get("risk_type"))[:4]),
                    "event_thread=" + "、".join(_as_list(intent.get("event_thread"))[:4]),
                    "actors=" + "、".join(_as_list(intent.get("actors"))[:6]),
                    _clean_text(intent.get("summary")),
                ],
                sep=" | ",
            )
        )
    text = "\n".join(
        part
        for part in [
            "Document Type: Cognitive Card",
            f"Cognitive Card: {card.cognitive_card_id}",
            f"Title: {payload.get('title') or ''}",
            f"Source: {source_type}:{source_id}",
            f"Evidence: {card.evidence_id}",
            f"Primary Chunk: {card.primary_chunk_id}",
            f"Summary: {summary}",
            f"Title Candidates: {'；'.join(title_candidates)}",
            f"Topic Intents: {'；'.join(topic_lines)}",
            f"Risk Signals: {_json_text(risk_signals)}",
            f"Impact Signals: {_json_text(local_impact_signals)}",
            f"Actor Signals: {_json_text(actor_signals)}",
            f"Supporting Text: {'；'.join(supporting_text)}",
            f"Expandable Handles: cognitive_card_id={card.cognitive_card_id} evidence_id={card.evidence_id} chunk_id={card.primary_chunk_id}",
        ]
        if part and not part.endswith(": ")
    )
    event_times = [
        _clean_text(intent.get("event_time"))
        for intent in topic_intents
        if _clean_text(intent.get("event_time"))
    ]
    return GraphIndexVectorDocument(
        document_id=card.cognitive_card_id,
        document_type="cognitive_card",
        collection_role="cognitive_card",
        source_type="kg_cognitive_card",
        source_id=card.cognitive_card_id,
        evidence_id=card.evidence_id,
        text=text,
        metadata={
            "cognitive_card_id": card.cognitive_card_id,
            "source_type": "kg_cognitive_card",
            "source_id": card.cognitive_card_id,
            "original_source_type": source_type,
            "original_source_id": source_id,
            "evidence_id": card.evidence_id,
            "primary_chunk_id": card.primary_chunk_id,
            "cited_chunk_ids": chunk_ids,
            "cited_evidence_ids": [card.evidence_id],
            "title": payload.get("title") or "",
            "title_candidates": title_candidates,
            "topic_intents": topic_intents,
            "risk_signals": risk_signals,
            "local_impact_signals": local_impact_signals,
            "actor_signals": actor_signals,
            "supporting_text": supporting_text,
            "published_at": payload.get("published_at") or "",
            "source_published_at": payload.get("published_at") or "",
            "created_at": payload.get("created_at") or "",
            "observed_at": payload.get("observed_at") or "",
            "event_time": event_times[0] if event_times else "",
            "event_time_start": payload.get("published_at") or "",
            "event_time_end": payload.get("published_at") or "",
        },
    )


def assignment_query_text(intent: dict[str, Any]) -> str:
    return "\n".join(assignment_query_texts(intent))


def assignment_query_texts(intent: dict[str, Any]) -> list[str]:
    lanes: list[list[Any]] = [
        [
            *_as_list(intent.get("parent_themes")),
            *_as_list(intent.get("broad_topics")),
            intent.get("title_candidate"),
            intent.get("raw_theme"),
        ],
        [
            *_as_list(intent.get("parent_themes")),
            *_as_list(intent.get("mid_topics")),
            *_as_list(intent.get("specific_topics")),
            intent.get("summary"),
        ],
        [
            *_as_list(intent.get("event_thread")),
            *_as_list(intent.get("event_action")),
            *_as_list(intent.get("driver")),
            *_as_list(intent.get("actors")),
        ],
        [
            *_as_list(intent.get("impact_target")),
            *_as_list(intent.get("risk_type")),
            intent.get("impact_direction"),
            *_as_list(intent.get("parent_themes")),
        ],
    ]
    queries: list[str] = []
    for lane in lanes:
        query = "\n".join(_dedupe([_clean_text(item) for item in lane if _clean_text(item)]))
        if query:
            queries.append(query)
    merged = "\n".join(_dedupe([line for query in queries for line in query.splitlines() if line.strip()]))
    return _dedupe([*queries, merged])


def assignment_query_lanes(intent: dict[str, Any]) -> list[dict[str, str]]:
    names = ["parent_topic", "child_direction", "event_driver", "impact_risk", "merged"]
    return [
        {"lane": names[index] if index < len(names) else f"lane_{index + 1}", "query": query}
        for index, query in enumerate(assignment_query_texts(intent))
    ]


def _candidate_aliases(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    alias_map: dict[str, str] = {}
    prompt_candidates: list[dict[str, Any]] = []
    for candidate in candidates:
        original_id = str(candidate.get("community_id") or "")
        alias_map[original_id] = original_id
        seed_definition = _seed_definition_for_candidate(candidate)
        if seed_definition is not None:
            prompt_candidate = _seed_candidate_payload(candidate, original_id, seed_definition)
        else:
            prompt_candidate = _emergent_candidate_payload(candidate, original_id)
        dynamic = _candidate_dynamic_payload(candidate, original_id)
        if len(dynamic) > 1:
            prompt_candidate.update({key: value for key, value in dynamic.items() if key != "community_id"})
        prompt_candidates.append(_compact_candidate_prompt_payload(prompt_candidate))
    return alias_map, prompt_candidates


def _seed_definition_for_candidate(candidate: dict[str, Any]) -> SeedCommunityDefinition | None:
    if _clean_text(candidate.get("origin")) != "seed":
        return None
    title_key = _normalize_label(candidate.get("title"))
    for definition in SEED_COMMUNITY_DEFINITIONS:
        if _normalize_label(definition.title) == title_key:
            return definition
    return None


def _seed_candidate_payload(
    candidate: dict[str, Any],
    alias: str,
    definition: SeedCommunityDefinition,
) -> dict[str, Any]:
    return {
        "community_id": alias,
        "title": definition.title,
        "scope": _clip(definition.scope, 260),
        "include_rules": [_clip(item, 120) for item in definition.include_rules[:6]],
        "exclude_rules": [_clip(item, 120) for item in definition.exclude_rules[:6]],
        "canonical_labels": list(definition.canonical_labels[:12]),
        "granularity_note": _clip(definition.granularity_note, 180),
    }


def _emergent_candidate_payload(candidate: dict[str, Any], alias: str) -> dict[str, Any]:
    return {
        "community_id": alias,
        "title": _clean_text(candidate.get("title")),
        "scope": _clip(str(candidate.get("scope") or candidate.get("directory_scope") or ""), 260),
        "include_rules": [_clip(item, 120) for item in _as_list(candidate.get("include_rules"))[:6]],
        "exclude_rules": [_clip(item, 120) for item in _as_list(candidate.get("exclude_rules"))[:6]],
        "canonical_labels": _stable_candidate_labels(candidate)[:10],
        "granularity_note": _clip(str(candidate.get("granularity_note") or ""), 180),
    }


def _stable_candidate_labels(candidate: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            *_as_list(candidate.get("canonical_labels")),
            *_as_list(candidate.get("parent_themes")),
            *_as_list(candidate.get("broad_topics")),
        ]
    )


def _candidate_dynamic_payload(candidate: dict[str, Any], alias: str) -> dict[str, Any]:
    subtopics = _candidate_absorbed_subtopics(candidate)
    payload: dict[str, Any] = {"community_id": alias}
    if subtopics:
        payload["absorbed_subtopics"] = subtopics
    maturity = _clean_text(candidate.get("maturity"))
    if maturity:
        payload["maturity"] = maturity
    return payload


def _compact_candidate_prompt_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"origin", "level", "dynamic_context"}:
            continue
        if isinstance(value, str):
            cleaned = _clean_text(value)
            if cleaned:
                result[key] = cleaned
            continue
        if isinstance(value, list):
            cleaned_items = [item for item in value if not (isinstance(item, str) and not item.strip())]
            if cleaned_items:
                result[key] = cleaned_items
            continue
        if value is None:
            continue
        result[key] = value
    return result


def _candidate_absorbed_subtopics(candidate: dict[str, Any]) -> list[str]:
    values = _dedupe(
        [
            *_as_list(candidate.get("parent_themes")),
            *_as_list(candidate.get("broad_topics")),
            *_as_list(candidate.get("mid_topics")),
            *_as_list(candidate.get("future_coverage")),
            *_as_list(candidate.get("specific_topics")),
        ]
    )
    return [value for value in values if _is_prompt_summary_label(value)][:8]


def _candidate_recent_signal_summary(candidate: dict[str, Any], subtopics: list[str]) -> list[str]:
    summaries: list[str] = []
    if subtopics:
        summaries.append("已吸收方向：" + "、".join(subtopics[:6]))
    for example in candidate.get("recent_examples") or []:
        if not isinstance(example, dict):
            continue
        title = _clean_text(example.get("title"))
        summary = _clean_text(example.get("summary"))
        text = title if _is_prompt_summary_label(title) else ""
        if not text and summary and _is_prompt_summary_label(summary):
            text = summary
        if text:
            summaries.append("近期信号：" + _clip(text, 60))
        if len(summaries) >= 3:
            break
    return _dedupe([_clip(item, 90) for item in summaries])[:3]


def _is_prompt_summary_label(value: Any) -> bool:
    text = _clean_text(value)
    if not text or len(text) > 42:
        return False
    digit_count = sum(char.isdigit() for char in text)
    if digit_count >= 3:
        return False
    if re.search(r"\d+(\.\d+)?\s*(%|亿元|万元|元|万股|股|点|倍)", text):
        return False
    if re.search(r"\b\d{5,6}\b", text):
        return False
    return True


def _resolve_aliases(decision: dict[str, Any], alias_map: dict[str, str]) -> dict[str, Any]:
    def resolve(value: Any) -> Any:
        return alias_map.get(str(value), value) if value is not None else value

    copied = json.loads(json.dumps(decision, ensure_ascii=False))
    for assignment in copied.get("assignments") or []:
        if isinstance(assignment, dict):
            if assignment.get("action") == "attach_existing":
                assignment["community_id"] = resolve(assignment.get("community_id"))
    return copied


def _join_non_empty(parts: list[str], *, sep: str = " ") -> str:
    return sep.join(part for part in parts if part)


def _json_text(value: Any) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)[:1200]


def _datetime_sort_key(value: Any) -> float:
    text = _clean_text(value)
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _future_coverage_from_intent(intent: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            *_as_list(intent.get("parent_themes")),
            *_as_list(intent.get("broad_topics")),
            *_as_list(intent.get("mid_topics")),
        ]
    )[:8]


def _community_future_coverage(
    base_coverage: list[str],
    signals: dict[str, list[str]],
    *,
    limit: int,
) -> list[str]:
    return _dedupe(
        [
            *base_coverage,
            *signals.get("parent_themes", []),
            *signals.get("broad_topics", []),
            *signals.get("mid_topics", []),
        ]
    )[:limit]


def _assignment_update_mode(*, action: str, weight: float) -> str:
    if action in {"create_new", "create_parent_and_absorb_existing"}:
        return "rewrite_summary"
    if weight >= 0.80:
        return "update_delta"
    return "append_reference"


def _assignment_reason_with_fit_type(assignment: dict[str, Any]) -> str:
    fit_type = _clean_text(assignment.get("fit_type"))
    reason = _clean_text(assignment.get("reason"))
    return f"fit_type={fit_type}; {reason}" if fit_type else reason


def _intent_identity(intent: dict[str, Any]) -> str:
    chunk_ids = "|".join(_as_list(intent.get("chunk_ids")))
    return _digest(
        [
            _clean_text(intent.get("cognitive_card_id")),
            _clean_text(intent.get("raw_theme")),
            _clean_text(intent.get("title_candidate")),
            _clean_text(intent.get("summary")),
            chunk_ids,
        ]
    )


def _dedupe_dicts_by_identity(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _intent_identity(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_dicts_by_key(items: list[dict[str, Any]], *, key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        value = _clean_text(item.get(key)) or _digest([json.dumps(item, ensure_ascii=False, sort_keys=True), str(index)])
        if value in seen:
            continue
        seen.add(value)
        result.append(item)
    return result


def maturity_label(source_count: int) -> str:
    if source_count <= 1:
        return "single_evidence"
    if source_count <= 5:
        return "multi_evidence"
    return "mature_topic"


def _clean_intent(intent: dict[str, Any]) -> dict[str, Any]:
    result = dict(intent)
    for key in ("raw_theme", "title_candidate", "topic_level_hint", "impact_direction", "event_stage", "timeline_position", "event_time", "summary", "supporting_text"):
        result[key] = _clean_text(result.get(key))
    for key in ("parent_themes", "broad_topics", "mid_topics", "specific_topics", "driver", "impact_target", "risk_type", "event_thread", "event_action", "actors"):
        result[key] = _dedupe(_as_list(result.get(key)))
    try:
        result["importance"] = max(0.0, min(1.0, float(result.get("importance") or 0.0)))
    except Exception:
        result["importance"] = 0.0
    return result


def _community_summary(community: CommunityDraft) -> str:
    signals = community.signal_values()
    parts = [
        f"{community.title} 聚合了 {len(set(community.source_ids))} 个来源、{len(set(community.chunk_ids))} 个 chunk 的认知信号。",
        f"父级主题：{'；'.join(signals.get('parent_themes', [])[:6])}。" if signals.get("parent_themes") else "",
        f"主要线索：{'；'.join(_dedupe([*signals.get('raw_theme', []), *signals.get('mid_topics', [])])[:6])}。"
        if signals.get("raw_theme") or signals.get("mid_topics")
        else "",
        f"影响对象：{'；'.join(signals.get('impact_target', [])[:8])}。" if signals.get("impact_target") else "",
        f"风险线索：{'；'.join(signals.get('risk_type', [])[:6])}。" if signals.get("risk_type") else "",
    ]
    return " ".join(part for part in parts if part).strip()


def _community_canonical_labels(draft: CommunityDraft, signals: dict[str, list[str]]) -> list[str]:
    return _dedupe(
        [
            draft.title,
            *draft.canonical_labels,
            *signals.get("parent_themes", []),
            *signals.get("broad_topics", []),
            *signals.get("mid_topics", []),
            *signals.get("raw_theme", []),
            *signals.get("title_candidate", []),
            *signals.get("event_thread", []),
        ]
    )[:40]


def _community_coverage_contract(draft: CommunityDraft, signals: dict[str, list[str]]) -> str:
    parent = _dedupe([draft.title, *signals.get("parent_themes", [])])[:6]
    children = _dedupe([*signals.get("broad_topics", []), *signals.get("mid_topics", []), *draft.future_coverage])[:12]
    specifics = _dedupe(signals.get("specific_topics", []))[:10]
    parts = [
        f"可承接父主题：{'、'.join(parent)}" if parent else "",
        f"可吸收子方向：{'、'.join(children)}" if children else "",
        f"当前具体线索：{'、'.join(specifics)}" if specifics else "",
    ]
    return _clip("；".join(part for part in parts if part), 420)


def _candidate_coverage_summary(signals: dict[str, list[str]], future_coverage: list[str]) -> str:
    parts = [
        f"父主题={ '、'.join(signals.get('parent_themes', [])[:4]) }" if signals.get("parent_themes") else "",
        f"已覆盖子方向={ '、'.join(_dedupe([*signals.get('mid_topics', []), *signals.get('specific_topics', [])])[:8]) }"
        if signals.get("mid_topics") or signals.get("specific_topics")
        else "",
        f"未来覆盖={ '、'.join(_dedupe(future_coverage)[:8]) }" if future_coverage else "",
    ]
    return _clip("；".join(part for part in parts if part), 360)


def _is_complex_intent(intent: dict[str, Any]) -> bool:
    return (
        len(_as_list(intent.get("parent_themes"))) + len(_as_list(intent.get("broad_topics"))) + len(_as_list(intent.get("mid_topics"))) >= 3
        or len(_as_list(intent.get("specific_topics"))) >= 3
        or len(_as_list(intent.get("impact_target"))) >= 4
        or len(_as_list(intent.get("actors"))) >= 4
        or (bool(_as_list(intent.get("risk_type"))) and bool(_as_list(intent.get("event_thread"))))
    )


def _infer_importance(intent: dict[str, Any]) -> float:
    score = 0.58
    if _as_list(intent.get("parent_themes")) or _as_list(intent.get("broad_topics")) or _as_list(intent.get("mid_topics")):
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


def _community_id(adapter_name: str, title: str) -> str:
    return f"kg_community:{COMMUNITY_PROJECTION}:l0:{_digest([adapter_name, _normalize_label(title)])}"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [_clean_text(item) for item in values if _clean_text(item)]


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = _normalize_label(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _clean_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clip(text: str, limit: int) -> str:
    text = _clean_text(text)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _normalize_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    return text


def _digest(parts: list[str]) -> str:
    data = "\n".join(str(part) for part in parts)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]
