"""跨 Chunk Card 召回、Summary 初筛与原文关系核验服务。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from src.application.services.card_relation_write_service import CardRelationWriteService
from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.card_relation import RELATION_KINDS
from src.domain.knowledge.atomic_cognitive_card import (
    CognitiveCardManifest,
    materialize_focus_evidence_context,
)
from src.domain.knowledge.relation_discovery import (
    MergedRelationCandidate,
    PairEvidencePackage,
    RelationRecallHit,
    RelationRoute,
    RouteCandidateHit,
    VerifiedRelationDecision,
    build_relation_routes,
    canonical_card_pair,
)
from src.domain.knowledge.repositories.knowledge_repository import KnowledgeRepository
from src.infrastructure.clients.reranker import RerankerClient
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.knowledge_repository_impl import KnowledgeRepositoryImpl
from src.infrastructure.vector_store.relation_candidate_store import (
    MilvusRelationCandidateStore,
    RelationCardText,
)


logger = logging.getLogger(__name__)

RELATION_DISCOVERY_PIPELINE_VERSION = "relation_discovery_v2_edge_persistence"
RELATION_VERIFICATION_PROMPT_VERSION = "kg_relation_evidence_verify_v3"
_RRF_K = 60

_SCREEN_SYSTEM_PROMPT = """你是知识图谱关系候选初筛器。

你只根据当前原子 Card Summary、候选 Card Summary 和材料发布时间，筛出存在足够具体潜在关系、值得读取完整原文进一步核验的 candidate_id。

规则：
- 这不是普通相关性判断；同主题、同行业、同公司或关键词相似不等于存在关系。
- 除同一事件、前因、后果、印证和冲突外，还要识别共同具体驱动、共同约束、产业链跨层传导、不同市场信号相互印证等值得核验的关系。
- 不要因为双方处于不同市场层级、产业环节或时间阶段就直接拒绝；应判断是否存在一个具体、可核验的连接机制。
- 共同属于宽泛行业不是关系；双方材料明确指向同一个具体驱动、约束、事件进程或传导链，才值得保留。
- 当双方 Summary 陈述同一主体、同一具体事件或同一组关键事实时，必须保留给原文核验；不要因为内容重复就拒绝，重复报道可能构成同一事件或独立确认关系。
- 当双方 Summary 对齐同一主体、核心对象和目标时间，但分别提供该事件或状态的不同互补属性时，也必须保留给原文核验；互补描述可能共同指向同一事件，不能因文字不重复而漏掉。
- “互补属性”必须属于同一个可识别事件或状态；仅共享行业、品类、主体或宽泛时间窗口仍然不够。
- 双方描述不同目标期间、不同预测区间或并列指标时，不能仅因主体相同、期间相邻或数值形成序列就保留。只有 Summary 已显示明确更新关系、具体共同驱动、直接约束或其他可核验连接时，才进入原文核验。
- Summary 信息不足但关系可能具体时，可以保守保留给原文核验。
- 可以拒绝全部候选。
- 不创建事实，不输出关系类型、角色、理由、原文证据或长报告。
- 只返回需要继续核验的 candidate_id；未返回的候选即视为当前未发现关系。
"""

_VERIFY_SYSTEM_PROMPT = """你是知识图谱原子事件关系核验器。

一次只核验 source_card 和 candidate_card 这一对原子 Card。chunk_summary 只用于理解整个 Chunk 的背景，不得单独作为关系证据。evidence 只包含当前 Card 的焦点原文片段，每项只有 text，并按原文顺序排列。关系只能基于这些焦点片段判断。

裁决步骤：
1. 分别从双方 focus 片段确认两个原子事实，不补充输入之外的上下文事实。
2. 找出双方证据共同支持的最短连接桥梁，并判断它是对称关系还是有向关系。
3. 选择证据能够支持的最低强度关系，再区分 observed、inferred 和 no_relation。
4. 检查 basis、relation_type、direction 和 inference_mechanism 中的每个实质判断是否都能回到双方焦点文本；删除无法接地的中间环节。

证据强度规则：
- observed：双方焦点原文直接证明同一事件、明确前后关系、直接因果、印证或冲突。
- same_event：双方焦点证据对齐同一主体、核心对象和目标时间，并描述同一次事件或同一状态；双方可以分别提供方向、幅度、绝对水平、数量、范围或限定条件等互补属性，不要求文字重复。
- confirmation：双方来源分别提供能够独立支持同一核心事实的证据；如果只是同一事件的不同属性而非相互验证，优先使用 same_event。
- temporal_progression：必须存在先后发生的实际观察、披露、执行、修订或状态更新，后一个事实对前一个事实形成明确后续进展。材料同时发布但分别描述不同目标期间、预测区间或并列指标，不构成时间进展。
- 如果两项事实只是同一公告、报告或事件中的并列组成部分，应根据原文选择 same_event、共同具体驱动或 no_relation；不得仅因目标期间相邻、数值形成序列或叙述顺序靠后而输出 temporal_progression。
- inferred：双方焦点原文分别证明两个端点，并共同支持一个不需要新增事实的连接机制。原文不必逐字写出关系名称，但推理只能组合已给事实。
- 用作共同驱动、共同约束或有向桥梁的关键事实必须分别出现在双方提供的焦点文本中；不得补充输入之外的事实。
- no_relation：只有主题、行业、主体或关键词相似，或者关系必须依靠输入之外的事实才能成立。
- 如果主体、核心对象或目标时间无法对齐，不能仅凭数值接近、方向相同或共同市场背景判定 same_event。
- 可成立的关系包括同一事件、后续进展、前因与后果、共同具体驱动、共同具体约束、跨层传导、跨市场或跨来源印证、冲突与反向约束；关系名称必须服从证据，而不是反过来寻找材料填充某个关系名称。
- 形式上，X→A 且 X→B 只能证明共同驱动，不能推出 A→B。只有双方焦点证据还支持 A 是 B 的中间原因时，才能输出 A→B 的有向传导。
- 不得用“通常会”“一般而言”“行业逻辑上”等外部常识补充中间事件；不得自行补出盈利改善、扩产、采购、需求传递等输入未证明的环节。
- 双方位于不同市场层级、产业环节或时间阶段不是拒绝理由，但也不能自动证明跨层传导；缺少有向桥梁时应停留在共同驱动、共同约束或相互印证。
- 只使用双方提供的焦点文本，不要把输入之外的事实写入结论。
- source_card_id/target_card_id 表示事实语义方向，不按新旧输入顺序机械填写；对称关系的 direction 应描述共同因素如何分别作用于双方，不虚构 A→B。
- 不得引用输入之外的 ref，不得把材料发布时间直接当成事件发生时间。
- 如果裁决为 no_relation，只输出 decision_class，不输出 Card ID、关系说明、basis、证据引用或推理过程。
- 只有裁决为 observed 或 inferred 时，才输出完整关系字段、稳定 relation_kind、置信度和最小充分证据引用。
- observed 关系不需要推理链，inference_mechanism 必须输出空字符串；只有 inferred 才填写最短推理机制。
- relation_kind 只能从 same_event、confirmation、contradiction、temporal_progression、causal_influence、common_driver、constraint 中选择；不要自造枚举。
- confidence 表示双方焦点原文对最终关系裁决的支持强度，范围为 0 到 1，不得使用召回或 rerank 分数。
"""


class RelationDiscoveryService:
    """把原子 Card 转换为经过原文核验的关系决定。"""

    def __init__(
        self,
        *,
        repository: KnowledgeRepository | None = None,
        vector_store: MilvusRelationCandidateStore | None = None,
        reranker: RerankerClient | None = None,
        llm: Any | None = None,
        relation_writer: CardRelationWriteService | Any | None = None,
    ) -> None:
        self._repository = repository or KnowledgeRepositoryImpl(target="prod")
        self._vector_store = vector_store or MilvusRelationCandidateStore()
        self._reranker = reranker or RerankerClient(
            base_url=settings.RERANKER_URL,
            timeout=settings.RERANKER_TIMEOUT,
            max_documents=settings.RERANKER_MAX_DOCUMENTS,
        )
        self._llm = llm or get_llm_gateway_service()
        self._relation_writer = relation_writer or CardRelationWriteService(
            knowledge_repository=self._repository,
            relation_candidate_store=self._vector_store,
        )

    async def discover_card_relations(
        self,
        card_ids: list[str],
        *,
        adapter_name: str = "financial",
        target: str = "prod",
        include_evaluation_details: bool = False,
        persist_edges: bool = True,
    ) -> dict[str, Any]:
        unique_ids = [item for item in dict.fromkeys(card_ids) if item]
        with langfuse_observation(
            name="kg.relation_discovery",
            as_type="chain",
            input={
                "card_ids": unique_ids,
                "adapter_name": adapter_name,
                "target": target,
            },
            metadata={"pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION},
        ):
            if not unique_ids:
                result = self._empty_result(reason="no_card_ids")
                langfuse_update_span(output=result, status_message="completed")
                return result

            manifests = self._repository.list_atomic_cognitive_card_manifests_by_ids(
                adapter_name,
                cognitive_card_ids=unique_ids,
                status="active",
            )
            manifest_by_id = {item.cognitive_card_id: item for item in manifests}
            missing_ids = [item for item in unique_ids if item not in manifest_by_id]
            source_summaries = await self._vector_store.get_summaries(
                list(manifest_by_id),
                adapter_name=adapter_name,
                target=target,
            )
            missing_ids.extend(item for item in manifest_by_id if item not in source_summaries)
            if missing_ids:
                raise RuntimeError(f"关系发现缺少 Card manifest 或 Summary: {sorted(set(missing_ids))}")

            all_decisions: list[VerifiedRelationDecision] = []
            card_diagnostics: list[dict[str, Any]] = []
            seen_pairs: set[tuple[str, str]] = set()
            for card_id in unique_ids:
                if card_id not in manifest_by_id:
                    continue
                decisions, diagnostics = await self._discover_one(
                    manifest=manifest_by_id[card_id],
                    source_summary=source_summaries[card_id],
                    adapter_name=adapter_name,
                    target=target,
                    seen_pairs=seen_pairs,
                    include_evaluation_details=include_evaluation_details,
                )
                all_decisions.extend(decisions)
                card_diagnostics.append(diagnostics)

            edge_persistence = (
                await self._relation_writer.persist_verified_decisions(
                    all_decisions,
                    adapter_name=adapter_name,
                    target=target,
                    pipeline_version=RELATION_DISCOVERY_PIPELINE_VERSION,
                    model_name=resolve_kg_llm_model("kg_relation_evidence_verify"),
                    prompt_version=RELATION_VERIFICATION_PROMPT_VERSION,
                )
                if persist_edges
                else {"skipped": True, "reason": "persist_edges_disabled"}
            )

            result = {
                "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
                "cards_requested": len(unique_ids),
                "cards_processed": len(card_diagnostics),
                "missing_card_ids": sorted(set(missing_ids)),
                "decisions": [item.as_dict() for item in all_decisions],
                "observed": sum(item.decision_class == "observed" for item in all_decisions),
                "inferred": sum(item.decision_class == "inferred" for item in all_decisions),
                "no_relation": sum(item.decision_class == "no_relation" for item in all_decisions),
                "edge_persistence": edge_persistence,
                "card_diagnostics": card_diagnostics,
            }
            langfuse_update_span(output=result, status_message="completed")
            return result

    async def _discover_one(
        self,
        *,
        manifest: CognitiveCardManifest,
        source_summary: RelationCardText,
        adapter_name: str,
        target: str,
        seen_pairs: set[tuple[str, str]],
        include_evaluation_details: bool,
    ) -> tuple[list[VerifiedRelationDecision], dict[str, Any]]:
        routes = build_relation_routes(
            source_card_id=manifest.cognitive_card_id,
            summary=source_summary.text,
            relation_probes=manifest.relation_probes,
            generator_version=RELATION_DISCOVERY_PIPELINE_VERSION,
        )
        with langfuse_observation(
            name="kg.relation.plan_routes",
            as_type="span",
            input={"card_id": manifest.cognitive_card_id},
        ):
            langfuse_update_span(
                output={"routes": [asdict(route) for route in routes]},
                status_message="completed",
            )

        recalled = await self._recall_routes(
            routes,
            adapter_name=adapter_name,
            target=target,
        )
        recalled, same_chunk_excluded_ids = self._exclude_same_chunk_candidates(
            recalled,
            source_manifest=manifest,
            adapter_name=adapter_name,
        )
        route_hits = await self._rerank_routes(
            routes,
            recalled,
            source_card_id=manifest.cognitive_card_id,
            source_schema_version=manifest.schema_version,
            adapter_name=adapter_name,
            target=target,
        )
        with langfuse_observation(
            name="kg.relation.merge_candidates",
            as_type="span",
            input={"route_hits": len(route_hits)},
        ):
            merged = self._merge_candidates(route_hits)
            selected = self._select_candidate_budget(merged)
            langfuse_update_span(
                output={
                    "merged_candidates": len(merged),
                    "selected_candidates": len(selected),
                    "focus_only": sum(item.recall_views == ["focus_evidence"] for item in selected),
                },
                status_message="completed",
            )
        kept_ids = await self._screen_candidates(
            source_card_id=manifest.cognitive_card_id,
            source_summary=source_summary,
            candidates=selected,
        )
        candidate_manifests = self._repository.list_atomic_cognitive_card_manifests_by_ids(
            adapter_name,
            cognitive_card_ids=kept_ids,
            status="active",
        )
        candidate_manifest_by_id = {item.cognitive_card_id: item for item in candidate_manifests}
        candidate_summaries = await self._vector_store.get_summaries(
            list(candidate_manifest_by_id),
            adapter_name=adapter_name,
            target=target,
        )

        packages: list[PairEvidencePackage] = []
        pair_errors: list[dict[str, str]] = []
        for candidate_id in kept_ids:
            pair = canonical_card_pair(manifest.cognitive_card_id, candidate_id)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            candidate_manifest = candidate_manifest_by_id.get(candidate_id)
            candidate_summary = candidate_summaries.get(candidate_id)
            try:
                if candidate_manifest is None or candidate_summary is None:
                    raise RuntimeError(f"关系核验候选数据缺失: {candidate_id}")
                packages.append(
                    await self._load_pair_evidence(
                        source_manifest=manifest,
                        source_summary=source_summary,
                        candidate_manifest=candidate_manifest,
                        candidate_summary=candidate_summary,
                        adapter_name=adapter_name,
                        target=target,
                    )
                )
            except (RuntimeError, ValueError) as exc:
                pair_errors.append(
                    {
                        "candidate_card_id": candidate_id,
                        "error_type": exc.__class__.__name__,
                        "error": str(exc),
                    }
                )
                logger.error(
                    "关系核验 pair 数据完整性失败: source=%s candidate=%s error=%s",
                    manifest.cognitive_card_id,
                    candidate_id,
                    exc,
                )

        decisions = await self._verify_packages(packages)
        diagnostics = {
            "card_id": manifest.cognitive_card_id,
            "routes": len(routes),
            "recalled_hits": sum(len(items) for items in recalled.values()),
            "route_reranked_hits": len(route_hits),
            "merged_candidates": len(merged),
            "screened_candidates": len(selected),
            "kept_for_evidence": len(kept_ids),
            "verified_pairs": len(decisions),
            "pair_data_errors": pair_errors,
            "focus_only_candidates": sum(item.recall_views == ["focus_evidence"] for item in selected),
            "same_chunk_excluded_candidates": len(same_chunk_excluded_ids),
            "same_chunk_excluded_candidate_ids": same_chunk_excluded_ids,
        }
        if include_evaluation_details:
            route_by_id = {item.route_id: item for item in routes}
            reranked_by_route: dict[str, list[str]] = {}
            for item in route_hits:
                reranked_by_route.setdefault(item.route_id, []).append(item.candidate_card_id)
            diagnostics["evaluation_details"] = {
                "budgets": {
                    "recall_per_view": settings.KG_RELATION_RECALL_PER_VIEW,
                    "rerank_top_n": settings.KG_RELATION_RERANK_TOP_N,
                    "merged_candidate_limit": settings.KG_RELATION_MERGED_CANDIDATE_LIMIT,
                },
                "routes": [
                    {
                        "route_id": route.route_id,
                        "route_type": route.route_type,
                        "role": route.role,
                        "query": route.query,
                        "summary_recalled_ids": _ordered_candidate_ids(
                            item.candidate_card_id
                            for item in recalled.get(route.route_id, [])
                            if item.recall_view == "summary"
                        ),
                        "focus_recalled_ids": _ordered_candidate_ids(
                            item.candidate_card_id
                            for item in recalled.get(route.route_id, [])
                            if item.recall_view == "focus_evidence"
                        ),
                        "reranked_ids": _ordered_candidate_ids(
                            reranked_by_route.get(route.route_id, [])
                        ),
                    }
                    for route in routes
                ],
                "merged_candidate_ids": [item.candidate_card_id for item in merged],
                "selected_candidate_ids": [item.candidate_card_id for item in selected],
                "screened_related_candidate_ids": list(kept_ids),
                "verified_candidate_ids": _ordered_candidate_ids(
                    _other_card_id(item, manifest.cognitive_card_id)
                    for item in decisions
                ),
                "route_count": len(route_by_id),
            }
        return decisions, diagnostics

    def _exclude_same_chunk_candidates(
        self,
        recalled: dict[str, list[RelationRecallHit]],
        *,
        source_manifest: CognitiveCardManifest,
        adapter_name: str,
    ) -> tuple[dict[str, list[RelationRecallHit]], list[str]]:
        candidate_ids = sorted(
            {
                hit.candidate_card_id
                for hits in recalled.values()
                for hit in hits
                if hit.candidate_card_id != source_manifest.cognitive_card_id
            }
        )
        manifests = self._repository.list_atomic_cognitive_card_manifests_by_ids(
            adapter_name,
            cognitive_card_ids=candidate_ids,
            status="active",
        )
        excluded_ids = {
            item.cognitive_card_id
            for item in manifests
            if item.primary_chunk_id == source_manifest.primary_chunk_id
        }
        if not excluded_ids:
            return recalled, []
        return (
            {
                route_id: [
                    hit
                    for hit in hits
                    if hit.candidate_card_id not in excluded_ids
                ]
                for route_id, hits in recalled.items()
            },
            sorted(excluded_ids),
        )

    async def _recall_routes(
        self,
        routes: list[RelationRoute],
        *,
        adapter_name: str,
        target: str,
    ) -> dict[str, list[RelationRecallHit]]:
        with langfuse_observation(
            name="kg.relation.route_recall",
            as_type="retriever",
            input={"routes": [asdict(route) for route in routes]},
        ):
            result = await self._vector_store.recall_routes(
                routes,
                adapter_name=adapter_name,
                target=target,
                limit_per_view=settings.KG_RELATION_RECALL_PER_VIEW,
            )
            for route in routes:
                hits = result.get(route.route_id, [])
                with langfuse_observation(
                    name="kg.relation.route_recall.detail",
                    as_type="retriever",
                    input=asdict(route),
                ):
                    langfuse_update_span(
                        output={
                            "hits": len(hits),
                            "summary_hits": sum(item.recall_view == "summary" for item in hits),
                            "focus_hits": sum(item.recall_view == "focus_evidence" for item in hits),
                            "hit_details": [asdict(item) for item in hits],
                        },
                        status_message="completed",
                    )
            langfuse_update_span(
                output={
                    route_id: {
                        "hits": len(hits),
                        "summary_hits": sum(item.recall_view == "summary" for item in hits),
                        "focus_hits": sum(item.recall_view == "focus_evidence" for item in hits),
                    }
                    for route_id, hits in result.items()
                },
                status_message="completed",
            )
            return result

    async def _rerank_routes(
        self,
        routes: list[RelationRoute],
        recalled: dict[str, list[RelationRecallHit]],
        *,
        source_card_id: str,
        source_schema_version: str,
        adapter_name: str,
        target: str,
    ) -> list[RouteCandidateHit]:
        semaphore = asyncio.Semaphore(max(1, settings.KG_RELATION_RERANK_CONCURRENCY))

        async def rerank_route(route: RelationRoute) -> list[RouteCandidateHit]:
            recall_by_card: dict[str, list[RelationRecallHit]] = {}
            for hit in recalled.get(route.route_id, []):
                if hit.candidate_card_id == source_card_id or hit.recall_score < 0:
                    continue
                recall_by_card.setdefault(hit.candidate_card_id, []).append(hit)
            if not recall_by_card:
                return []
            summaries = await self._vector_store.get_summaries(
                list(recall_by_card),
                adapter_name=adapter_name,
                target=target,
            )
            candidate_ids = [
                item
                for item in recall_by_card
                if item in summaries
                and str(summaries[item].metadata.get("status") or "active") == "active"
            ]
            if not candidate_ids:
                return []
            documents = [summaries[item].text for item in candidate_ids]
            async with semaphore:
                with langfuse_observation(
                    name="kg.relation.route_rerank",
                    as_type="span",
                    input={
                        "route": asdict(route),
                        "candidate_ids": candidate_ids,
                        "documents": documents,
                    },
                ):
                    response = await self._reranker.rerank(
                        query=route.query,
                        documents=documents,
                        top_n=min(settings.KG_RELATION_RERANK_TOP_N, len(documents)),
                    )
                    route_result: list[RouteCandidateHit] = []
                    filtered_below_min_score = 0
                    for rank, item in enumerate(response.results, start=1):
                        if item.index < 0 or item.index >= len(candidate_ids):
                            raise RuntimeError(f"reranker 返回非法 index: {item.index}")
                        if item.relevance_score < settings.KG_RELATION_RERANK_MIN_SCORE:
                            filtered_below_min_score += 1
                            continue
                        candidate_id = candidate_ids[item.index]
                        route_result.append(
                            RouteCandidateHit(
                                candidate_card_id=candidate_id,
                                candidate_summary=summaries[candidate_id].text,
                                candidate_published_at=str(
                                    summaries[candidate_id].metadata.get("source_published_at") or ""
                                ),
                                route_id=route.route_id,
                                route_type=route.route_type,
                                role=route.role,
                                query=route.query,
                                recall_hits=recall_by_card[candidate_id],
                                rerank_rank=rank,
                                rerank_score=float(item.relevance_score),
                            )
                        )
                    langfuse_update_span(
                        output={
                            "ranked": len(response.results),
                            "retained": len(route_result),
                            "filtered_below_min_score": filtered_below_min_score,
                            "min_score": settings.KG_RELATION_RERANK_MIN_SCORE,
                            "top": [
                                {
                                    "candidate_card_id": candidate_ids[item.index],
                                    "score": item.relevance_score,
                                }
                                for item in response.results[:10]
                            ],
                        },
                        status_message="completed",
                    )
                    return route_result

        grouped = await asyncio.gather(*(rerank_route(route) for route in routes))
        return [item for group in grouped for item in group]

    @staticmethod
    def _merge_candidates(route_hits: list[RouteCandidateHit]) -> list[MergedRelationCandidate]:
        merged: dict[str, MergedRelationCandidate] = {}
        for hit in route_hits:
            item = merged.setdefault(
                hit.candidate_card_id,
                MergedRelationCandidate(
                    candidate_card_id=hit.candidate_card_id,
                    candidate_summary=hit.candidate_summary,
                    candidate_published_at=hit.candidate_published_at,
                ),
            )
            item.route_hits.append(hit)
            item.rrf_score += 1.0 / (_RRF_K + hit.rerank_rank)
        return sorted(
            merged.values(),
            key=lambda item: (-item.rrf_score, item.candidate_card_id),
        )

    @staticmethod
    def _select_candidate_budget(
        candidates: list[MergedRelationCandidate],
    ) -> list[MergedRelationCandidate]:
        limit = max(1, settings.KG_RELATION_MERGED_CANDIDATE_LIMIT)
        if len(candidates) <= limit:
            return candidates
        return candidates[:limit]

    async def _screen_candidates(
        self,
        *,
        source_card_id: str,
        source_summary: RelationCardText,
        candidates: list[MergedRelationCandidate],
    ) -> list[str]:
        if not candidates:
            return []
        result: list[str] = []
        size = max(1, settings.KG_RELATION_SCREEN_BATCH_SIZE)
        for start in range(0, len(candidates), size):
            batch = candidates[start : start + size]
            payload = {
                "source_card": {
                    "card_id": source_card_id,
                    "summary": source_summary.text,
                    "source_published_at": source_summary.metadata.get("source_published_at") or "",
                },
                "candidates": [self._screen_candidate_payload(item) for item in batch],
            }
            with langfuse_observation(
                name="kg.relation.summary_screen",
                as_type="span",
                input=payload,
            ):
                data = await self._call_structured_llm(
                    task="kg_relation_candidate_screen",
                    system_prompt=_SCREEN_SYSTEM_PROMPT,
                    payload=payload,
                    schema=_screen_schema([item.candidate_card_id for item in batch]),
                    max_tokens=1000,
                    reasoning_effort="disabled",
                )
                expected_ids = [item.candidate_card_id for item in batch]
                repaired_output = False
                try:
                    related_ids = _parse_screening_candidate_ids(data, expected_ids=expected_ids)
                except ValueError as exc:
                    repaired = await self._call_structured_llm(
                        task="kg_relation_candidate_screen",
                        system_prompt=_SCREEN_SYSTEM_PROMPT,
                        payload={
                            **payload,
                            "repair": {
                                "validation_error": str(exc),
                                "previous_output": data,
                                "instruction": "只修复输出契约，不补充输入之外的事实。",
                            },
                        },
                        schema=_screen_schema(expected_ids),
                        max_tokens=1000,
                        reasoning_effort="disabled",
                        use_cache=False,
                    )
                    related_ids = _parse_screening_candidate_ids(repaired, expected_ids=expected_ids)
                    repaired_output = True
                langfuse_update_span(
                    output={
                        "related_candidate_ids": related_ids,
                        "repaired": repaired_output,
                    },
                    status_message="completed",
                )
            result.extend(related_ids)
        return result

    async def _load_pair_evidence(
        self,
        *,
        source_manifest: CognitiveCardManifest,
        source_summary: RelationCardText,
        candidate_manifest: CognitiveCardManifest,
        candidate_summary: RelationCardText,
        adapter_name: str,
        target: str,
    ) -> PairEvidencePackage:
        with langfuse_observation(
            name="kg.relation.load_pair_evidence",
            as_type="span",
            input={
                "source_card_id": source_manifest.cognitive_card_id,
                "candidate_card_id": candidate_manifest.cognitive_card_id,
            },
        ):
            chunks = await self._vector_store.get_chunks(
                [source_manifest.primary_chunk_id, candidate_manifest.primary_chunk_id],
                adapter_name=adapter_name,
                target=target,
            )
            source_chunk = chunks.get(source_manifest.primary_chunk_id)
            candidate_chunk = chunks.get(candidate_manifest.primary_chunk_id)
            if source_chunk is None or candidate_chunk is None:
                raise RuntimeError(
                    "关系核验缺少 Primary Chunk: "
                    f"source={source_manifest.primary_chunk_id in chunks} "
                    f"candidate={candidate_manifest.primary_chunk_id in chunks}"
                )
            source_text = _raw_chunk_text(source_chunk.text)
            candidate_text = _raw_chunk_text(candidate_chunk.text)
            source_context, source_refs = materialize_focus_evidence_context(
                source_text,
                focus_span_offsets=source_manifest.focus_span_offsets,
            )
            candidate_context, candidate_refs = materialize_focus_evidence_context(
                candidate_text,
                focus_span_offsets=candidate_manifest.focus_span_offsets,
            )
            # 关系核验只需要 Card 的最小证据窗口；未命中 focus span 的句子
            # 会把完整 chunk 带入 LLM，增加 token 却不能作为该 Card 的证据。
            source_context = [
                item for item in source_context if item.get("evidence_ref")
            ]
            candidate_context = [
                item for item in candidate_context if item.get("evidence_ref")
            ]
            package = PairEvidencePackage(
                source_card_id=source_manifest.cognitive_card_id,
                source_evidence_context=source_context,
                source_focus_refs=source_refs,
                source_published_at=str(source_summary.metadata.get("source_published_at") or ""),
                source_chunk_summary=str(source_summary.metadata.get("chunk_summary") or ""),
                candidate_card_id=candidate_manifest.cognitive_card_id,
                candidate_evidence_context=candidate_context,
                candidate_focus_refs=candidate_refs,
                candidate_published_at=str(candidate_summary.metadata.get("source_published_at") or ""),
                candidate_chunk_summary=str(candidate_summary.metadata.get("chunk_summary") or ""),
            )
            langfuse_update_span(
                output={
                    "source_chunk_chars": len(source_text),
                    "candidate_chunk_chars": len(candidate_text),
                    "source_focus_refs": package.source_focus_refs,
                    "candidate_focus_refs": package.candidate_focus_refs,
                },
                status_message="completed",
            )
            return package

    async def _verify_packages(
        self,
        packages: list[PairEvidencePackage],
    ) -> list[VerifiedRelationDecision]:
        semaphore = asyncio.Semaphore(max(1, settings.KG_RELATION_VERIFY_CONCURRENCY))

        async def verify(package: PairEvidencePackage) -> VerifiedRelationDecision:
            async with semaphore:
                with langfuse_observation(
                    name="kg.relation.evidence_verify",
                    as_type="span",
                    input=_verification_payload(package),
                ):
                    data = await self._call_structured_llm(
                        task="kg_relation_evidence_verify",
                        system_prompt=_VERIFY_SYSTEM_PROMPT,
                        payload=_verification_payload(package),
                        schema=_verification_schema(package),
                        max_tokens=5000,
                        reasoning_effort="disabled",
                    )
                    try:
                        decision = _parse_verified_decision(data, package)
                    except ValueError as exc:
                        repaired = await self._call_structured_llm(
                            task="kg_relation_evidence_verify",
                            system_prompt=_VERIFY_SYSTEM_PROMPT,
                            payload={
                                **_verification_payload(package),
                                "repair": {
                                    "validation_error": str(exc),
                                    "previous_output": data,
                                    "instruction": "只修复输出契约，不补充输入之外的事实。",
                                },
                            },
                            schema=_verification_schema(package),
                            max_tokens=5000,
                            reasoning_effort="disabled",
                            use_cache=False,
                        )
                        decision = _parse_verified_decision(repaired, package)
                    langfuse_update_span(output=decision.as_dict(), status_message="completed")
                    return decision

        return list(await asyncio.gather(*(verify(item) for item in packages))) if packages else []

    async def _call_structured_llm(
        self,
        *,
        task: str,
        system_prompt: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
        max_tokens: int,
        reasoning_effort: str,
        use_cache: bool = True,
    ) -> dict[str, Any]:
        model = resolve_kg_llm_model(task)
        request = LLMProxyRequest(
            model=model,
            system_prompt=system_prompt,
            prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            temperature=0,
            max_tokens=max_tokens,
            json_schema=schema,
            provider_options={
                "reasoning_effort": reasoning_effort,
                "thinking_type": "disabled" if reasoning_effort == "disabled" else "",
            },
            metadata={
                "task": task,
                "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
                "_cache_key_metadata": {
                    "task": task,
                    "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
                },
            },
            use_cache=use_cache,
        )
        response = await self._llm.generate(request)
        if isinstance(response.structured_output, dict):
            return response.structured_output
        if (
            isinstance(response.structured_output, list)
            and len(response.structured_output) == 1
            and isinstance(response.structured_output[0], dict)
        ):
            return response.structured_output[0]
        shape = type(response.structured_output).__name__
        if isinstance(response.structured_output, list):
            shape += f"[len={len(response.structured_output)}]"
        raise ValueError(f"{task} 顶层输出必须是 JSON object, actual={shape}")

    @staticmethod
    def _screen_candidate_payload(item: MergedRelationCandidate) -> dict[str, Any]:
        return {
            "candidate_id": item.candidate_card_id,
            "summary": item.candidate_summary,
            "source_published_at": item.candidate_published_at,
        }

    @staticmethod
    def _empty_result(*, reason: str) -> dict[str, Any]:
        return {
            "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
            "cards_requested": 0,
            "cards_processed": 0,
            "reason": reason,
            "decisions": [],
            "observed": 0,
            "inferred": 0,
            "no_relation": 0,
            "edge_persistence": {"skipped": True, "reason": reason},
            "card_diagnostics": [],
        }


def _route_hit_payload(hit: RouteCandidateHit) -> dict[str, Any]:
    return {
        "route_id": hit.route_id,
        "route_type": hit.route_type,
        "role": hit.role,
        "query": hit.query,
        "rerank_rank": hit.rerank_rank,
        "rerank_score": hit.rerank_score,
        "recall_hits": [asdict(item) for item in hit.recall_hits],
    }


def _screen_schema(candidate_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "related_candidate_ids": {
                "type": "array",
                "maxItems": len(candidate_ids),
                "uniqueItems": True,
                "items": {"type": "string", "enum": candidate_ids},
            }
        },
        "required": ["related_candidate_ids"],
        "additionalProperties": False,
    }


def _parse_screening_candidate_ids(
    data: dict[str, Any],
    *,
    expected_ids: list[str],
) -> list[str]:
    raw = data.get("related_candidate_ids")
    if not isinstance(raw, list):
        raise ValueError("关系初筛 related_candidate_ids 必须是数组")
    result = [str(item) for item in raw]
    if len(result) != len(set(result)) or not set(result).issubset(expected_ids):
        raise ValueError(
            f"关系初筛 candidate_id 非法: expected_subset_of={expected_ids}, returned={result}"
        )
    selected = set(result)
    return [item for item in expected_ids if item in selected]


def _verification_schema(package: PairEvidencePackage) -> dict[str, Any]:
    card_ids = [package.source_card_id, package.candidate_card_id]
    return {
        "type": "object",
        "properties": {
            "source_card_id": {"type": "string", "enum": card_ids},
            "target_card_id": {"type": "string", "enum": card_ids},
            "decision_class": {
                "type": "string",
                "enum": ["observed", "inferred", "no_relation"],
            },
            "relation_kind": {"type": "string", "enum": sorted(RELATION_KINDS)},
            "relation_type": {"type": "string", "maxLength": 120},
            "direction": {"type": "string", "maxLength": 120},
            "basis": {"type": "string", "maxLength": 1000},
            "inference_mechanism": {"type": "string", "maxLength": 1000},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "required": ["decision_class"],
        "additionalProperties": False,
    }


def _parse_verified_decision(
    data: dict[str, Any],
    package: PairEvidencePackage,
) -> VerifiedRelationDecision:
    decision_class = str(data.get("decision_class") or "")
    if decision_class not in {"observed", "inferred", "no_relation"}:
        raise ValueError(f"未知关系决定: {decision_class}")
    if decision_class == "no_relation":
        if set(data) != {"decision_class"}:
            raise ValueError("no_relation 只能输出 decision_class")
        return VerifiedRelationDecision(
            source_card_id=package.source_card_id,
            target_card_id=package.candidate_card_id,
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

    source_id = str(data.get("source_card_id") or "")
    target_id = str(data.get("target_card_id") or "")
    expected_ids = {package.source_card_id, package.candidate_card_id}
    if source_id == target_id or {source_id, target_id} != expected_ids:
        raise ValueError(f"关系核验端点错误: source={source_id}, target={target_id}")
    refs_by_card = {
        package.source_card_id: list(package.source_focus_refs),
        package.candidate_card_id: list(package.candidate_focus_refs),
    }
    source_refs = list(refs_by_card[source_id])
    target_refs = list(refs_by_card[target_id])
    mechanism = str(data.get("inference_mechanism") or "").strip()
    relation_kind = str(data.get("relation_kind") or "").strip()
    relation_type = str(data.get("relation_type") or "").strip()
    direction = str(data.get("direction") or "").strip()
    if not source_refs or not target_refs or not relation_type or not direction:
        raise ValueError("Observed/Inferred 关系必须包含类型、方向和双方证据引用")
    if relation_kind not in RELATION_KINDS:
        raise ValueError(f"Observed/Inferred relation_kind 非法: {relation_kind}")
    if decision_class == "inferred" and not mechanism:
        raise ValueError("Inferred 关系必须包含 inference_mechanism")
    basis = str(data.get("basis") or "").strip()
    if not basis:
        raise ValueError("关系核验 basis 不能为空")
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ValueError("关系核验 confidence 必须是 0 到 1 的数字") from exc
    if not 0 <= confidence <= 1:
        raise ValueError("关系核验 confidence 必须处于 0 到 1")
    return VerifiedRelationDecision(
        source_card_id=source_id,
        target_card_id=target_id,
        decision_class=decision_class,  # type: ignore[arg-type]
        relation_kind=relation_kind,
        relation_type=relation_type,
        direction=direction,
        basis=basis,
        source_evidence_refs=source_refs,
        target_evidence_refs=target_refs,
        inference_mechanism=mechanism,
        confidence=confidence,
    )


def _verification_payload(package: PairEvidencePackage) -> dict[str, Any]:
    """关系核验只接收双方原文事实，不暴露检索和存储工程字段。"""

    return {
        "source_card": {
            "card_id": package.source_card_id,
            "source_published_at": package.source_published_at,
            "chunk_summary": package.source_chunk_summary,
        "evidence": [
            {"text": item["text"]}
            for item in package.source_evidence_context
            if item.get("text")
        ],
        },
        "candidate_card": {
            "card_id": package.candidate_card_id,
            "source_published_at": package.candidate_published_at,
            "chunk_summary": package.candidate_chunk_summary,
        "evidence": [
            {"text": item["text"]}
            for item in package.candidate_evidence_context
            if item.get("text")
        ],
        },
    }


def _raw_chunk_text(text: str) -> str:
    marker = "Evidence Text:"
    if marker in text:
        raw = text.rsplit(marker, 1)[1]
        # 语义文档格式固定在 marker 后插入一个分隔空格；只移除该分隔符，
        # 不使用 strip()，避免改变原始 Chunk offset。
        return raw[1:] if raw.startswith(" ") else raw
    return text


def _ordered_candidate_ids(values: Any) -> list[str]:
    return [item for item in dict.fromkeys(str(value) for value in values if str(value).strip())]


def _other_card_id(decision: VerifiedRelationDecision, source_card_id: str) -> str:
    return (
        decision.target_card_id
        if decision.source_card_id == source_card_id
        else decision.source_card_id
    )
