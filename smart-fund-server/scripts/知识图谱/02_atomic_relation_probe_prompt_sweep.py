#!/usr/bin/env python3
"""穷举 Relation / Relation Probe 提示词因素并回放固定真实新闻样本。"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.application.services import atomic_cognitive_card_service as atomic_service  # noqa: E402
from src.domain.knowledge.atomic_cognitive_card import (  # noqa: E402
    StableSpanSegmenter,
    render_atomic_card_prompt_input,
)
from src.domain.knowledge.chunking import build_evidence_chunks  # noqa: E402
from src.domain.knowledge.schemas import EvidenceChunk  # noqa: E402
from src.domain.knowledge_adapters.financial.source_projection import (  # noqa: E402
    project_ft_news_row,
)
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


DEFAULT_NEWS_IDS = [
    109570,
    109535,
    109537,
    109286,
    109539,
    109540,
    109541,
    109290,
    109552,
]

RELATION_FACTORS = {
    "taxonomy": """
类型边界核对：
- confirmation 只连接不同声明者或不同来源对同一事实命题的独立支持；同源标题与正文复述、总述与明细、结论与支撑数据都不是 confirmation。
- contradiction 要求两个 Card 对同一主体、对象、谓词、时间和口径给出不可同时成立的结论；请求与裁决、指控与对另一命题的否认都不是 contradiction。
- common_driver 要求原文明确指出同一个第三事实分别驱动两个端点；板块与成分、整体与局部、总量与明细不属于 common_driver。
- constraint 要求 source 明确限制 target 的发生、执行、规模或概率；总量与分项、范围与成员不属于 constraint。
""",
    "evidence": """
连接证据核对：每条 Relation 的原文证据必须直接断言“这两个 Card 之间存在该类型连接”，而不只是分别证明两个端点。消息面、同段出现、先后报道、共同背景和模型补出的中间机制均不构成连接。causal_influence 必须由原文把 source 明确表达为 target 的原因、触发、依据或影响；temporal_progression 必须由后项明确承接、回应、执行、改变或结束前项。
""",
    "utility": """
图增量核对：Relation 必须表达 Cards 单独阅读时看不到的连接事实。若 basis 的实际含义只是“一部分、一个例子、一个组成项、共同发生、同属一个事件或同方向波动”，则不建边。默认不输出 Relation；无法确定类型或方向时留空，不能选择最接近的类型凑数。
""",
}

PROBE_FACTORS = {
    "scarcity": """
Probe 是稀缺的跨文档路由，不是每张 Card 的固定附属物。只有原文明确留下尚未闭合的原因、结果、执行、回应、争议、预测验证或独立确认缺口，并且补齐后会显著改变当前事实的解释或可信度，才生成 Probe；否则必须输出空数组。每张 Card 最多两条。
""",
    "target_event": """
query 使用可直接参与语义检索的“候选事件描述”，不要写成泛化问句。描述必须包含当前事实锚点和要寻找的另一端事件类型，但不能预设尚无证据的具体结果、主体或机制。禁止“是否有后续、后续走势、公司回应、具体原因或公告、有什么影响”这类没有可识别目标事件的模板问题。
""",
    "deduplicate": """
并列明细去重：当多个 Card 只是同一整体事件的地区、公司、指标或组成项时，只允许在能够代表整体事件的 Card 上生成共享 Probe；各明细 Card 保持空数组，除非某一明细在原文中具有独立争议或独立未决动作。confirmation 只用于来源存疑、存在争议或独立佐证会实质改变可信度的主张，不为普通行情数字逐项寻求重复报道。
""",
}


REFINED_RELATION_BASE = RELATION_FACTORS["utility"]

REFINED_RELATION_FACTORS = {
    "explicit_connector": """
从原文的连接表达出发，不从 Card 的主题相似度出发。原文明确用原因、影响、触发、执行、回应、进展或限制语义连接两个不同事实时，应输出该连接；只有时间词、同段出现或模型可以补全的机制时不输出。
""",
    "endpoint_entailment": """
端点对齐核对：basis 必须精确使用 source Card 和 target Card 各自的事实谓词，不得把其中一端替换为上位概括、组成项、第三个未建卡事实或另一个命题。如果原文只支持“整体包含明细”或“同一句分别陈述两端”，不建边。
""",
    "type_guard": """
类型只做精确匹配：confirmation 要求独立表述者对同一命题给出同向证据；contradiction 要求同一命题的两个结论不可同时成立；temporal_progression 要求同一事件的状态确实发生变化；common_driver 要求原文明说同一第三事实分别驱动两端；constraint 要求一端直接限制另一端。类型不精确时不用近似类型代替。
""",
}


REFINED_PROBE_BASE = "\n".join(
    [PROBE_FACTORS["scarcity"].strip(), PROBE_FACTORS["deduplicate"].strip()]
)

REFINED_PROBE_FACTORS = {
    "as_of_time": """
只为当前检索库中截至 published_at 可能已经发生的历史事件生成 Probe。不搜索 published_at 之后的价格、处罚、投产、重启、调查结论或其他未来验证结果；即使当前 Card 含预测或计划，也只搜索已经发生的前置原因、现有约束、独立支持或反证。
""",
    "retrieval_form": """
query 是一条可直接用于语义检索的“候选事件描述”，不是向数据库提问。它应保留当前 Card 的关键主体或对象，并描述要搜索的另一端动作、状态或指标类型。不使用“是否、什么、多少、最新进展、具体细节”等问句或模板词。
""",
    "bridge_recall": """
对原因未在当前 Chunk 中解释的显著变化、决策、异常行情、中断或风险信号，允许为代表整体事件的 Card 保留一条 upstream Probe，用于寻找可能的已发生驱动事件。只描述候选事件的可观测类型，不预设某个具体机制、主体或结论已经成立。
""",
}


FINAL_RELATION_BASE = "\n".join(
    [
        RELATION_FACTORS["utility"].strip(),
        REFINED_RELATION_FACTORS["explicit_connector"].strip(),
        REFINED_RELATION_FACTORS["endpoint_entailment"].strip(),
    ]
)

FINAL_RELATION_FACTORS = {
    "proposition_identity": """
命题同一性核对：confirmation 只用于不同来源或声明者对同一主体、谓词、对象、时间与口径的独立支持；同一声明者的重复说明不是独立确认。contradiction 只用于上述同一命题的结论不可同时成立；“疑似关联”与“未发现资金输送”这类谓词不同的陈述不构成确认或矛盾。
""",
    "no_whole_part": """
不连接整体与其表现、概括与具体化、总量与分项、计划与其列举项，也不连接事实键相同但详细度不同的重复 Card。这些是 Card 边界问题，不是 Relation。
""",
    "minimal_path": """
构建最短的证据链：当 A 经过已存在的 Card B 再影响 C，且原文没有另外直接连接 A 与 C 时，只输出 A→B 和 B→C，不输出传递快捷边 A→C。对集合性结论，只有原文分别对每个 pair 建立连接时才逐对输出。
""",
}


FINAL_PROBE_BASE = """
Probe 是搜索其他 Chunk 历史 Card 的跨文档路由，只在找到另一端后能与当前 Card 建立直接关系、并显著补充当前事实的解释或可信度时生成。纯观测到显著变化但当前 Chunk 没有原因时，可在代表整体事件的 Card 上保留一条 upstream Probe；不预设具体原因已经成立。普通事实、已闭合的解释和独立价值很低的明细保持空数组。当多个 Card 只是同一整体事件的公司、地区、指标或组成项时，只在代表整体事件的 Card 上生成共享 Probe。每张 Card 最多两条，不按 role 凑数。
"""

FINAL_PROBE_FACTORS = {
    "as_of_time": REFINED_PROBE_FACTORS["as_of_time"],
    "retrieval_form": REFINED_PROBE_FACTORS["retrieval_form"],
    "exclude_current": """
输出前将 query 与上一轮所有 Cards 和 Relations 逐项对照。如果 query 要找的候选事件已经出现在任一 Card 的 summary 或 Relation 中，即使更换说法、要求详细信息或换一个时间粒度，也必须删除该 Probe。Probe 只能指向当前 Chunk 未给出的另一事件端点。
""",
    "corroboration_exception": """
confirmation 和 contradiction 的另一端可以与当前 Card 讨论同一命题，但必须是当前 Chunk 中尚未出现的独立声明者、监管结论、审计结果或可验证观测。这是“排除当前 Chunk”的唯一例外；只换来源转述同一句话仍然删除。
""",
}


STABILITY_RELATION_FACTORS = {
    "sparse_minimum": """
Relation 是稀疏的高置信图边，不是对文章的完整解释。只保留删除后会丢失一条原文直接连接的最小边集；不为覆盖每个分句、列举项、表现或论证步骤而建边。候选边若只是重复端点信息、传递连接、整体连接或类型近似，必须删除。
""",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--news-id", action="append", type=int, default=[])
    parser.add_argument("--target", choices=["prod", "test"], default="prod")
    parser.add_argument("--model", default="deepseek-v4-pro")
    parser.add_argument("--provider", default="")
    parser.add_argument(
        "--round",
        choices=["coarse", "refined", "final", "stability"],
        default="coarse",
    )
    parser.add_argument("--stage", choices=["both", "relation", "probe"], default="both")
    parser.add_argument(
        "--relation-input",
        default="",
        help="Probe-only 阶段读取的 Relation 阶段 JSON 结果",
    )
    parser.add_argument(
        "--relation-variant",
        default="",
        help="Probe-only 阶段固定使用的 Relation 变体，默认使用自动评分第一名",
    )
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--output", default="")
    return parser.parse_args()


def _prompt_parts() -> tuple[str, str, str]:
    stage = atomic_service._ATOMIC_CARD_STAGE_SYSTEM_PROMPT
    before_relation, relation_and_json = stage.split("\nRelation：\n", 1)
    relation_body, json_body = relation_and_json.split("\nJSON 输出：\n", 1)
    return before_relation, relation_body, json_body


def _relation_prompt(
    enabled: tuple[str, ...],
    *,
    factors: dict[str, str],
    base: str = "",
) -> str:
    before_relation, relation_body, json_body = _prompt_parts()
    additions = "\n".join(
        part
        for part in [base.strip(), *(factors[name].strip() for name in enabled)]
        if part
    )
    stage = (
        f"{before_relation}\nRelation：\n{relation_body.strip()}"
        f"\n{additions}\nJSON 输出：\n{json_body.strip()}"
    )
    return "\n\n".join(
        [stage.strip(), atomic_service._ATOMIC_RELATION_PROBE_SYSTEM_SECTION.strip()]
    )


def _probe_followup(
    enabled: tuple[str, ...],
    *,
    factors: dict[str, str],
    base: str = "",
) -> str:
    additions = "\n".join(
        part
        for part in [base.strip(), *(factors[name].strip() for name in enabled)]
        if part
    )
    return (
        f"{atomic_service.ATOMIC_RELATION_PROBE_FOLLOWUP_PROMPT}\n\n"
        f"本次实验的附加约束：\n{additions}"
    ).strip()


def _factor_variants(prefix: str, factors: dict[str, str]) -> list[dict[str, Any]]:
    names = tuple(factors)
    variants = []
    for bits in product((0, 1), repeat=len(names)):
        enabled = tuple(name for name, bit in zip(names, bits) if bit)
        variants.append(
            {
                "name": f"{prefix}_{''.join(str(bit) for bit in bits)}",
                "enabled": enabled,
            }
        )
    return variants


def _news_row(row: News) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "summary": row.summary,
        "source": row.source,
        "source_name": row.source_name,
        "source_reliability": row.source_reliability,
        "category": row.category,
        "url": row.url,
        "tags": row.tags,
        "related_stocks": row.related_stocks,
        "published_at": row.published_at,
        "fingerprint": row.fingerprint,
        "created_at": row.created_at,
    }


def load_chunks(news_ids: list[int], target: str, run_id: str) -> list[dict[str, Any]]:
    with get_session(target) as session:
        rows = list(session.scalars(select(News).where(News.id.in_(news_ids))).all())
    by_id = {int(row.id): row for row in rows}
    output: list[dict[str, Any]] = []
    segmenter = StableSpanSegmenter()
    for news_id in news_ids:
        row = by_id.get(news_id)
        if row is None:
            continue
        record = project_ft_news_row(_news_row(row))
        if record is None:
            continue
        chunks = build_evidence_chunks(
            adapter_name="financial",
            evidence_id=f"kg_ev:financial:prompt_sweep:ft_news:{news_id}:{run_id}",
            content=record["raw_text"],
            payload={
                **record["payload"],
                "source_type": record["source_type"],
                "source_id": record["source_id"],
            },
        )
        for chunk in chunks:
            blocks = segmenter.segment_blocks(chunk.content)
            output.append(
                {
                    "news_id": news_id,
                    "title": row.title,
                    "chunk": chunk,
                    "prompt_input": render_atomic_card_prompt_input(
                        source_published_at=(chunk.payload or {}).get("published_at") or "",
                        source_title=(chunk.payload or {}).get("title") or "",
                        sentence_blocks=blocks,
                    ),
                }
            )
    return output


async def _call(
    *,
    semaphore: asyncio.Semaphore,
    model: str,
    provider: str | None,
    messages: list[dict[str, str]],
    schema: dict[str, Any],
    task: str,
    variant: str,
    news_id: int,
) -> dict[str, Any]:
    request = LLMProxyRequest(
        model=model,
        provider=provider,
        messages=messages,
        temperature=0,
        max_tokens=atomic_service.ATOMIC_CARD_MAX_TOKENS,
        json_schema=schema,
        provider_options={
            "thinking_type": "disabled",
            "inject_json_schema_instruction": False,
        },
        metadata={
            "task": task,
            "source_type": "news_articles",
            "source_id": f"ft_news:{news_id}",
            "prompt_variant": variant,
            "_cache_key_metadata": {
                "task": task,
                "prompt_variant": variant,
            },
        },
        use_cache=False,
    )
    async with semaphore:
        response = await get_llm_gateway_service().generate(request)
    return {
        "structured_output": response.structured_output,
        "text": response.text,
        "usage": response.usage,
        "duration_ms": response.duration_ms,
    }


def _relation_score(news_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["news_id"]: item for item in news_results}
    score = 0.0
    notes: list[str] = []
    negative_caps = {109535: 0, 109537: 0, 109286: 0, 109541: 0}
    for news_id, cap in negative_caps.items():
        count = len((by_id.get(news_id) or {}).get("relations") or [])
        score -= max(0, count - cap) * 2.0
    for news_id in (109539, 109540):
        relations = (by_id.get(news_id) or {}).get("relations") or []
        for relation in relations:
            basis = str(relation.get("basis") or "")
            if relation.get("relation_kind") in {"common_driver", "constraint"} and any(
                marker in basis for marker in ("一部分", "表现之一", "其中之一")
            ):
                score -= 2.0
    relations_109540 = (by_id.get(109540) or {}).get("relations") or []
    score += 3.0 * sum(r.get("relation_kind") == "confirmation" for r in relations_109540)
    score -= 2.0 * sum(
        r.get("relation_kind") in {"causal_influence", "contradiction"}
        for r in relations_109540
    )
    for news_id, minimum, maximum in ((109290, 2, 5), (109552, 12, 35)):
        count = len((by_id.get(news_id) or {}).get("relations") or [])
        score += min(count, minimum) * 1.5
        score -= max(0, minimum - count) * 2.0
        score -= max(0, count - maximum) * 0.5
    zero_cards = [
        news_id for news_id, item in by_id.items() if not (item.get("cards") or [])
    ]
    score -= len(zero_cards) * 10.0
    if zero_cards:
        notes.append(f"zero_cards={zero_cards}")
    relation_kinds = Counter(
        relation.get("relation_kind")
        for item in news_results
        for relation in item.get("relations") or []
    )
    return {
        "score": round(score, 3),
        "relation_count": sum(len(item.get("relations") or []) for item in news_results),
        "relation_kinds": dict(relation_kinds),
        "zero_cards": zero_cards,
        "notes": notes,
    }


def _probe_score(news_results: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {item["news_id"]: item for item in news_results}
    caps = {
        109570: 1,
        109535: 4,
        109537: 1,
        109286: 1,
        109539: 4,
        109540: 3,
        109541: 2,
        109290: 4,
        109552: 8,
    }
    minimums = {109570: 1, 109540: 2, 109290: 2, 109552: 3}
    score = 0.0
    all_queries: list[str] = []
    counts: dict[int, int] = {}
    for news_id, item in by_id.items():
        probes = [
            probe
            for plan in item.get("probe_plans") or []
            for probe in plan.get("relation_probes") or []
        ]
        counts[news_id] = len(probes)
        all_queries.extend(str(probe.get("query") or "") for probe in probes)
        score -= max(0, len(probes) - caps.get(news_id, 4)) * 1.5
        score += min(len(probes), minimums.get(news_id, 0))
        score -= max(0, minimums.get(news_id, 0) - len(probes)) * 2.0
    generic_markers = ("后续走势", "公司回应", "具体原因或公告", "是否有其他来源确认")
    generic_count = sum(any(marker in query for marker in generic_markers) for query in all_queries)
    duplicate_count = sum(value - 1 for value in Counter(all_queries).values() if value > 1)
    score -= generic_count
    score -= duplicate_count
    return {
        "score": round(score, 3),
        "probe_count": len(all_queries),
        "counts_by_news": counts,
        "generic_count": generic_count,
        "duplicate_count": duplicate_count,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    session_id = args.session_id or f"atomic-prompt-sweep-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
    news_ids = list(dict.fromkeys(args.news_id or DEFAULT_NEWS_IDS))
    chunks = load_chunks(news_ids, args.target, session_id)
    semaphore = asyncio.Semaphore(max(1, min(40, args.concurrency)))
    provider = str(args.provider or "").strip() or None

    if args.round == "stability":
        relation_factors = STABILITY_RELATION_FACTORS
        relation_base = ""
        probe_factors = {}
        probe_base = ""
        relation_prefix = "rs"
        probe_prefix = "ps"
    elif args.round == "final":
        relation_factors = FINAL_RELATION_FACTORS
        relation_base = FINAL_RELATION_BASE
        probe_factors = FINAL_PROBE_FACTORS
        probe_base = FINAL_PROBE_BASE
        relation_prefix = "rf"
        probe_prefix = "pf"
    elif args.round == "refined":
        relation_factors = REFINED_RELATION_FACTORS
        relation_base = REFINED_RELATION_BASE
        probe_factors = REFINED_PROBE_FACTORS
        probe_base = REFINED_PROBE_BASE
        relation_prefix = "rr"
        probe_prefix = "pr"
    else:
        relation_factors = RELATION_FACTORS
        relation_base = ""
        probe_factors = PROBE_FACTORS
        probe_base = ""
        relation_prefix = "r"
        probe_prefix = "p"

    relation_variants = _factor_variants(relation_prefix, relation_factors)
    if args.stage == "probe":
        if not args.relation_input:
            raise ValueError("--stage probe 必须提供 --relation-input")
        relation_checkpoint = json.loads(
            Path(args.relation_input).read_text(encoding="utf-8")
        )
        if relation_checkpoint.get("round") != args.round:
            raise ValueError("Relation 检查点与当前 --round 不一致")
        relation_output = relation_checkpoint["relation_results"]
        relation_rank = relation_checkpoint["relation_rank"]
        if args.relation_variant:
            best_relation = next(
                item for item in relation_rank if item["name"] == args.relation_variant
            )
        else:
            best_relation = relation_checkpoint["best_relation_variant"]
    else:
        relation_tasks = []
        for variant in relation_variants:
            system_prompt = _relation_prompt(
                variant["enabled"],
                factors=relation_factors,
                base=relation_base,
            )
            for item in chunks:
                relation_tasks.append(
                    (
                        variant,
                        item,
                        asyncio.create_task(
                            _call(
                                semaphore=semaphore,
                                model=args.model,
                                provider=provider,
                                messages=[
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": item["prompt_input"]},
                                ],
                                schema=atomic_service.ATOMIC_CARD_SCHEMA,
                                task="kg_atomic_relation_prompt_sweep",
                                variant=variant["name"],
                                news_id=item["news_id"],
                            )
                        ),
                    )
                )
        relation_output = {variant["name"]: [] for variant in relation_variants}
        for variant, item, task in relation_tasks:
            response = await task
            structured = response["structured_output"] or {}
            relation_output[variant["name"]].append(
                {
                    "news_id": item["news_id"],
                    "title": item["title"],
                    "cards": structured.get("cards") or [],
                    "relations": structured.get("relations") or [],
                    "skip_reason": structured.get("skip_reason") or "",
                    "assistant_text": response["text"],
                    "usage": response["usage"],
                    "duration_ms": response["duration_ms"],
                }
            )
        relation_rank = sorted(
            [
                {
                    **variant,
                    **_relation_score(relation_output[variant["name"]]),
                }
                for variant in relation_variants
            ],
            key=lambda item: item["score"],
            reverse=True,
        )
        best_relation = relation_rank[0]

    best_relation_results = relation_output[best_relation["name"]]
    best_relation_prompt = _relation_prompt(
        tuple(best_relation["enabled"]),
        factors=relation_factors,
        base=relation_base,
    )

    if args.stage == "relation":
        return {
            "session_id": session_id,
            "round": args.round,
            "stage": args.stage,
            "model": args.model,
            "provider": provider,
            "news_ids": news_ids,
            "relation_rank": relation_rank,
            "probe_rank": [],
            "best_relation_variant": best_relation,
            "best_probe_variant": {},
            "relation_results": relation_output,
            "probe_results": {},
        }

    probe_variants = _factor_variants(probe_prefix, probe_factors)
    probe_tasks = []
    for variant in probe_variants:
        followup = _probe_followup(
            variant["enabled"],
            factors=probe_factors,
            base=probe_base,
        )
        for item in chunks:
            relation_result = next(
                result
                for result in best_relation_results
                if result["news_id"] == item["news_id"]
            )
            if not relation_result["cards"]:
                continue
            probe_tasks.append(
                (
                    variant,
                    item,
                    asyncio.create_task(
                        _call(
                            semaphore=semaphore,
                            model=args.model,
                            provider=provider,
                            messages=[
                                {"role": "system", "content": best_relation_prompt},
                                {"role": "user", "content": item["prompt_input"]},
                                {"role": "assistant", "content": relation_result["assistant_text"]},
                                {"role": "user", "content": followup},
                            ],
                            schema=atomic_service.ATOMIC_RELATION_PROBE_SCHEMA,
                            task="kg_atomic_probe_prompt_sweep",
                            variant=f"{best_relation['name']}+{variant['name']}",
                            news_id=item["news_id"],
                        )
                    ),
                )
            )
    probe_output: dict[str, list[dict[str, Any]]] = {
        variant["name"]: [] for variant in probe_variants
    }
    for variant, item, task in probe_tasks:
        response = await task
        structured = response["structured_output"] or {}
        probe_output[variant["name"]].append(
            {
                "news_id": item["news_id"],
                "title": item["title"],
                "probe_plans": structured.get("probe_plans") or [],
                "usage": response["usage"],
                "duration_ms": response["duration_ms"],
            }
        )
    probe_rank = sorted(
        [
            {**variant, **_probe_score(probe_output[variant["name"]])}
            for variant in probe_variants
        ],
        key=lambda item: item["score"],
        reverse=True,
    )
    return {
        "session_id": session_id,
        "round": args.round,
        "stage": args.stage,
        "model": args.model,
        "provider": provider,
        "news_ids": news_ids,
        "relation_rank": relation_rank,
        "probe_rank": probe_rank,
        "best_relation_variant": best_relation,
        "best_probe_variant": probe_rank[0],
        "relation_results": relation_output,
        "probe_results": probe_output,
    }


async def main() -> None:
    args = parse_args()
    trace_name = "kg.atomic_relation_probe.prompt_sweep"
    with langfuse_propagation_context(
        trace_name=trace_name,
        session_id=args.session_id or None,
        tags=["kg", "atomic-card", "relation", "probe", "prompt-sweep"],
    ):
        with langfuse_observation(name=trace_name, as_type="chain"):
            result = await run(args)
            output_path = Path(args.output) if args.output else Path(
                f"/tmp/02_atomic_relation_probe_prompt_sweep_{result['session_id']}.json"
            )
            output_path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary = {
                "session_id": result["session_id"],
                "best_relation_variant": result["best_relation_variant"],
                "best_probe_variant": result["best_probe_variant"],
                "relation_rank": result["relation_rank"],
                "probe_rank": result["probe_rank"],
                "output": str(output_path),
            }
            langfuse_update_span(output=summary, status_message="completed")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
    langfuse_flush()


if __name__ == "__main__":
    asyncio.run(main())
