"""原子 Cognitive Card 的提取、校验和发布服务。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, replace
from itertools import combinations
from typing import Any

import redis

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.atomic_cognitive_card import (
    ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
    ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
    INTRA_CHUNK_RELATION_KINDS,
    AtomicCardExtractionResult,
    AtomicCognitiveCard,
    StableSpanSegmenter,
    atomic_card_focus_document,
    atomic_card_summary_document,
    atomic_card_from_llm_item,
    intra_chunk_relation_from_llm_item,
    relation_probes_from_llm_items,
    render_atomic_card_prompt_input,
)
from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.domain.knowledge.relation_discovery import RelationProbe, VerifiedRelationDecision
from src.domain.knowledge.schemas import EvidenceChunk
from src.infrastructure.config import settings
from src.infrastructure.config.settings import JETTASK_PREFIX, REDIS_URL
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.vector_store.semantic_hybrid_retriever import MilvusSemanticHybridRetriever


logger = logging.getLogger(__name__)

ATOMIC_CARD_MAX_TOKENS = 20000
ATOMIC_EVIDENCE_TOPOLOGY_MAX_TOKENS = 6000
ATOMIC_CARD_PREFIX_WARM_MARK_KEY = (
    f"{JETTASK_PREFIX}:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmed"
)
ATOMIC_CARD_PREFIX_WARM_LOCK_KEY = (
    f"{JETTASK_PREFIX}:lock:kg_cognitive_card:{ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION}:prefix_warmup"
)
ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS = 0.05
ATOMIC_CARD_INTRA_CHUNK_PIPELINE_VERSION = "atomic_card_intra_chunk_relation_v1"
ATOMIC_RELATION_PROBE_GENERATOR_VERSION = "atomic_relation_probe_v18"
ATOMIC_EVIDENCE_TOPOLOGY_GENERATOR_VERSION = "atomic_evidence_topology_v14"


ATOMIC_CARD_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "local_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "focus_evidence_refs": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
        },
    },
    "required": [
        "local_card_id",
        "summary",
        "focus_evidence_refs",
    ],
    "additionalProperties": False,
}


ATOMIC_RELATION_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "target_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "relation_kind": {
            "type": "string",
            "enum": sorted(INTRA_CHUNK_RELATION_KINDS),
        },
        "basis": {"type": "string", "minLength": 1, "maxLength": 1000},
        "relation_evidence_refs": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
        },
    },
    "required": [
        "source_card_id",
        "target_card_id",
        "relation_kind",
        "basis",
        "relation_evidence_refs",
    ],
    "additionalProperties": False,
}


ATOMIC_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": ATOMIC_CARD_ITEM_SCHEMA,
        },
        "relations": {
            "type": "array",
            "items": ATOMIC_RELATION_ITEM_SCHEMA,
        },
        "skip_reason": {"type": ["string", "null"], "maxLength": 240},
    },
    "required": ["cards", "relations", "skip_reason"],
    "additionalProperties": False,
}


ATOMIC_RELATION_PROBE_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "local_card_id": {
            "type": "string",
            "pattern": "^c[1-9][0-9]*$",
        },
        "relation_probes": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": [
                            "upstream",
                            "downstream",
                            "confirmation",
                            "contradiction",
                        ],
                    },
                    "query": {"type": "string", "minLength": 1, "maxLength": 300},
                },
                "required": ["role", "query"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["local_card_id", "relation_probes"],
    "additionalProperties": False,
}


ATOMIC_RELATION_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "probe_plans": {
            "type": "array",
            "items": ATOMIC_RELATION_PROBE_ITEM_SCHEMA,
        }
    },
    "required": ["probe_plans"],
    "additionalProperties": False,
}


ATOMIC_TOPOLOGY_RELATION_PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "accepted_relation_pairs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_card_id": {"type": "string", "pattern": "^c[1-9][0-9]*$"},
                    "target_card_id": {"type": "string", "pattern": "^c[1-9][0-9]*$"},
                },
                "required": ["source_card_id", "target_card_id"],
                "additionalProperties": False,
            },
        },
        "probe_plans": {
            "type": "array",
            "items": ATOMIC_RELATION_PROBE_SCHEMA["properties"]["probe_plans"][
                "items"
            ],
        },
    },
    "required": ["accepted_relation_pairs", "probe_plans"],
    "additionalProperties": False,
}


ATOMIC_EVIDENCE_TOPOLOGY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "event_groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "group_id": {"type": "string", "pattern": "^g[1-9][0-9]*$"},
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
                    },
                    "focus": {"type": "string", "minLength": 1, "maxLength": 80},
                },
                "required": ["group_id", "evidence_refs", "focus"],
                "additionalProperties": False,
            },
        },
        "keep_separate": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "left_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
                    },
                    "right_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
                    },
                },
                "required": ["left_evidence_refs", "right_evidence_refs"],
                "additionalProperties": False,
            },
        },
        "direct_links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
                    },
                    "target_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
                    },
                    "link_evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
                    },
                    "link_statement": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 240,
                    },
                    "source_mention": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 60,
                    },
                    "relation_cue": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 60,
                    },
                    "target_mention": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 60,
                    },
                    "relation_kind": {
                        "type": "string",
                        "enum": sorted(INTRA_CHUNK_RELATION_KINDS),
                    },
                },
                "required": [
                    "source_evidence_refs",
                    "target_evidence_refs",
                    "link_evidence_refs",
                    "link_statement",
                    "source_mention",
                    "relation_cue",
                    "target_mention",
                    "relation_kind",
                ],
                "additionalProperties": False,
            },
        },
        "open_slots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evidence_refs": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string", "pattern": "^s[0-9]{4}$"},
                    },
                    "role": {
                        "type": "string",
                        "enum": [
                            "upstream",
                            "downstream",
                            "confirmation",
                            "contradiction",
                        ],
                    },
                    "endpoint_constraint": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 180,
                    },
                },
                "required": ["evidence_refs", "role", "endpoint_constraint"],
                "additionalProperties": False,
            },
        },
    },
    "required": [
        "event_groups",
        "keep_separate",
        "direct_links",
        "open_slots",
    ],
    "additionalProperties": False,
}


_ATOMIC_CARD_STAGE_SYSTEM_PROMPT = """你是知识图谱的原子 Cognitive Card 与同 Chunk Relation 抽取器。

当前调用只执行 Card 和同 Chunk Relation。输入首行是新闻发布时间；其后按原文句子换行，`<title>` 表示标题，`[sNNNN]` 是紧随文本的证据坐标。一个 Ref 可能包含多个事实，它只是引用地址，不代表 Card 边界，也不是需要逐项分类的清单。只有后续 user 明确切换阶段时才执行 Relation Probe。

Card：
1. 原文直接支持、能够独立判断真假的每个事实各生成一张 Card。事实由完整主体、一个具体动作/状态/指标、对象、时间与口径组成。
2. 原子边界是强约束：一张 Card 恰好对应一个事实键 `主体 + 单一谓词或指标 + 对象 + 单一时间口径`。一句话含有几个事实键就生成几张 Card；同一句、同一公告、同一事件或共同描述一种局面都不是合并理由。不同动作、状态、指标、报告期、绝对值、变化率和事件步骤必须拆分；原因、决定、执行、回应、结果分别作为端点。两个分句能够分别为真或为假时必须拆分，边界不确定时也拆分。
3. 只合并事实键完全相同的重复表述。不得为了减少 Card 数量而概括段落、事件链或数据组，也不得创造上位事实。summary 只陈述一个事实端点、一个核心谓词并写明完整主体。
4. 保留消息来源、声明者及“涉嫌、可能、预计、据称”等原文强度。广告、署名、重复标题、作者归纳、宽泛评价和没有明确声明者的推演不建卡。
5. focus_evidence_refs 只保留能够独立证明 summary 的最小 `sNNNN` 集合。标题只补足正文省略内容；published_at 只解释相对时间，不补造年份或事实。

Relation：
1. Cards 确定后，只映射原文明确连接的两个事实端点。Relation 必须增加 Card 未给出的连接事实；类型或方向不确定时不输出。
2. 从原文连接语义出发，不从主题相似度出发。原文必须明确表达原因、影响、触发、执行、回应、进展或限制，且两端是不同事实。文本相邻、单纯先后、共同主题和模型补全的机制都不建边。
3. 不连接重复表达、并列列举、整体与其表现、概括与具体化、总量与分项、计划与列举项，也不连接事实键相同但详细度不同的 Card。这些是 Card 边界，不是 Relation。
4. basis 必须使用两端各自的事实谓词，不得替换为概括、组成项或第三个事实。relation_evidence_refs 必须同时证明两端和连接；不补充原文没有的因果、先后或机制。集合结论无法归给一个 pair 时不输出，每对 Card 最多一条 Relation。

JSON 输出：
- 顶层严格按 `cards`、`relations`、`skip_reason` 输出，不添加其他字段。
- Card 严格且仅含 `local_card_id`、`summary`、`focus_evidence_refs`。按原文顺序连续编号为 c1、c2、c3，不得使用字母后缀或跳号。
- Relation 严格且仅含 `source_card_id`、`target_card_id`、`relation_kind`、`basis`、`relation_evidence_refs`。
- cards 非空时 skip_reason 为 `""`；cards 为空时写明原因且 relations 为空。
- 只输出 JSON，不输出分析、标签、Community、预测或 Markdown。"""


_ATOMIC_RELATION_PROBE_SYSTEM_SECTION = """Relation Probe 阶段：

仅在后续 user 明确切换到 Relation Probe 阶段后执行。冻结上一轮 Cards 和 Relations，不修改它们。

为 Card 生成可用于搜索其他 Chunk 历史 Card 的一跳关系路由：
1. 只在另一端能与当前 Card 建立直接 upstream、downstream、confirmation 或 contradiction，并显著补充解释或可信度时生成 Probe。显著变化在当前 Chunk 中没有原因时，可在整体事件 Card 上保留一条 upstream Probe，但不预设原因已成立。
2. Summary 已承担同义召回。输出前对照上一轮所有 Cards 和 Relations；候选事件已在当前 Chunk 出现时，即使换说法、补细节或改时间粒度也删除。Probe 只指向当前 Chunk 未给出的事件端点。
3. 只搜索截至 published_at 可能已发生的历史事件。不搜索之后才能出现的验证结果；Card 含预测或计划时，只搜索已发生的原因、约束、支持或反证。
4. query 是可直接语义检索的候选事件描述，不是问句。保留当前 Card 的关键主体或对象，并描述另一端的动作、状态或指标类型；不使用模板词，不补造具体主体、日期、数值、机制或既成结果。
5. 普通事实、已闭合解释和低价值明细保持空数组。多个 Card 只是同一整体事件的公司、地区、指标或组成项时，只在整体事件 Card 上生成共享 Probe。零 Probe 正常，每张 Card 最多两条，不按 role 凑数。

JSON 输出：顶层严格且仅含 `probe_plans`。每个输入 Card 按原顺序恰好输出一项，严格且仅含 `local_card_id`、`relation_probes`；空结果写 `[]`。每条 Probe 严格且仅含 `role`、`query`，role 只允许 upstream、downstream、confirmation、contradiction。只输出 JSON，不输出 Cards、Relations、分析、额外字段或 Markdown。"""


ATOMIC_CARD_SYSTEM_PROMPT = "\n\n".join(
    [
        _ATOMIC_CARD_STAGE_SYSTEM_PROMPT,
        _ATOMIC_RELATION_PROBE_SYSTEM_SECTION,
    ]
)


ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT = """现在进入 Relation Probe 阶段。严格使用 System Prompt 中的 Relation Probe 阶段规则，冻结上一轮 Cards 和 Relations，只输出 probe_plans JSON。"""


_ATOMIC_EVIDENCE_TOPOLOGY_SYSTEM_SECTION = """Evidence Topology 预处理阶段：

仅在 user 明确要求 Evidence Topology 时执行。此阶段只生成后续抽取的导航，不生成 Card 清单或 Card 文案。
1. event_groups 只划分少量具有共同主体、时间范围和叙事目标的主要事件组或传导链。它不是 Card 边界，不得把每个指标、句子或 Ref 单独列成一组；focus 只写短语。
2. keep_separate 只标记最容易被错误合并的命题边界，不得穷举所有事实，也不写解释。不同主体或立场的声明必须拆开；同一公告内的监管事实与公司经营表态必须拆开；复合分析或预测中的多个驱动因素及多个结果，只要能分别作为关系端点就必须拆开。此阶段不输出强制合并决定，避免把误判固化为 Card 边界。
3. direct_links 只记录对理解正文有价值、且原文明示的事件间关系。source_evidence_refs 和 target_evidence_refs 分别定位两个单一命题端点；一个端点混有两个可分别成立的影响或结果时，必须拆成多条 link。link_evidence_refs 必须定位原文中明确表达连接语义的文本；link_statement 必须从这些 Ref 原样复制最短的完整连续关系陈述。source_mention、relation_cue、target_mention 分别从 link_statement 原样复制 source 指代、关系措辞和 target 指代，三者缺一不可；target_mention 不能用日期、段落引导语或 source 的组成部分冒充。只有端点动作、章节引导语、普通顺承语或脱离上下文无法识别两端的片段都不合格。找不到这样的原文陈述就不输出。标题并列、报道顺序、时间先后或常识机制不能替代连接证据；不得把致电与回答、发布与被发布内容、公告与公告内容、数值属性之间建立为因果边。
4. open_slots 只记录当前 Chunk 尚未出现、截至 published_at 可能已经发生、且从历史材料找到后会显著补充原因、影响、可信度或反证的一跳事件端点。端点必须能独立生成 Card；带有“可能、预计、计划”等未发生强度的命题不得搜索其 downstream，且不得搜索尚未发生的后续灾情、处置、验证结果或预测兑现。普通明细、历史比较、泛化背景、已有完整解释和“查找某数值原因”不生成。每条只描述一种关系角色和一个端点约束，不使用“或”拼接多个搜索目标；endpoint_constraint 不写问句，不猜测具体事实。
5. 除 direct_links.link_statement 必须原样引用外，禁止摘抄或改写原文；禁止输出摘要、事实台账、逐 Ref 解释、分析过程和候选方案。只输出最终导航；内容少是正常结果。

event_groups、keep_separate、direct_links 和 open_slots 都只是下一阶段可纠正的阅读导航，不是事实结论、完整清单或输出配额。后续阶段必须重新依据原文判断，可以忽略、拆分、合并或补充导航中的项目；不得为了覆盖 event_group 而建 Card，不得仅因 keep_separate 而机械拆卡，不得仅因 direct_link 而建边，也不得把 open_slots 当作 Probe 白名单。"""


ATOMIC_EVIDENCE_TOPOLOGY_REQUEST = """现在进入 Evidence Topology 预处理阶段。只输出 event_groups、keep_separate、direct_links、open_slots JSON。"""


ATOMIC_CARD_FROM_TOPOLOGY_FOLLOWUP_PROMPT = """现在进入 Card/Relation 阶段。上一轮只提供可能的命题边界和候选直连，不具有约束力，也可能遗漏或判断错误。重新阅读原文并独立决定 Card 边界：keep_separate 只有在两侧确实是可分别成立的命题时才拆分，否则忽略；未被提示的事实仍按 Card 规则处理。direct_links 只提示可能存在连接，只有 source_mention、relation_cue、target_mention 和 link_statement 与最终两张 Card 的核心事实及方向一致时才生成 Relation；原文另有明确关系句时也可生成。relation_evidence_refs 必须覆盖同时证明两端与连接的最小原文范围。不要把标题并列、报道顺序、过渡语或常识推断补成关系。不要复述、解释或评价导航，只输出 cards、relations、skip_reason JSON。"""


ATOMIC_RELATION_PROBE_FROM_TOPOLOGY_FOLLOWUP_PROMPT = """现在进入 Relation 审查与 Probe 阶段。冻结上一轮 Cards，不新增或改写 Relation。逐条回到原文复核上一轮 Relations：source Card 与 target Card 必须是两个不同事实，relation_evidence_refs 必须同时证明两端和连接语义，basis 不得加入原文没有的“导致、引发、验证、回应”等机制。若 Relation 对应清洗后的 direct_links，还要确认 source_mention 与 target_mention 分别对应两张 Card 的核心谓词；不在 direct_links 中不代表必然无效，但必须由原文中的明确关系句独立证明。任一端只对应泛称因素、第三个事实、宽泛支撑、标题并列、报道顺序、过渡语或常识推断时，不保留。accepted_relation_pairs 只能从输入的 relation_candidates 逐字复制 Card ID pair；不确定就省略，绝不添加其他 pair。open_slots 只是候选搜索方向，不是完整清单：结合最终 Cards 和原文独立判断是否采用、改写、忽略或补充。新增 Probe 仍须满足 System Prompt 的直接关系、历史可发生性和显著价值要求；预测或计划 Card 不映射 downstream。每个 Card 仍须在 probe_plans 中恰好出现一次。只输出 accepted_relation_pairs、probe_plans JSON。"""


_ATOMIC_CARD_TOPOLOGY_STAGE_SYSTEM_PROMPT = _ATOMIC_CARD_STAGE_SYSTEM_PROMPT.replace(
    """Card：
1. 原文直接支持、能够独立判断真假的每个事实各生成一张 Card。事实由完整主体、一个具体动作/状态/指标、对象、时间与口径组成。
2. 原子边界是强约束：一张 Card 恰好对应一个事实键 `主体 + 单一谓词或指标 + 对象 + 单一时间口径`。一句话含有几个事实键就生成几张 Card；同一句、同一公告、同一事件或共同描述一种局面都不是合并理由。不同动作、状态、指标、报告期、绝对值、变化率和事件步骤必须拆分；原因、决定、执行、回应、结果分别作为端点。两个分句能够分别为真或为假时必须拆分，边界不确定时也拆分。
3. 只合并事实键完全相同的重复表述。不得为了减少 Card 数量而概括段落、事件链或数据组，也不得创造上位事实。summary 只陈述一个事实端点、一个核心谓词并写明完整主体。""",
    """Card：
1. 原文直接支持、对后续关系发现有独立意义的每个事实命题生成一张 Card。Card 应保留完整主体、核心动作或状态、对象、时间和必要口径。
2. Card 边界按命题能否独立成立判断，而不是按句子、段落或叙事主题判断。删除其中一个动作、状态、原因、结果或判断后，其余内容仍可独立为真或为假，就必须拆成不同 Card；多个原因共同指向一个结果、一个原因产生多个结果时，各个可独立核验的端点也分别建卡，不能把整条传导链写进一张 summary。
3. 同一主体、同一时间、同一核心状态的一组数值属性只有在共同描述同一个谓词、离开该状态没有独立解释价值时才保留在一张 Card 中。不同动作、状态、主体或立场、时间阶段、关系角色必须拆分。同一公告内的监管事实和公司经营表态仍是不同命题；预测中的条件、方向、约束和结果也按可独立核验的命题拆分。
Relation 的两个端点不能被预先藏进同一张 Card。不得生成总述 Card 再重复生成其全部明细 Card，也不得用一个概括性 Card 替代原文明示的多个关系端点；summary 只表达一个可用于关系判断的命题。""",
).replace(
    "当前调用只执行 Card 和同 Chunk Relation。",
    "按 user 明确指定的阶段执行 Evidence Topology、Card/Relation 或 Relation Probe。",
)


ATOMIC_CARD_TOPOLOGY_SYSTEM_PROMPT = "\n\n".join(
    [
        _ATOMIC_CARD_TOPOLOGY_STAGE_SYSTEM_PROMPT,
        _ATOMIC_EVIDENCE_TOPOLOGY_SYSTEM_SECTION,
        _ATOMIC_RELATION_PROBE_SYSTEM_SECTION,
    ]
)


@dataclass(frozen=True)
class AtomicCardStageResult:
    status: str
    cards: list[AtomicCognitiveCard]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class _ValidatedAtomicCardResponse:
    cards: list[AtomicCognitiveCard]
    cards_by_local_id: dict[str, AtomicCognitiveCard]
    relations: list[VerifiedRelationDecision]
    skip_reason: str
    discarded_card_count: int = 0
    discarded_relation_count: int = 0
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ValidatedProbeResponse:
    probes_by_local_id: dict[str, list[RelationProbe]]
    accepted_relation_pairs: frozenset[tuple[str, str]] | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class _EvidenceTopologyResult:
    data: dict[str, Any]
    response: Any
    messages: list[dict[str, Any]]


@dataclass(frozen=True)
class _AtomicCardModelRoute:
    model: str
    tier: str


def _response_cannot_be_safely_repaired(response: Any) -> bool:
    raw_payload = getattr(response, "raw_payload", None) or {}
    if str(raw_payload.get("finish_reason") or "").strip().lower() == "length":
        return True
    proxy = getattr(response, "proxy", None) or {}
    return bool(proxy.get("json_prefix_continuation_attempted")) and not bool(
        proxy.get("json_prefix_continuation_success")
    )


def _response_message_content(response: Any) -> str:
    content = str(getattr(response, "text", "") or "").strip()
    if content:
        return content
    return json.dumps(
        getattr(response, "structured_output", None),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _response_usage_diagnostics(response: Any) -> dict[str, Any]:
    usage = dict(getattr(response, "usage", None) or {})
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "prompt_cache_hit_tokens": int(usage.get("prompt_cache_hit_tokens") or 0),
        "prompt_cache_miss_tokens": int(usage.get("prompt_cache_miss_tokens") or 0),
        "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
        "reasoning_chars": len(str(getattr(response, "reasoning_content", "") or "")),
        "duration_ms": int(getattr(response, "duration_ms", 0) or 0),
        "cache_hit": bool(getattr(response, "cache_hit", False)),
    }


class AtomicCognitiveCardExtractor:
    """先提取 Card 与同 Chunk 关系，再在同一对话中续问跨 Chunk Probe。"""

    def __init__(
        self,
        llm: Any | None = None,
        *,
        model: str | None = None,
        provider: str | None = None,
        thinking_type: str | None = None,
        relation_probe_thinking_type: str | None = None,
        system_prompt: str | None = None,
        relation_probe_followup_prompt: str | None = None,
        prompt_profile: str | None = None,
        evidence_topology_preplan: bool = False,
        concurrency: int = 4,
        segmenter: StableSpanSegmenter | None = None,
    ) -> None:
        self._llm = llm or get_llm_gateway_service()
        self._model_override = str(model or "").strip() or None
        fallback_model = resolve_kg_llm_model("kg_cognitive_card")
        self._simple_model = str(
            settings.KG_COGNITIVE_CARD_SIMPLE_MODEL or fallback_model
        ).strip()
        self._complex_model = str(
            settings.KG_COGNITIVE_CARD_COMPLEX_MODEL or fallback_model
        ).strip()
        self._simple_max_spans = max(
            1, int(settings.KG_COGNITIVE_CARD_SIMPLE_MAX_SPANS)
        )
        self._simple_max_chars = max(
            1, int(settings.KG_COGNITIVE_CARD_SIMPLE_MAX_CHARS)
        )
        self._provider = str(provider or "").strip() or None
        normalized_thinking_type = str(
            thinking_type
            if thinking_type is not None
            else settings.KG_COGNITIVE_CARD_THINKING_TYPE
        ).strip().lower()
        if normalized_thinking_type not in {"", "enabled", "disabled"}:
            raise ValueError("thinking_type 只允许 enabled、disabled 或空值")
        normalized_probe_thinking_type = str(
            relation_probe_thinking_type
            if relation_probe_thinking_type is not None
            else settings.KG_RELATION_PROBE_THINKING_TYPE
        ).strip().lower()
        if normalized_probe_thinking_type not in {"", "enabled", "disabled"}:
            raise ValueError(
                "relation_probe_thinking_type 只允许 enabled、disabled 或空值"
            )
        self._card_thinking_type = normalized_thinking_type or None
        self._probe_thinking_type = (
            normalized_probe_thinking_type or self._card_thinking_type
        )
        self._evidence_topology_preplan = bool(evidence_topology_preplan)
        default_system_prompt = (
            ATOMIC_CARD_TOPOLOGY_SYSTEM_PROMPT
            if self._evidence_topology_preplan
            else ATOMIC_CARD_SYSTEM_PROMPT
        )
        self._system_prompt = str(system_prompt or default_system_prompt).strip()
        self._relation_probe_followup_prompt = str(
            relation_probe_followup_prompt or ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT
        ).strip()
        self._prompt_profile = str(prompt_profile or "production").strip()
        self._prompt_fingerprint = hashlib.sha256(
            self._system_prompt.encode("utf-8")
        ).hexdigest()[:16]
        self._concurrency = max(1, concurrency)
        self._semaphore = asyncio.Semaphore(self._concurrency)
        self._segmenter = segmenter or StableSpanSegmenter()
        self._redis: Any | None = None

    async def extract(self, chunks: list[EvidenceChunk]) -> list[AtomicCognitiveCard]:
        results = await self.extract_with_diagnostics(chunks)
        return [card for result in results for card in result.cards]

    async def extract_with_diagnostics(
        self,
        chunks: list[EvidenceChunk],
    ) -> list[AtomicCardExtractionResult]:
        async def run(chunk: EvidenceChunk) -> AtomicCardExtractionResult:
            async with self._semaphore:
                return await self._extract_one(chunk)

        tasks = [asyncio.create_task(run(chunk)) for chunk in chunks]
        try:
            return await asyncio.gather(*tasks)
        except Exception:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    async def _extract_one(self, chunk: EvidenceChunk) -> AtomicCardExtractionResult:
        with langfuse_observation(
            name="kg.atomic_card.segment_chunk",
            as_type="span",
            input={"chunk_id": chunk.chunk_id, "text_chars": len(chunk.content)},
        ):
            sentence_blocks = self._segmenter.segment_blocks(chunk.content)
            spans = [part for block in sentence_blocks for part in block.parts]
            langfuse_update_span(
                output={
                    "chunk_id": chunk.chunk_id,
                    "sentence_block_count": len(sentence_blocks),
                    "span_count": len(spans),
                },
                status_message="completed",
            )
        if not spans:
            return AtomicCardExtractionResult(
                chunk_id=chunk.chunk_id,
                spans=[],
                cards=[],
                relations=[],
                input_text_chars=len(chunk.content),
            )

        payload = dict(chunk.payload or {})
        model_route = self._select_model_route(
            span_count=len(spans),
            text_chars=len(chunk.content),
        )
        prompt_input = render_atomic_card_prompt_input(
            source_published_at=payload.get("published_at") or "",
            source_title=payload.get("title") or "",
            sentence_blocks=sentence_blocks,
        )
        source_messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": prompt_input},
        ]
        with langfuse_observation(
            name="kg.atomic_card.extract_cards_and_relations",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "text_chars": len(chunk.content),
                "sentence_block_count": len(sentence_blocks),
                "span_count": len(spans),
                "selected_model": model_route.model,
                "model_route": model_route.tier,
            },
            metadata={"schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION},
        ):
            prefix_scope = self._prefix_warm_scope(model_route.model)
            warmup_lock = await self._claim_prefix_warmup_lock(prefix_scope)
            try:
                evidence_topology: dict[str, Any] = {}
                stage_usage: dict[str, dict[str, Any]] = {}
                card_messages = source_messages
                if self._evidence_topology_preplan:
                    topology_result = await self._preplan_evidence_topology(
                        chunk=chunk,
                        spans=spans,
                        source_messages=source_messages,
                        model_route=model_route,
                    )
                    evidence_topology = topology_result.data
                    stage_usage["evidence_topology"] = _response_usage_diagnostics(
                        topology_result.response
                    )
                    card_navigation = {
                        "keep_separate": list(
                            topology_result.data.get("keep_separate") or []
                        ),
                        "direct_links": list(
                            topology_result.data.get("direct_links") or []
                        ),
                    }
                    card_messages = [
                        *topology_result.messages,
                        {
                            "role": "assistant",
                            "content": json.dumps(
                                card_navigation,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        },
                        {
                            "role": "user",
                            "content": ATOMIC_CARD_FROM_TOPOLOGY_FOLLOWUP_PROMPT,
                        },
                    ]
                    await self._mark_prefix_warmed(
                        prefix_scope,
                        settle=warmup_lock is not None,
                    )
                request = self._card_request(
                    chunk=chunk,
                    payload=payload,
                    messages=card_messages,
                    model_route=model_route,
                    span_count=len(spans),
                )
                response = await self._llm.generate(request)
                stage_usage["cards_and_relations"] = _response_usage_diagnostics(response)
                if not self._evidence_topology_preplan:
                    await self._mark_prefix_warmed(
                        prefix_scope,
                        settle=warmup_lock is not None,
                    )
                validated, repaired, repair_attempted, accepted_card_response = (
                    await self._cards_from_response(
                        chunk=chunk,
                        spans=spans,
                        request=request,
                        response=response,
                    )
                )
                stage_usage["cards_and_relations"] = _response_usage_diagnostics(
                    accepted_card_response
                )
                (
                    cards_with_probes,
                    probe_repaired,
                    probe_repair_attempted,
                    probe_issues,
                    probe_usage,
                    accepted_relation_pairs,
                ) = (
                    await self._plan_relation_probes(
                        chunk=chunk,
                        cards_by_local_id=validated.cards_by_local_id,
                        relation_count=len(validated.relations),
                        base_messages=card_messages,
                        card_response=accepted_card_response,
                        model_route=model_route,
                        evidence_topology=evidence_topology,
                    )
                )
                if accepted_relation_pairs is not None:
                    accepted_relations = [
                        relation
                        for relation in validated.relations
                        if tuple(
                            sorted(
                                (
                                    relation.source_card_id,
                                    relation.target_card_id,
                                )
                            )
                        )
                        in accepted_relation_pairs
                    ]
                    discarded_by_review = len(validated.relations) - len(
                        accepted_relations
                    )
                    if discarded_by_review:
                        validated = replace(
                            validated,
                            relations=accepted_relations,
                            discarded_relation_count=(
                                validated.discarded_relation_count
                                + discarded_by_review
                            ),
                            issues=(
                                *validated.issues,
                                f"Relation 审查丢弃 {discarded_by_review} 条未获原文支持的关系",
                            ),
                        )
                stage_usage["relation_probes"] = probe_usage
                repaired = repaired or probe_repaired
                repair_attempted = repair_attempted or probe_repair_attempted
                all_issues = [*validated.issues, *probe_issues]
                langfuse_update_span(
                    output={
                        "chunk_id": chunk.chunk_id,
                        "card_count": len(cards_with_probes),
                        "card_ids": [card.cognitive_card_id for card in cards_with_probes],
                        "summary_chars": [len(card.summary) for card in cards_with_probes],
                        "focus_ref_counts": [len(card.focus_evidence_refs) for card in cards_with_probes],
                        "relation_probe_counts": [
                            len(card.relation_probes) for card in cards_with_probes
                        ],
                        "relation_probe_roles": [
                            [probe.role for probe in card.relation_probes]
                            for card in cards_with_probes
                        ],
                        "relation_probes": [
                            {
                                "cognitive_card_id": card.cognitive_card_id,
                                "summary": card.summary,
                                "items": [
                                    probe.as_dict() for probe in card.relation_probes
                                ],
                            }
                            for card in cards_with_probes
                            if card.relation_probes
                        ],
                        "intra_chunk_relation_count": len(validated.relations),
                        "intra_chunk_relation_kinds": [
                            relation.relation_kind for relation in validated.relations
                        ],
                        "repaired": repaired,
                        "repair_attempted": repair_attempted,
                        "discarded_card_count": validated.discarded_card_count,
                        "discarded_relation_count": validated.discarded_relation_count,
                        "validation_issues": all_issues,
                        "skip_reason": validated.skip_reason,
                        "prefix_warmup_owner": warmup_lock is not None,
                        "selected_model": model_route.model,
                        "model_route": model_route.tier,
                        "evidence_topology_preplan": self._evidence_topology_preplan,
                        "evidence_topology": evidence_topology,
                        "llm_stage_usage": stage_usage,
                    },
                    status_message="completed",
                )
                return AtomicCardExtractionResult(
                    chunk_id=chunk.chunk_id,
                    spans=spans,
                    cards=cards_with_probes,
                    relations=validated.relations,
                    selected_model=model_route.model,
                    model_route=model_route.tier,
                    input_text_chars=len(chunk.content),
                    repaired=repaired,
                    repair_attempted=repair_attempted,
                    discarded_card_count=validated.discarded_card_count,
                    discarded_relation_count=validated.discarded_relation_count,
                    validation_issues=all_issues,
                    skip_reason=validated.skip_reason,
                    evidence_topology=evidence_topology,
                    llm_stage_usage=stage_usage,
                )
            finally:
                if warmup_lock is not None:
                    await self._release_prefix_warmup_lock(warmup_lock)

    def _card_request(
        self,
        *,
        chunk: EvidenceChunk,
        payload: dict[str, Any],
        messages: list[dict[str, Any]],
        model_route: _AtomicCardModelRoute,
        span_count: int,
    ) -> LLMProxyRequest:
        return LLMProxyRequest(
            model=model_route.model,
            provider=self._provider,
            messages=messages,
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=ATOMIC_CARD_SCHEMA,
            provider_options=self._provider_options(self._card_thinking_type),
            metadata={
                "task": "kg_cognitive_card",
                "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                "source_type": payload.get("source_type") or "",
                "source_id": payload.get("source_id") or "",
                "chunk_id": chunk.chunk_id,
                "model_route": model_route.tier,
                "span_count": span_count,
                "input_text_chars": len(chunk.content),
                "thinking_type": self._card_thinking_type or "provider_default",
                "prompt_profile": self._prompt_profile,
                "prompt_fingerprint": self._prompt_fingerprint,
                "evidence_topology_preplan": self._evidence_topology_preplan,
                "_cache_key_metadata": {
                    "task": "kg_cognitive_card",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                    "model_route": model_route.tier,
                    "thinking_type": self._card_thinking_type or "provider_default",
                    "prompt_profile": self._prompt_profile,
                    "prompt_fingerprint": self._prompt_fingerprint,
                    "evidence_topology_preplan": self._evidence_topology_preplan,
                },
            },
            use_cache=True,
        )

    async def _preplan_evidence_topology(
        self,
        *,
        chunk: EvidenceChunk,
        spans: list[Any],
        source_messages: list[dict[str, Any]],
        model_route: _AtomicCardModelRoute,
    ) -> _EvidenceTopologyResult:
        messages = [
            *source_messages,
            {"role": "user", "content": ATOMIC_EVIDENCE_TOPOLOGY_REQUEST},
        ]
        payload = dict(chunk.payload or {})
        request = LLMProxyRequest(
            model=model_route.model,
            provider=self._provider,
            messages=messages,
            temperature=0,
            max_tokens=ATOMIC_EVIDENCE_TOPOLOGY_MAX_TOKENS,
            json_schema=ATOMIC_EVIDENCE_TOPOLOGY_SCHEMA,
            provider_options=self._provider_options(self._card_thinking_type),
            metadata={
                "task": "kg_atomic_evidence_topology",
                "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                "generator_version": ATOMIC_EVIDENCE_TOPOLOGY_GENERATOR_VERSION,
                "source_type": payload.get("source_type") or "",
                "source_id": payload.get("source_id") or "",
                "chunk_id": chunk.chunk_id,
                "model_route": model_route.tier,
                "span_count": len(spans),
                "input_text_chars": len(chunk.content),
                "thinking_type": self._card_thinking_type or "provider_default",
                "prompt_profile": self._prompt_profile,
                "prompt_fingerprint": self._prompt_fingerprint,
                "_cache_key_metadata": {
                    "task": "kg_atomic_evidence_topology",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_EVIDENCE_TOPOLOGY_GENERATOR_VERSION,
                    "model_route": model_route.tier,
                    "thinking_type": self._card_thinking_type or "provider_default",
                    "prompt_profile": self._prompt_profile,
                    "prompt_fingerprint": self._prompt_fingerprint,
                },
            },
            use_cache=True,
        )
        with langfuse_observation(
            name="kg.atomic_card.preplan_evidence_topology",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "span_count": len(spans),
                "selected_model": model_route.model,
                "model_route": model_route.tier,
            },
        ):
            response = await self._llm.generate(request)
            if _response_cannot_be_safely_repaired(response):
                raise RuntimeError(
                    "Evidence Topology 输出在 Prefix Completion 后仍未完成，"
                    f"chunk_id={chunk.chunk_id}; 禁止重新执行完整业务请求"
                )
            topology = self._validate_evidence_topology(
                response.structured_output,
                evidence_text_by_ref={span.ref: span.text for span in spans},
            )
            raw_link_count = len(
                (response.structured_output or {}).get("direct_links") or []
            )
            raw_separate_count = len(
                (response.structured_output or {}).get("keep_separate") or []
            )
            raw_slot_count = len(
                (response.structured_output or {}).get("open_slots") or []
            )
            langfuse_update_span(
                output={
                    "chunk_id": chunk.chunk_id,
                    "event_group_count": len(topology["event_groups"]),
                    "keep_separate_count": len(topology["keep_separate"]),
                    "discarded_keep_separate_count": max(
                        0,
                        raw_separate_count - len(topology["keep_separate"]),
                    ),
                    "direct_link_count": len(topology["direct_links"]),
                    "discarded_direct_link_count": max(
                        0,
                        raw_link_count - len(topology["direct_links"]),
                    ),
                    "open_slot_count": len(topology["open_slots"]),
                    "discarded_open_slot_count": max(
                        0,
                        raw_slot_count - len(topology["open_slots"]),
                    ),
                    "topology": topology,
                    "usage": _response_usage_diagnostics(response),
                },
                status_message="completed",
            )
        return _EvidenceTopologyResult(
            data=topology,
            response=response,
            messages=messages,
        )

    @staticmethod
    def _validate_evidence_topology(
        data: Any,
        *,
        evidence_text_by_ref: dict[str, str],
    ) -> dict[str, Any]:
        valid_refs = set(evidence_text_by_ref)
        if not isinstance(data, dict):
            raise ValueError("Evidence Topology 顶层输出必须是 JSON object")
        expected = {
            "event_groups",
            "keep_separate",
            "direct_links",
            "open_slots",
        }
        if set(data) != expected:
            raise ValueError(
                "Evidence Topology 顶层字段不符合契约: "
                f"missing={sorted(expected.difference(data))}, "
                f"extra={sorted(set(data).difference(expected))}"
            )
        event_groups = data.get("event_groups")
        keep_separate = data.get("keep_separate")
        direct_links = data.get("direct_links")
        open_slots = data.get("open_slots")
        if not isinstance(event_groups, list):
            raise ValueError("event_groups 必须是数组")
        if not isinstance(keep_separate, list):
            raise ValueError("keep_separate 必须是数组")
        if not isinstance(direct_links, list):
            raise ValueError("direct_links 必须是数组")
        if not isinstance(open_slots, list):
            raise ValueError("open_slots 必须是数组")

        def validate_refs(refs: Any, *, location: str) -> tuple[str, ...]:
            if not isinstance(refs, list) or not refs:
                raise ValueError(f"{location} 必须是非空数组")
            normalized = tuple(str(ref).strip() for ref in refs)
            unknown_refs = sorted(set(normalized).difference(valid_refs))
            if unknown_refs:
                raise ValueError(f"{location} 引用了未知 evidence_refs: {unknown_refs}")
            return normalized

        for index, item in enumerate(event_groups, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"event_group[{index}] 必须是对象")
            group_id = str(item.get("group_id") or "").strip()
            expected_id = f"g{index}"
            if group_id != expected_id:
                raise ValueError(
                    f"event_group[{index}].group_id 应为 {expected_id}，实际为 {group_id}"
                )
            validate_refs(
                item.get("evidence_refs"),
                location=f"event_group[{index}].evidence_refs",
            )
        seen_separate: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        useful_separate: list[dict[str, Any]] = []
        for index, item in enumerate(keep_separate, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"keep_separate[{index}] 必须是对象")
            left_refs = validate_refs(
                item.get("left_evidence_refs"),
                location=f"keep_separate[{index}].left_evidence_refs",
            )
            right_refs = validate_refs(
                item.get("right_evidence_refs"),
                location=f"keep_separate[{index}].right_evidence_refs",
            )
            if left_refs == right_refs:
                logger.warning(
                    "丢弃两端相同的 Evidence Topology keep_separate: index=%s",
                    index,
                )
                continue
            pair = tuple(sorted((left_refs, right_refs)))
            if pair in seen_separate:
                logger.warning(
                    "丢弃重复的 Evidence Topology keep_separate: index=%s",
                    index,
                )
                continue
            seen_separate.add(pair)
            useful_separate.append(item)
        seen_links: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
        grounded_links: list[dict[str, Any]] = []
        for index, item in enumerate(direct_links, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"direct_link[{index}] 必须是对象")
            source_refs = validate_refs(
                item.get("source_evidence_refs"),
                location=f"direct_link[{index}].source_evidence_refs",
            )
            target_refs = validate_refs(
                item.get("target_evidence_refs"),
                location=f"direct_link[{index}].target_evidence_refs",
            )
            link_refs = validate_refs(
                item.get("link_evidence_refs"),
                location=f"direct_link[{index}].link_evidence_refs",
            )
            link_statement = "".join(
                str(item.get("link_statement") or "").split()
            )
            link_text = "".join(
                "".join(str(evidence_text_by_ref[ref]).split()) for ref in link_refs
            )
            if not link_statement or link_statement not in link_text:
                logger.warning(
                    "丢弃未接地的 Evidence Topology direct_link: "
                    "index=%s link_statement=%r",
                    index,
                    item.get("link_statement"),
                )
                continue
            proof_parts = {
                field: "".join(str(item.get(field) or "").split())
                for field in ("source_mention", "relation_cue", "target_mention")
            }
            invalid_parts = [
                field
                for field, value in proof_parts.items()
                if not value or value not in link_statement
            ]
            if invalid_parts:
                logger.warning(
                    "丢弃关系证明字段未接地的 Evidence Topology direct_link: "
                    "index=%s fields=%s",
                    index,
                    invalid_parts,
                )
                continue
            if source_refs == target_refs:
                logger.warning(
                    "丢弃两端相同的 Evidence Topology direct_link: index=%s",
                    index,
                )
                continue
            pair = tuple(sorted((source_refs, target_refs)))
            if pair in seen_links:
                logger.warning(
                    "丢弃重复的 Evidence Topology direct_link: index=%s",
                    index,
                )
                continue
            seen_links.add(pair)
            grounded_links.append(item)
        seen_slots: set[tuple[tuple[str, ...], str]] = set()
        useful_slots: list[dict[str, Any]] = []
        for index, item in enumerate(open_slots, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"open_slot[{index}] 必须是对象")
            refs = validate_refs(
                item.get("evidence_refs"),
                location=f"open_slot[{index}].evidence_refs",
            )
            role = str(item.get("role") or "").strip()
            key = (refs, role)
            if key in seen_slots:
                logger.warning(
                    "丢弃重复的 Evidence Topology open_slot: index=%s",
                    index,
                )
                continue
            seen_slots.add(key)
            useful_slots.append(item)
        return {
            **data,
            "keep_separate": useful_separate,
            "direct_links": grounded_links,
            "open_slots": useful_slots,
        }

    async def _cards_from_response(
        self,
        *,
        chunk: EvidenceChunk,
        spans: list[Any],
        request: LLMProxyRequest,
        response: Any,
    ) -> tuple[_ValidatedAtomicCardResponse, bool, bool, Any]:
        if _response_cannot_be_safely_repaired(response):
            raise RuntimeError(
                "原子 Cognitive Card 输出在 Prefix Completion 后仍未完成，"
                f"chunk_id={chunk.chunk_id}; 禁止重新执行完整业务请求"
            )
        issues: list[str] = []
        with langfuse_observation(
            name="kg.atomic_card.validate",
            as_type="span",
            input={"chunk_id": chunk.chunk_id},
        ):
            try:
                validated = self._validate_card_response(
                    chunk,
                    spans,
                    response.structured_output,
                )
                langfuse_update_span(
                    output={
                        "valid": True,
                        "card_count": len(validated.cards),
                        "discarded_card_count": validated.discarded_card_count,
                        "issues": list(validated.issues),
                    },
                    status_message="completed",
                )
                return validated, False, False, response
            except Exception as exc:
                issues.append(str(exc))
                langfuse_update_span(
                    output={"valid": False, "issues": issues},
                    level="WARNING",
                    status_message="repair_required",
                )

        with langfuse_observation(
            name="kg.atomic_card.repair",
            as_type="span",
            input={"chunk_id": chunk.chunk_id, "issues": issues},
        ):
            repaired = await self._llm.repair_with_feedback(
                request,
                response,
                issues,
                instruction=(
                    "上一轮原子 Cognitive Card 输出未通过契约校验。"
                    "只修复事件边界、JSON 结构和 Span Ref 合规性；"
                    "先修复去重后的最终 cards，再修复只引用这些 Card 的 relations；"
                    "完成 Card 拆分后废弃所有草稿编号，严格按最终 cards 数组顺序"
                    "重新编号为 c1、c2、c3，禁止字母后缀、跳号或保留拆分前编号；"
                    "修复后重新检查每个 Card 的焦点证据是否完整支撑 Summary，"
                    "缺少支撑时补充已有 Span Ref 或删除对应表述；"
                    "不得新增外部事实，不得恢复旧 topic_intents 或主题标签字段。"
                ),
                retry_reason="atomic_cognitive_card_validation_invalid",
            )
        try:
            validated = self._validate_card_response(
                chunk,
                spans,
                repaired.structured_output,
            )
            return validated, True, True, repaired
        except Exception as exc:
            raise RuntimeError(
                f"原子 Cognitive Card 修复后仍未通过校验: chunk_id={chunk.chunk_id}; "
                f"first_issues={issues}; repair_issue={exc}"
            ) from exc

    @staticmethod
    def _validate_card_response(
        chunk: EvidenceChunk,
        spans: list[Any],
        data: Any,
    ) -> _ValidatedAtomicCardResponse:
        if not isinstance(data, dict):
            raise ValueError(f"顶层输出必须是 JSON object，实际为 {type(data).__name__}")
        expected_top_level = {"cards", "relations", "skip_reason"}
        if set(data) != expected_top_level:
            raise ValueError(
                "顶层字段不符合契约: "
                f"missing={sorted(expected_top_level.difference(data))}, "
                f"extra={sorted(set(data).difference(expected_top_level))}"
            )
        raw_cards = data.get("cards")
        if not isinstance(raw_cards, list):
            raise ValueError("cards 必须是数组")
        issues: list[str] = []
        skip_reason = str(data.get("skip_reason") or "").strip()
        if not raw_cards and not skip_reason:
            raise ValueError("cards 为空时必须提供 skip_reason")
        cards: list[AtomicCognitiveCard] = []
        cards_by_local_id: dict[str, AtomicCognitiveCard] = {}
        card_ids: set[str] = set()
        for index, item in enumerate(raw_cards, start=1):
            try:
                if not isinstance(item, dict):
                    raise ValueError("元素不是对象")
                local_card_id = str(item.get("local_card_id") or "").strip()
                expected_local_id = f"c{index}"
                if local_card_id != expected_local_id:
                    raise ValueError(
                        f"local_card_id 应为 {expected_local_id}，实际为 {local_card_id}"
                    )
                raw_card = {key: value for key, value in item.items() if key != "local_card_id"}
                card = atomic_card_from_llm_item(chunk, raw_card, spans=spans)
                if card.cognitive_card_id in card_ids:
                    raise ValueError("与前序 Card 身份重复")
            except Exception as exc:
                raise ValueError(f"card[{index}] 不符合基础契约: {exc}") from exc
            cards.append(card)
            card_ids.add(card.cognitive_card_id)
            cards_by_local_id[local_card_id] = card

        raw_relations = data.get("relations")
        if not isinstance(raw_relations, list):
            issues.append("relations 不是数组，已丢弃全部关系")
            raw_relations = []
            discarded_relation_count = 1
        else:
            discarded_relation_count = 0
        relations: list[VerifiedRelationDecision] = []
        seen_pairs: set[tuple[str, str]] = set()
        for index, item in enumerate(raw_relations, start=1):
            try:
                if not isinstance(item, dict):
                    raise ValueError("元素不是对象")
                local_pair = tuple(
                    sorted(
                        (
                            str(item.get("source_card_id") or "").strip(),
                            str(item.get("target_card_id") or "").strip(),
                        )
                    )
                )
                if local_pair in seen_pairs:
                    raise ValueError(f"同一 Card 对重复输出: {local_pair}")
                relation = intra_chunk_relation_from_llm_item(
                    item,
                    chunk=chunk,
                    spans=spans,
                    cards_by_local_id=cards_by_local_id,
                )
            except Exception as exc:
                discarded_relation_count += 1
                issues.append(f"relation[{index}] 已丢弃: {exc}")
                continue
            seen_pairs.add(local_pair)
            relations.append(relation)
        return _ValidatedAtomicCardResponse(
            cards=cards,
            cards_by_local_id=cards_by_local_id,
            relations=relations,
            skip_reason=skip_reason,
            discarded_card_count=0,
            discarded_relation_count=discarded_relation_count,
            issues=tuple(issues),
        )

    async def _plan_relation_probes(
        self,
        *,
        chunk: EvidenceChunk,
        cards_by_local_id: dict[str, AtomicCognitiveCard],
        relation_count: int,
        base_messages: list[dict[str, Any]],
        card_response: Any,
        model_route: _AtomicCardModelRoute,
        evidence_topology: dict[str, Any] | None = None,
    ) -> tuple[
        list[AtomicCognitiveCard],
        bool,
        bool,
        tuple[str, ...],
        dict[str, Any],
        frozenset[tuple[str, str]] | None,
    ]:
        if not cards_by_local_id:
            return [], False, False, (), {}, None

        assistant_content = _response_message_content(card_response)
        review_relations = bool(evidence_topology)
        card_output = getattr(card_response, "structured_output", None) or {}
        raw_relation_items = list(card_output.get("relations") or [])
        allowed_local_relation_pairs = frozenset(
            tuple(
                sorted(
                    (
                        str(item.get("source_card_id") or "").strip(),
                        str(item.get("target_card_id") or "").strip(),
                    )
                )
            )
            for item in raw_relation_items
            if isinstance(item, dict)
        )
        followup_prompt = (
            ATOMIC_RELATION_PROBE_FROM_TOPOLOGY_FOLLOWUP_PROMPT
            if evidence_topology
            else self._relation_probe_followup_prompt
        )
        if review_relations:
            review_payload = {
                "cards": [
                    {
                        key: item.get(key)
                        for key in (
                            "local_card_id",
                            "summary",
                            "focus_evidence_refs",
                        )
                    }
                    for item in (card_output.get("cards") or [])
                    if isinstance(item, dict)
                ],
                "relation_candidates": [
                    {
                        key: item.get(key)
                        for key in (
                            "source_card_id",
                            "target_card_id",
                            "relation_kind",
                            "relation_evidence_refs",
                        )
                    }
                    for item in raw_relation_items
                    if isinstance(item, dict)
                ],
                "direct_links": list(
                    (evidence_topology or {}).get("direct_links") or []
                ),
                "open_slots": list(
                    (evidence_topology or {}).get("open_slots") or []
                ),
            }
            review_content = "\n".join(
                [
                    followup_prompt,
                    "以下是待独立审查的数据；relation_candidates 已故意移除 basis，"
                    "不得复用上一轮结论，只能根据本消息前面的新闻原文判断：",
                    json.dumps(
                        review_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ]
            )
            conversation_messages = [
                *base_messages[:2],
                {"role": "user", "content": review_content},
            ]
        else:
            conversation_messages = [
                *base_messages,
                {"role": "assistant", "content": assistant_content},
                {"role": "user", "content": followup_prompt},
            ]
        request = LLMProxyRequest(
            model=model_route.model,
            provider=self._provider,
            messages=conversation_messages,
            temperature=0,
            max_tokens=ATOMIC_CARD_MAX_TOKENS,
            json_schema=(
                ATOMIC_TOPOLOGY_RELATION_PROBE_SCHEMA
                if review_relations
                else ATOMIC_RELATION_PROBE_SCHEMA
            ),
            provider_options=self._provider_options(self._probe_thinking_type),
            metadata={
                "task": "kg_relation_probe",
                "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                "generator_version": ATOMIC_RELATION_PROBE_GENERATOR_VERSION,
                "card_generator_version": ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                "chunk_id": chunk.chunk_id,
                "source_type": str((chunk.payload or {}).get("source_type") or ""),
                "source_id": str((chunk.payload or {}).get("source_id") or ""),
                "card_count": len(cards_by_local_id),
                "relation_count": relation_count,
                "model_route": model_route.tier,
                "thinking_type": self._probe_thinking_type or "provider_default",
                "prompt_profile": self._prompt_profile,
                "prompt_fingerprint": self._prompt_fingerprint,
                "evidence_topology_preplan": bool(evidence_topology),
                "_cache_key_metadata": {
                    "task": "kg_relation_probe",
                    "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
                    "generator_version": ATOMIC_RELATION_PROBE_GENERATOR_VERSION,
                    "model_route": model_route.tier,
                    "thinking_type": self._probe_thinking_type or "provider_default",
                    "prompt_profile": self._prompt_profile,
                    "prompt_fingerprint": self._prompt_fingerprint,
                    "evidence_topology_preplan": bool(evidence_topology),
                },
            },
            use_cache=True,
        )
        with langfuse_observation(
            name="kg.atomic_card.plan_relation_probes",
            as_type="span",
            input={
                "chunk_id": chunk.chunk_id,
                "card_count": len(cards_by_local_id),
                "relation_count": relation_count,
                "continuation_mode": "multi_turn_follow_up",
                "history_message_count": len(conversation_messages),
                "selected_model": model_route.model,
                "model_route": model_route.tier,
                "evidence_topology_preplan": bool(evidence_topology),
                "open_slot_count": len((evidence_topology or {}).get("open_slots") or []),
            },
        ):
            response = await self._llm.generate(request)
            validated, repaired, repair_attempted = await self._probes_from_response(
                chunk=chunk,
                cards_by_local_id=cards_by_local_id,
                request=request,
                response=response,
                review_relations=review_relations,
                allowed_local_relation_pairs=allowed_local_relation_pairs,
            )
            cards = [
                replace(
                    card,
                    relation_probes=list(validated.probes_by_local_id[local_id]),
                )
                for local_id, card in cards_by_local_id.items()
            ]
            langfuse_update_span(
                output={
                    "chunk_id": chunk.chunk_id,
                    "probe_count": sum(
                        len(items) for items in validated.probes_by_local_id.values()
                    ),
                    "cards_without_probes": sum(
                        not items for items in validated.probes_by_local_id.values()
                    ),
                    "repaired": repaired,
                    "repair_attempted": repair_attempted,
                    "issues": list(validated.issues),
                    "accepted_relation_count": (
                        len(validated.accepted_relation_pairs)
                        if validated.accepted_relation_pairs is not None
                        else None
                    ),
                    "usage": _response_usage_diagnostics(response),
                },
                status_message="completed",
            )
            return (
                cards,
                repaired,
                repair_attempted,
                validated.issues,
                _response_usage_diagnostics(response),
                validated.accepted_relation_pairs,
            )

    @staticmethod
    def _provider_options(thinking_type: str | None) -> dict[str, Any]:
        options: dict[str, Any] = {"inject_json_schema_instruction": False}
        if thinking_type:
            options["thinking_type"] = thinking_type
        if thinking_type != "disabled":
            options["reasoning_effort"] = "medium"
        return options

    def _select_model_route(
        self,
        *,
        span_count: int,
        text_chars: int,
    ) -> _AtomicCardModelRoute:
        if self._model_override:
            return _AtomicCardModelRoute(
                model=self._model_override,
                tier="explicit_override",
            )
        if (
            span_count <= self._simple_max_spans
            and text_chars <= self._simple_max_chars
        ):
            return _AtomicCardModelRoute(model=self._simple_model, tier="simple")
        return _AtomicCardModelRoute(model=self._complex_model, tier="complex")

    async def _probes_from_response(
        self,
        *,
        chunk: EvidenceChunk,
        cards_by_local_id: dict[str, AtomicCognitiveCard],
        request: LLMProxyRequest,
        response: Any,
        review_relations: bool,
        allowed_local_relation_pairs: frozenset[tuple[str, str]],
    ) -> tuple[_ValidatedProbeResponse, bool, bool]:
        if _response_cannot_be_safely_repaired(response):
            raise RuntimeError(
                "Relation Probe 输出在 Prefix Completion 后仍未完成，"
                f"chunk_id={chunk.chunk_id}; 禁止重新执行完整业务请求"
            )
        try:
            return (
                self._validate_probe_response(
                    cards_by_local_id,
                    response.structured_output,
                    review_relations=review_relations,
                    allowed_local_relation_pairs=allowed_local_relation_pairs,
                ),
                False,
                False,
            )
        except Exception as exc:
            issues = [str(exc)]

        repaired = await self._llm.repair_with_feedback(
            request,
            response,
            issues,
            instruction=(
                "上一轮 Relation Probe 输出未通过契约校验。"
                + (
                    "只修复 accepted_relation_pairs、probe_plans 的 JSON 结构、"
                    if review_relations
                    else "只修复 probe_plans 的 JSON 结构、"
                )
                +
                "Card 覆盖和 Probe 字段；"
                "必须按输入顺序为每个 local_card_id 恰好输出一项，"
                "不得修改 Card，不得新增事实或同 Chunk Relation。"
            ),
            retry_reason="atomic_relation_probe_validation_invalid",
        )
        try:
            return (
                self._validate_probe_response(
                    cards_by_local_id,
                    repaired.structured_output,
                    review_relations=review_relations,
                    allowed_local_relation_pairs=allowed_local_relation_pairs,
                ),
                True,
                True,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Relation Probe 修复后仍未通过校验: chunk_id={chunk.chunk_id}; "
                f"first_issues={issues}; repair_issue={exc}"
            ) from exc

    @staticmethod
    def _validate_probe_response(
        cards_by_local_id: dict[str, AtomicCognitiveCard],
        data: Any,
        *,
        review_relations: bool = False,
        allowed_local_relation_pairs: frozenset[tuple[str, str]] = frozenset(),
    ) -> _ValidatedProbeResponse:
        if not isinstance(data, dict):
            raise ValueError(
                f"Relation Probe 顶层输出必须是 JSON object，实际为 {type(data).__name__}"
            )
        expected_fields = (
            {"accepted_relation_pairs", "probe_plans"}
            if review_relations
            else {"probe_plans"}
        )
        if set(data) != expected_fields:
            raise ValueError(
                "Relation Probe 顶层字段不符合契约: "
                f"missing={sorted(expected_fields.difference(data))}, "
                f"extra={sorted(set(data).difference(expected_fields))}"
            )
        raw_plans = data.get("probe_plans")
        if not isinstance(raw_plans, list):
            raise ValueError("probe_plans 必须是数组")

        expected_ids = list(cards_by_local_id)
        actual_ids: list[str] = []
        probes_by_local_id: dict[str, list[RelationProbe]] = {}
        for index, item in enumerate(raw_plans, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"probe_plan[{index}] 必须是对象")
            if set(item) != {"local_card_id", "relation_probes"}:
                raise ValueError(f"probe_plan[{index}] 字段不符合契约")
            local_card_id = str(item.get("local_card_id") or "").strip()
            actual_ids.append(local_card_id)
            probes_by_local_id[local_card_id] = relation_probes_from_llm_items(
                item.get("relation_probes")
            )
        if actual_ids != expected_ids:
            raise ValueError(
                "probe_plans 必须按输入顺序完整覆盖 Cards: "
                f"expected={expected_ids}, actual={actual_ids}"
            )
        accepted_relation_pairs: frozenset[tuple[str, str]] | None = None
        if review_relations:
            raw_pairs = data.get("accepted_relation_pairs")
            if not isinstance(raw_pairs, list):
                raise ValueError("accepted_relation_pairs 必须是数组")
            accepted_global_pairs: set[tuple[str, str]] = set()
            seen_local_pairs: set[tuple[str, str]] = set()
            for index, item in enumerate(raw_pairs, start=1):
                if not isinstance(item, dict) or set(item) != {
                    "source_card_id",
                    "target_card_id",
                }:
                    raise ValueError(
                        f"accepted_relation_pair[{index}] 字段不符合契约"
                    )
                source_local_id = str(item.get("source_card_id") or "").strip()
                target_local_id = str(item.get("target_card_id") or "").strip()
                local_pair = tuple(sorted((source_local_id, target_local_id)))
                if (
                    source_local_id not in cards_by_local_id
                    or target_local_id not in cards_by_local_id
                    or source_local_id == target_local_id
                ):
                    logger.warning(
                        "丢弃 Relation 审查额外输出的非法 Card pair: index=%s pair=%s",
                        index,
                        local_pair,
                    )
                    continue
                if local_pair not in allowed_local_relation_pairs:
                    logger.warning(
                        "丢弃 Relation 审查新增的非候选 pair: index=%s pair=%s",
                        index,
                        local_pair,
                    )
                    continue
                if local_pair in seen_local_pairs:
                    continue
                seen_local_pairs.add(local_pair)
                accepted_global_pairs.add(
                    tuple(
                        sorted(
                            (
                                cards_by_local_id[source_local_id].cognitive_card_id,
                                cards_by_local_id[target_local_id].cognitive_card_id,
                            )
                        )
                    )
                )
            accepted_relation_pairs = frozenset(accepted_global_pairs)
        return _ValidatedProbeResponse(
            probes_by_local_id=probes_by_local_id,
            accepted_relation_pairs=accepted_relation_pairs,
        )

    async def _claim_prefix_warmup_lock(self, prefix_scope: str) -> Any | None:
        if settings.KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS <= 0:
            return None
        if await self._prefix_recently_warmed(prefix_scope):
            return None
        lock = self._prefix_warmup_lock(prefix_scope)
        acquired = await self._try_acquire_prefix_warmup_lock(lock)
        if acquired is None:
            return None
        if not acquired:
            return await self._wait_for_prefix_warmup(prefix_scope)
        if await self._prefix_recently_warmed(prefix_scope):
            await self._release_prefix_warmup_lock(lock)
            return None
        return lock

    def _prefix_warmup_lock(self, prefix_scope: str) -> Any:
        return self._redis_client().lock(
            f"{ATOMIC_CARD_PREFIX_WARM_LOCK_KEY}:{prefix_scope}",
            timeout=max(1, settings.KG_COGNITIVE_CARD_PREFIX_WARM_LOCK_TIMEOUT_SECONDS),
            blocking_timeout=0,
            sleep=ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS,
        )

    async def _try_acquire_prefix_warmup_lock(self, lock: Any) -> bool | None:
        try:
            return bool(await asyncio.to_thread(lambda: lock.acquire(blocking=False)))
        except Exception as exc:
            logger.warning("原子 Card 前缀预热锁不可用: %s", exc)
            return None

    async def _wait_for_prefix_warmup(self, prefix_scope: str) -> Any | None:
        deadline = time.monotonic() + max(
            1,
            settings.KG_COGNITIVE_CARD_PREFIX_WARM_BLOCKING_TIMEOUT_SECONDS,
        )
        while time.monotonic() < deadline:
            if await self._prefix_recently_warmed(prefix_scope):
                return None
            lock = self._prefix_warmup_lock(prefix_scope)
            acquired = await self._try_acquire_prefix_warmup_lock(lock)
            if acquired is None:
                return None
            if acquired:
                if await self._prefix_recently_warmed(prefix_scope):
                    await self._release_prefix_warmup_lock(lock)
                    return None
                return lock
            await asyncio.sleep(ATOMIC_CARD_PREFIX_WARM_POLL_SECONDS)
        return None

    async def _prefix_recently_warmed(self, prefix_scope: str) -> bool:
        try:
            return bool(
                await asyncio.to_thread(
                    self._redis_client().exists,
                    f"{ATOMIC_CARD_PREFIX_WARM_MARK_KEY}:{prefix_scope}",
                )
            )
        except Exception:
            return False

    async def _mark_prefix_warmed(
        self,
        prefix_scope: str,
        *,
        settle: bool = False,
    ) -> None:
        try:
            if settle and settings.KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS > 0:
                await asyncio.sleep(settings.KG_COGNITIVE_CARD_PREFIX_WARM_SETTLE_SECONDS)
            await asyncio.to_thread(
                self._redis_client().setex,
                f"{ATOMIC_CARD_PREFIX_WARM_MARK_KEY}:{prefix_scope}",
                max(1, settings.KG_COGNITIVE_CARD_PREFIX_WARM_WINDOW_SECONDS),
                str(int(time.time())),
            )
        except Exception:
            return

    def _prefix_warm_scope(self, model: str) -> str:
        raw_scope = ":".join(
            (
                self._provider or "auto",
                model,
                self._card_thinking_type or "provider_default",
                self._prompt_fingerprint,
                "topology" if self._evidence_topology_preplan else "direct",
            )
        )
        return hashlib.sha256(raw_scope.encode("utf-8")).hexdigest()[:16]

    async def _release_prefix_warmup_lock(self, lock: Any) -> None:
        try:
            await asyncio.to_thread(lock.release)
        except Exception:
            return

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = redis.from_url(REDIS_URL, decode_responses=True)
        return self._redis


class AtomicCognitiveCardStageService:
    """完成 Card 提取、PG manifest 替换和 Milvus 发布后停止。"""

    def __init__(
        self,
        *,
        repository: KnowledgeRepository,
        semantic_retriever: MilvusSemanticHybridRetriever,
        extractor: AtomicCognitiveCardExtractor | None = None,
        relation_writer: Any | None = None,
    ) -> None:
        self._repository = repository
        self._semantic_retriever = semantic_retriever
        self._relation_writer = relation_writer
        self._extractor = extractor or AtomicCognitiveCardExtractor(
            concurrency=max(
                1,
                min(4, int(getattr(settings, "CLAUDE_PROXY_MAX_CONCURRENCY", 2) or 2)),
            )
        )

    async def refresh(
        self,
        *,
        adapter_name: str,
        target: str,
        kg_version: str,
        changed_chunks: list[EvidenceChunk],
        persist: bool = True,
    ) -> AtomicCardStageResult:
        with langfuse_observation(
            name="kg.atomic_card.stage",
            as_type="chain",
            input={
                "adapter_name": adapter_name,
                "target": target,
                "changed_chunks": len(changed_chunks),
                "persist": persist,
            },
            metadata={"schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION},
        ):
            inactive_cleanup = {
                "card_ids": [],
                "deleted_documents": 0,
                "deleted_manifests": 0,
                "relation_result": {},
            }
            if persist:
                inactive_cleanup = await self._cleanup_inactive_evidence_cards(
                    adapter_name=adapter_name,
                    target=target,
                )
            if not changed_chunks:
                diagnostics = self._diagnostics(
                    [],
                    {
                        "deleted_cards": inactive_cleanup["deleted_manifests"],
                        "deleted_card_ids": inactive_cleanup["card_ids"],
                    },
                    0,
                    inactive_cleanup["deleted_documents"],
                    inactive_cleanup["relation_result"],
                    {},
                    assignment_executed=False,
                )
                langfuse_update_span(output=diagnostics, status_message="cards_ready")
                return AtomicCardStageResult(status="cards_ready", cards=[], diagnostics=diagnostics)

            extraction_results = await self._extractor.extract_with_diagnostics(changed_chunks)
            cards = [card for result in extraction_results for card in result.cards]
            intra_chunk_relations = [
                relation
                for result in extraction_results
                for relation in result.relations
            ]
            intra_chunk_sync_decisions = _intra_chunk_relation_sync_decisions(
                extraction_results
            )
            persistence: dict[str, Any] = {}
            documents_written = 0
            stale_documents_deleted = int(inactive_cleanup["deleted_documents"])
            stale_relation_result: dict[str, Any] = dict(inactive_cleanup["relation_result"])
            intra_chunk_relation_result: dict[str, Any] = {}

            if persist:
                evidence_ids = list(dict.fromkeys(chunk.evidence_id for chunk in changed_chunks))
                with langfuse_observation(
                    name="kg.atomic_card.pg_replace",
                    as_type="span",
                    input={"evidence_count": len(evidence_ids), "card_count": len(cards)},
                ):
                    persistence = self._repository.replace_atomic_cognitive_cards_for_evidence(
                        adapter_name,
                        evidence_ids=evidence_ids,
                        cards=cards,
                    )
                    langfuse_update_span(output=persistence, status_message="completed")

                chunks_by_id = {chunk.chunk_id: chunk for chunk in changed_chunks}
                summary_documents = [atomic_card_summary_document(card) for card in cards]
                focus_documents = [
                    atomic_card_focus_document(
                        card,
                        chunk_content=chunks_by_id[card.primary_chunk_id].content,
                    )
                    for card in cards
                ]
                documents = [*summary_documents, *focus_documents]
                with langfuse_observation(
                    name="kg.atomic_card.milvus_upsert",
                    as_type="span",
                    input={
                        "summary_documents": len(summary_documents),
                        "focus_evidence_documents": len(focus_documents),
                    },
                ):
                    documents_written = await self._semantic_retriever.upsert_semantic_documents(
                        adapter_name=adapter_name,
                        target=target,
                        documents=documents,
                        kg_version=kg_version,
                    )
                    langfuse_update_span(
                        output={"documents_written": documents_written},
                        status_message="completed",
                    )
                current_ids = {card.cognitive_card_id for card in cards}
                stale_ids = [
                    card_id
                    for card_id in persistence.get("deleted_card_ids") or []
                    if card_id and card_id not in current_ids
                ]
                with langfuse_observation(
                    name="kg.atomic_card.delete_stale",
                    as_type="span",
                    input={"target_ids": stale_ids},
                ):
                    deleted_summary = await self._semantic_retriever.delete_documents_by_role(
                        collection_role="cognitive_card",
                        adapter_name=adapter_name,
                        target=target,
                        target_ids=stale_ids,
                    )
                    deleted_focus = await self._semantic_retriever.delete_documents_by_role(
                        collection_role="cognitive_card_focus",
                        adapter_name=adapter_name,
                        target=target,
                        target_ids=stale_ids,
                    )
                    stale_documents_deleted = deleted_summary + deleted_focus
                    langfuse_update_span(
                        output={
                            "summary_deleted": deleted_summary,
                            "focus_evidence_deleted": deleted_focus,
                            "deleted": stale_documents_deleted,
                        },
                        status_message="completed",
                    )
                if stale_ids and self._relation_writer is not None:
                    with langfuse_observation(
                        name="kg.atomic_card.invalidate_relations",
                        as_type="span",
                        input={"stale_card_ids": stale_ids},
                    ):
                        replacement_relation_result = await self._relation_writer.invalidate_cards(
                            stale_ids,
                            adapter_name=adapter_name,
                            target=target,
                        )
                        stale_relation_result = _merge_relation_write_results(
                            stale_relation_result,
                            replacement_relation_result,
                        )
                        langfuse_update_span(
                            output=stale_relation_result,
                            status_message="completed",
                        )
                if intra_chunk_sync_decisions:
                    if self._relation_writer is None:
                        raise RuntimeError("同 Chunk Relation 已生成，但 CardRelationWriteService 未配置")
                    with langfuse_observation(
                        name="kg.atomic_card.persist_intra_chunk_relations",
                        as_type="span",
                        input={
                            "positive_relations": len(intra_chunk_relations),
                            "synchronized_pairs": len(intra_chunk_sync_decisions),
                        },
                    ):
                        intra_chunk_relation_result = (
                            await self._relation_writer.persist_verified_decisions(
                                intra_chunk_sync_decisions,
                                adapter_name=adapter_name,
                                target=target,
                                pipeline_version=ATOMIC_CARD_INTRA_CHUNK_PIPELINE_VERSION,
                                model_name=resolve_kg_llm_model("kg_cognitive_card"),
                                prompt_version=ATOMIC_COGNITIVE_CARD_GENERATOR_VERSION,
                            )
                        )
                        langfuse_update_span(
                            output=intra_chunk_relation_result,
                            status_message="completed",
                        )

            diagnostics = self._diagnostics(
                extraction_results,
                persistence,
                documents_written,
                stale_documents_deleted,
                stale_relation_result,
                intra_chunk_relation_result,
                assignment_executed=False,
            )
            langfuse_update_span(output=diagnostics, status_message="cards_ready")
            return AtomicCardStageResult(status="cards_ready", cards=cards, diagnostics=diagnostics)

    async def _cleanup_inactive_evidence_cards(
        self,
        *,
        adapter_name: str,
        target: str,
    ) -> dict[str, Any]:
        """清理已失效 Evidence 留下的 Card、语义视图和正式 Edge。"""

        card_ids = self._repository.list_atomic_cognitive_card_ids_for_inactive_evidence(
            adapter_name
        )
        if not card_ids:
            return {
                "card_ids": [],
                "deleted_documents": 0,
                "deleted_manifests": 0,
                "relation_result": {},
            }
        if self._relation_writer is None:
            raise RuntimeError("清理失效 Evidence Card 需要正式 Relation Writer")
        with langfuse_observation(
            name="kg.atomic_card.cleanup_inactive_evidence",
            as_type="span",
            input={"card_ids": card_ids},
        ):
            deleted_summary = await self._semantic_retriever.delete_documents_by_role(
                collection_role="cognitive_card",
                adapter_name=adapter_name,
                target=target,
                target_ids=card_ids,
            )
            deleted_focus = await self._semantic_retriever.delete_documents_by_role(
                collection_role="cognitive_card_focus",
                adapter_name=adapter_name,
                target=target,
                target_ids=card_ids,
            )
            relation_result = await self._relation_writer.invalidate_cards(
                card_ids,
                adapter_name=adapter_name,
                target=target,
            )
            deleted_manifests = self._repository.delete_atomic_cognitive_cards_by_ids(
                adapter_name,
                cognitive_card_ids=card_ids,
            )
            result = {
                "card_ids": card_ids,
                "deleted_documents": deleted_summary + deleted_focus,
                "deleted_manifests": deleted_manifests,
                "relation_result": relation_result,
            }
            langfuse_update_span(output=result, status_message="completed")
            return result

    @staticmethod
    def _diagnostics(
        extraction_results: list[AtomicCardExtractionResult],
        persistence: dict[str, Any],
        documents_written: int,
        stale_documents_deleted: int,
        stale_relation_result: dict[str, Any],
        intra_chunk_relation_result: dict[str, Any],
        *,
        assignment_executed: bool,
    ) -> dict[str, Any]:
        card_count = sum(len(result.cards) for result in extraction_results)
        intra_chunk_relation_count = sum(
            len(result.relations) for result in extraction_results
        )
        relation_probe_count = sum(
            len(card.relation_probes)
            for result in extraction_results
            for card in result.cards
        )
        intra_chunk_observed = sum(
            relation.decision_class == "observed"
            for result in extraction_results
            for relation in result.relations
        )
        intra_chunk_inferred = sum(
            relation.decision_class == "inferred"
            for result in extraction_results
            for relation in result.relations
        )
        chunk_count = len(extraction_results)
        return {
            "status": "cards_ready",
            "schema_version": ATOMIC_COGNITIVE_CARD_SCHEMA_VERSION,
            "input_chunks": chunk_count,
            "successful_chunks": chunk_count,
            "zero_card_chunks": sum(1 for result in extraction_results if not result.cards),
            "repaired_chunks": sum(1 for result in extraction_results if result.repaired),
            "repair_attempted_chunks": sum(
                1 for result in extraction_results if result.repair_attempted
            ),
            "discarded_cards": sum(
                result.discarded_card_count for result in extraction_results
            ),
            "discarded_relations": sum(
                result.discarded_relation_count for result in extraction_results
            ),
            "validation_issues": [
                {"chunk_id": result.chunk_id, "issues": list(result.validation_issues)}
                for result in extraction_results
                if result.validation_issues
            ],
            "zero_card_reasons": [
                {"chunk_id": result.chunk_id, "reason": result.skip_reason}
                for result in extraction_results
                if not result.cards and result.skip_reason
            ],
            "cards": card_count,
            "relation_probes": relation_probe_count,
            "cards_without_relation_probes": sum(
                not card.relation_probes
                for result in extraction_results
                for card in result.cards
            ),
            "average_cards_per_chunk": round(card_count / chunk_count, 3) if chunk_count else 0.0,
            "pg_inserted_cards": int(persistence.get("inserted_cards") or 0),
            "pg_deleted_cards": int(persistence.get("deleted_cards") or 0),
            "milvus_documents_written": documents_written,
            "milvus_stale_documents_deleted": stale_documents_deleted,
            "invalidated_relation_edge_ids": list(
                stale_relation_result.get("changed_edge_ids") or []
            ),
            "intra_chunk_relations": intra_chunk_relation_count,
            "intra_chunk_observed": intra_chunk_observed,
            "intra_chunk_inferred": intra_chunk_inferred,
            "intra_chunk_changed_edge_ids": list(
                intra_chunk_relation_result.get("changed_edge_ids") or []
            ),
            "intra_chunk_graph_event_ids": list(
                intra_chunk_relation_result.get("graph_event_ids") or []
            ),
            "assignment_executed": assignment_executed,
        }


def _merge_relation_write_results(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    result = {**left, **right}
    for key in (
        "touched_edge_ids",
        "changed_edge_ids",
        "milvus_upserted_edge_ids",
        "milvus_deleted_edge_ids",
        "graph_event_ids",
        "affected_card_ids",
    ):
        result[key] = list(
            dict.fromkeys([*(left.get(key) or []), *(right.get(key) or [])])
        )
    return result


def _intra_chunk_relation_sync_decisions(
    extraction_results: list[AtomicCardExtractionResult],
) -> list[VerifiedRelationDecision]:
    """把每个 Chunk 的正关系补成完整 pair 状态，确保重跑可撤销旧 Edge。"""

    decisions: list[VerifiedRelationDecision] = []
    for result in extraction_results:
        positive_by_pair = {
            tuple(sorted((item.source_card_id, item.target_card_id))): item
            for item in result.relations
        }
        for left, right in combinations(result.cards, 2):
            pair = tuple(sorted((left.cognitive_card_id, right.cognitive_card_id)))
            positive = positive_by_pair.get(pair)
            if positive is not None:
                decisions.append(positive)
                continue
            decisions.append(
                VerifiedRelationDecision(
                    source_card_id=left.cognitive_card_id,
                    target_card_id=right.cognitive_card_id,
                    decision_class="no_relation",
                    relation_kind="",
                    relation_type="",
                    direction="",
                    basis="",
                    source_evidence_refs=[],
                    target_evidence_refs=[],
                    inference_mechanism="",
                    confidence=0.0,
                )
            )
    return decisions
