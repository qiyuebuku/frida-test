"""跨 Chunk Card 召回、Summary 初筛与原文关系核验服务。"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from src.application.services.card_relation_write_service import (
    CardRelationWriteService,
)
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
from src.infrastructure.persistence.repositories.knowledge_repository_impl import (
    KnowledgeRepositoryImpl,
)
from src.infrastructure.vector_store.relation_candidate_store import (
    MilvusRelationCandidateStore,
    RelationCardText,
)


logger = logging.getLogger(__name__)

RELATION_DISCOVERY_PIPELINE_VERSION = (
    "relation_discovery_v8_bidirectional_fact_identity"
)
RELATION_SCREEN_CACHE_VERSION = "relation_discovery_v3_glm53_low_reasoning"
RELATION_SCREEN_MAX_TOKENS = 16_000
RELATION_VERIFY_MAX_TOKENS = 32_000
RELATION_SAME_FACT_MAX_TOKENS = 16_000
RELATION_VERIFICATION_PROMPT_VERSION = (
    "kg_relation_evidence_verify_v12_claim_list_fact_gate"
)
_RRF_K = 60

_SCREEN_SYSTEM_PROMPT = """你是知识图谱关系候选初筛器。

你只根据当前原子 Card Summary、候选 Card Summary 和材料发布时间，筛出存在足够具体潜在关系、值得读取完整原文进一步核验的 candidate_id。

规则：
- 这不是普通相关性判断；同主题、同行业、同公司或关键词相似不等于存在关系。
- 除同一事件、前因、后果、印证和冲突外，还要识别共同具体驱动、共同约束、产业链跨层传导、不同市场信号相互印证等值得核验的关系。
- 不要因为双方处于不同市场层级、产业环节或时间阶段就直接拒绝；应判断是否存在一个具体、可核验的连接机制。
- 共同属于宽泛行业不是关系；双方材料明确指向同一个具体驱动、约束、事件进程或传导链，才值得保留。
- 当双方 Summary 陈述同一主体、同一具体事件或同一组关键事实时，必须保留给原文核验；不要因为内容重复就拒绝，原文核验需要区分等价事实、同一事件的不同事实和独立确认。
- 当双方 Summary 对齐同一主体、核心对象和目标时间，但分别提供该事件或状态的不同互补属性时，也必须保留给原文核验；互补描述可能共同指向同一事件，不能因文字不重复而漏掉。
- “互补属性”必须属于同一个可识别事件或状态；仅共享行业、品类、主体或宽泛时间窗口仍然不够。
- 双方描述不同目标期间、不同预测区间或并列指标时，不能仅因主体相同、期间相邻或数值形成序列就保留。只有 Summary 已显示明确更新关系、具体共同驱动、直接约束或其他可核验连接时，才进入原文核验。
- Summary 信息不足但关系可能具体时，可以保守保留给原文核验。
- 可以拒绝全部候选。
- 不创建事实，不输出关系类型、角色、理由、原文证据或长报告。
- 只返回需要继续核验的 candidate_id；未返回的候选即视为当前未发现关系。
"""

_VERIFY_SYSTEM_PROMPT = """你是知识图谱原子事件关系核验器。

一次只核验 source_card 和 candidate_card 这一对原子 Card。
- card_summary 定义当前 Card 要表达的原子事实，是关系端点的语义边界。
- evidence 是支持该 Card 的焦点原文；它可能同时包含同一段话中的其他事实，不能把这些额外事实自动视为当前 Card 的主张。
- chunk_summary 只用于消解主体、指代和背景，不得单独作为关系证据，也不得扩大 card_summary 的事实边界。
- 最终关系必须同时符合双方 card_summary，并由双方 evidence 接地。

裁决步骤：
1. 先读取双方 card_summary，确定本次真正比较的两个原子主张。
2. 再用各自 evidence 核验主张；原文中不属于 card_summary 的并列内容不得进入当前关系。
3. 如果 card_summary 与 evidence 不一致，以 evidence 为事实边界，但不得借用 evidence 中与该 Card 主张无关的其他句子扩大主张。
4. 找出双方主张与证据共同支持的最短连接桥梁，并判断它是对称关系还是有向关系。
5. 选择证据能够支持的最低强度关系，再区分 observed、inferred 或不成立。
6. 检查 basis、relation_type、direction 和 inference_mechanism 中的每个实质判断是否都能回到双方 card_summary 与焦点文本；删除无法接地的中间环节。

证据强度规则：
- observed：双方焦点原文直接证明等价事实、同一事件、明确前后关系、直接因果、印证或冲突。
- same_fact：双方 card_summary 陈述可以互相替代的同一个原子事实。主体、动作或状态、核心对象、目标时间、方向或否定性以及影响结论的关键限定必须一致；来源、措辞、无实质影响的四舍五入可以不同。用任意一方的 card_summary 替换另一方后，不得丢失会改变 Agent 判断的事实信息。
- 即使双方 evidence 来自同一段原文或内容完全相同，只要两个 card_summary 分别选择了该段原文中的不同主张，就禁止判为 same_fact。
- same_fact 必须满足双向完整等价，而不是只存在一个相同子句。逐项检查双方 card_summary 中的每个独立动作、状态、指标和条件：任一方包含另一方没有的独立主张，就是子集/超集关系，禁止判为 same_fact；同一现实事件中的这种包含关系应判为 same_event。
- 如果双方分别提供不同属性、不同阶段、不同指标、不同后果或额外限定，即使属于同一次现实事件，也不是 same_fact。此时根据证据选择 same_event 或其他业务关系。
- same_event：双方属于同一个可唯一识别的现实事件、公告、交易、执行动作或同一资产的同次状态观测，但各自表达的是不可互相替代的不同事实侧面。它保留事件内部结构，不参与 Card 去重。
- same_event 必须能对齐事件主体、核心对象和具体发生或观测窗口。仅处于同一交易日、同一市场、同一行业、同一行情方向或同一宏观叙事，不是同一事件。
- 个股上涨与市场指数上涨、行业板块表现与任意成分股表现、不同资产在同一时段同向波动，不能仅凭共同市场背景标为 same_event；根据焦点证据选择 market_co_movement、confirmation、common_driver 或不建立关系。
- same_fact 和 same_event 都只能在双方焦点证据可直接对齐时裁决为 observed；不得输出 inferred。
- 必须先检查是否满足严格的 same_fact；不满足可替代性时禁止为了去重而使用 same_fact。confirmation 用于不同观察或不同事实对同一结论形成独立支持，不能替代 same_fact 或 same_event。
- temporal_progression：必须存在先后发生的实际观察、披露、执行、修订或状态更新，后一个事实对前一个事实形成明确后续进展。材料同时发布但分别描述不同目标期间、预测区间或并列指标，不构成时间进展。
- 如果两项事实只是同一公告、报告或事件中的并列组成部分，应根据原文选择 same_event、共同具体驱动或不建立关系；并列事实不得标记为 same_fact，也不得仅因目标期间相邻、数值形成序列或叙述顺序靠后而输出 temporal_progression。
- inferred：双方焦点原文分别证明两个端点，并共同支持一个不需要新增事实的连接机制。原文不必逐字写出关系名称，但推理只能组合已给事实。
- market_co_movement：双方焦点原文分别证明具有明确共同市场对象或直接资产暴露的两个市场载体，在可比较时间窗口内呈现同向或反向的价格、指数、板块或资产表现。它只表达跨市场信号同步或背离，不表达因果、传导或共同驱动；通常属于 inferred 和对称关系。例如现货贵金属与贵金属股票板块同步走弱，可以构成该关系，但普通大盘下跌与任意商品下跌不能仅凭方向相同建立该关系。
- market_co_movement 不要求双方证明同一个事实，因此不要误标为 confirmation；也不要求双方写出共同驱动，因此不得为了建边补造宏观原因。basis 和 inference_mechanism 必须明确共同市场对象、双方市场载体以及同步或背离方向。
- 用作共同驱动、共同约束或有向桥梁的关键事实必须分别出现在双方提供的焦点文本中；不得补充输入之外的事实。
- 只有主题、行业、主体或关键词相似，或者关系必须依靠输入之外的事实才能成立时，不返回关系。
- 如果主体、核心对象或目标时间无法对齐，不能仅凭数值接近、方向相同或共同市场背景判定 same_event。
- 可成立的关系包括同一事件、后续进展、前因与后果、共同具体驱动、共同具体约束、跨层传导、跨市场或跨来源印证、冲突与反向约束；关系名称必须服从证据，而不是反过来寻找材料填充某个关系名称。
- 形式上，X→A 且 X→B 只能证明共同驱动，不能推出 A→B。只有双方焦点证据还支持 A 是 B 的中间原因时，才能输出 A→B 的有向传导。
- 不得用“通常会”“一般而言”“行业逻辑上”等外部常识补充中间事件；不得自行补出盈利改善、扩产、采购、需求传递等输入未证明的环节。
- 双方位于不同市场层级、产业环节或时间阶段不是拒绝理由，但也不能自动证明跨层传导；缺少有向桥梁时应停留在共同驱动、共同约束或相互印证。
- 只使用双方提供的焦点文本，不要把输入之外的事实写入结论。
- source_card_id/target_card_id 表示事实语义方向，不按新旧输入顺序机械填写；对称关系不得虚构 A→B。common_driver 的 direction 描述共同因素如何分别作用于双方，market_co_movement 的 direction 只描述同步或背离。
- 不得引用输入之外的 ref，不得把材料发布时间直接当成事件发生时间。
- 顶层只输出 relations 数组。关系不成立时输出空数组 `{"relations":[]}`，不要输出 no_relation、Card ID、关系说明、basis 或推理过程。
- 只有裁决为 observed 或 inferred 时，relations 才包含一条完整关系；一次核验最多输出一条关系。
- observed 关系不需要推理链，inference_mechanism 必须输出空字符串；只有 inferred 才填写最短推理机制。
- relation_kind 只能从 same_fact、same_event、confirmation、contradiction、temporal_progression、causal_influence、common_driver、constraint、market_co_movement 中选择；不要自造枚举。
- confidence 表示双方焦点原文对最终关系裁决的支持强度，范围为 0 到 1，不得使用召回或 rerank 分数。
"""

_SAME_FACT_GATE_SYSTEM_PROMPT = """你只裁决两条原子事实摘要的事实身份。

- 先将每条摘要拆成最小、不可再分的陈述性主张并输出 claims。
- 动作、状态、指标、条件、时间结论、定性结论、意义判断和所谓背景结论都必须单独计为主张，不能因其不是核心事件而忽略。
- 只有双方 claims 数量相同且逐项语义等价时，equivalent 才能为 true。
- 只共享部分主张、摘要/明细或包含关系时，equivalent 必须为 false。
- same_event 仅表示双方属于同一个可唯一识别的现实事件、公告、交易、执行动作或同一次状态观测。
- 同一事件的不同事实侧面应输出 equivalent=false、same_event=true。
- 仅同主题、同主体、同一行业、相似措辞或共享一个核心子句，不足以构成 same_event。
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
        workflow_id: str = "",
    ) -> dict[str, Any]:
        unique_ids = [item for item in dict.fromkeys(card_ids) if item]
        with langfuse_observation(
            name="kg.relation_discovery",
            as_type="chain",
            input={
                "card_ids": unique_ids,
                "adapter_name": adapter_name,
                "target": target,
                "workflow_id": workflow_id,
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
            missing_ids.extend(
                item for item in manifest_by_id if item not in source_summaries
            )
            if missing_ids:
                raise RuntimeError(
                    f"关系发现缺少 Card manifest 或 Summary: {sorted(set(missing_ids))}"
                )

            all_decisions: list[VerifiedRelationDecision] = []
            card_diagnostics: list[dict[str, Any]] = []
            persistence_checkpoints: list[dict[str, Any]] = []
            checkpointed_card_ids: list[str] = []
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
                if persist_edges and decisions:
                    with langfuse_observation(
                        name="kg.relation.card_checkpoint",
                        as_type="span",
                        input={
                            "source_card_id": card_id,
                            "decision_count": len(decisions),
                        },
                    ):
                        checkpoint = (
                            await self._relation_writer.persist_verified_decisions(
                                decisions,
                                adapter_name=adapter_name,
                                target=target,
                                pipeline_version=RELATION_DISCOVERY_PIPELINE_VERSION,
                                model_name=resolve_kg_llm_model(
                                    "kg_relation_evidence_verify"
                                ),
                                prompt_version=RELATION_VERIFICATION_PROMPT_VERSION,
                                workflow_id=workflow_id,
                            )
                        )
                        persistence_checkpoints.append(checkpoint)
                        checkpointed_card_ids.append(card_id)
                        langfuse_update_span(
                            output={
                                "source_card_id": card_id,
                                "changed_edge_ids": list(
                                    checkpoint.get("changed_edge_ids") or []
                                ),
                                "fact_id_changed_card_ids": list(
                                    checkpoint.get("fact_id_changed_card_ids") or []
                                ),
                                "graph_event_ids": list(
                                    checkpoint.get("graph_event_ids") or []
                                ),
                            },
                            status_message="completed",
                        )

            edge_persistence = (
                _merge_edge_persistence_checkpoints(
                    persistence_checkpoints,
                    checkpointed_card_ids=checkpointed_card_ids,
                    workflow_id=workflow_id,
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
                "observed": sum(
                    item.decision_class == "observed" for item in all_decisions
                ),
                "inferred": sum(
                    item.decision_class == "inferred" for item in all_decisions
                ),
                "no_relation": sum(
                    item.decision_class == "no_relation" for item in all_decisions
                ),
                "edge_persistence": edge_persistence,
                "card_diagnostics": card_diagnostics,
                "workflow_id": workflow_id,
            }
            langfuse_update_span(output=result, status_message="completed")
            return result

    async def reverify_card_pairs(
        self,
        card_pairs: list[tuple[str, str]],
        *,
        adapter_name: str = "financial",
        target: str = "prod",
        persist_edges: bool = True,
        workflow_id: str = "",
    ) -> dict[str, Any]:
        """Reclassify known pairs from original evidence without repeating recall."""

        pairs = sorted(
            {
                canonical_card_pair(left, right)
                for left, right in card_pairs
                if left and right and left != right
            }
        )
        with langfuse_observation(
            name="kg.relation_reverification",
            as_type="chain",
            input={
                "pair_count": len(pairs),
                "adapter_name": adapter_name,
                "target": target,
                "workflow_id": workflow_id,
            },
            metadata={"pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION},
        ):
            card_ids = sorted({card_id for pair in pairs for card_id in pair})
            manifests = self._repository.list_atomic_cognitive_card_manifests_by_ids(
                adapter_name,
                cognitive_card_ids=card_ids,
                status="active",
            )
            manifest_by_id = {item.cognitive_card_id: item for item in manifests}
            summaries = await self._vector_store.get_summaries(
                card_ids,
                adapter_name=adapter_name,
                target=target,
            )
            missing = sorted(
                card_id
                for card_id in card_ids
                if card_id not in manifest_by_id or card_id not in summaries
            )
            if missing:
                raise RuntimeError(
                    f"历史关系重分类缺少 Card manifest 或 Summary: {missing}"
                )

            packages = list(
                await asyncio.gather(
                    *(
                        self._load_pair_evidence(
                            source_manifest=manifest_by_id[left],
                            source_summary=summaries[left],
                            candidate_manifest=manifest_by_id[right],
                            candidate_summary=summaries[right],
                            adapter_name=adapter_name,
                            target=target,
                        )
                        for left, right in pairs
                    )
                )
            )
            decisions = await self._verify_packages(packages)
            persistence = (
                await self._relation_writer.persist_verified_decisions(
                    decisions,
                    adapter_name=adapter_name,
                    target=target,
                    pipeline_version=RELATION_DISCOVERY_PIPELINE_VERSION,
                    model_name=resolve_kg_llm_model("kg_relation_evidence_verify"),
                    prompt_version=RELATION_VERIFICATION_PROMPT_VERSION,
                    workflow_id=workflow_id,
                )
                if persist_edges
                else {"skipped": True, "reason": "persist_edges_disabled"}
            )
            result = {
                "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
                "pairs_requested": len(pairs),
                "decisions": [item.as_dict() for item in decisions],
                "same_fact": sum(
                    item.relation_kind == "same_fact" for item in decisions
                ),
                "same_event": sum(
                    item.relation_kind == "same_event" for item in decisions
                ),
                "other_relation": sum(
                    item.decision_class in {"observed", "inferred"}
                    and item.relation_kind not in {"same_fact", "same_event"}
                    for item in decisions
                ),
                "no_relation": sum(
                    item.decision_class == "no_relation" for item in decisions
                ),
                "edge_persistence": persistence,
                "workflow_id": workflow_id,
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
            fact_grouped, same_fact_candidate_ids = (
                self._group_candidates_by_existing_fact(
                    merged,
                    adapter_name=adapter_name,
                )
            )
            selected = self._select_candidate_budget(fact_grouped)
            langfuse_update_span(
                output={
                    "merged_candidates": len(merged),
                    "fact_grouped_candidates": len(fact_grouped),
                    "same_fact_candidates_collapsed": len(same_fact_candidate_ids),
                    "selected_candidates": len(selected),
                    "focus_only": sum(
                        item.recall_views == ["focus_evidence"] for item in selected
                    ),
                },
                status_message="completed",
            )
        screened_related_ids = await self._screen_candidates(
            source_card_id=manifest.cognitive_card_id,
            source_summary=source_summary,
            candidates=selected,
        )
        kept_ids, verification_budget_dropped_ids = (
            self._select_verification_budget(
                selected,
                related_candidate_ids=screened_related_ids,
            )
        )
        candidate_manifests = (
            self._repository.list_atomic_cognitive_card_manifests_by_ids(
                adapter_name,
                cognitive_card_ids=kept_ids,
                status="active",
            )
        )
        candidate_manifest_by_id = {
            item.cognitive_card_id: item for item in candidate_manifests
        }
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
            "fact_grouped_candidates": len(fact_grouped),
            "same_fact_candidates_collapsed": len(same_fact_candidate_ids),
            "same_fact_candidate_ids_collapsed": same_fact_candidate_ids,
            "screened_candidates": len(selected),
            "screen_related_candidates": len(screened_related_ids),
            "kept_for_evidence": len(kept_ids),
            "verification_budget": max(
                1, settings.KG_RELATION_VERIFY_MAX_CANDIDATES_PER_CARD
            ),
            "verification_budget_dropped": len(verification_budget_dropped_ids),
            "verification_budget_dropped_candidate_ids": (
                verification_budget_dropped_ids
            ),
            "verified_pairs": len(decisions),
            "pair_data_errors": pair_errors,
            "focus_only_candidates": sum(
                item.recall_views == ["focus_evidence"] for item in selected
            ),
            "same_chunk_excluded_candidates": len(same_chunk_excluded_ids),
            "same_chunk_excluded_candidate_ids": same_chunk_excluded_ids,
        }
        if include_evaluation_details:
            route_by_id = {item.route_id: item for item in routes}
            reranked_by_route: dict[str, list[str]] = {}
            for item in route_hits:
                reranked_by_route.setdefault(item.route_id, []).append(
                    item.candidate_card_id
                )
            diagnostics["evaluation_details"] = {
                "budgets": {
                    "recall_per_view": settings.KG_RELATION_RECALL_PER_VIEW,
                    "rerank_top_n": settings.KG_RELATION_RERANK_TOP_N,
                    "merged_candidate_limit": settings.KG_RELATION_MERGED_CANDIDATE_LIMIT,
                    "verify_max_candidates_per_card": (
                        settings.KG_RELATION_VERIFY_MAX_CANDIDATES_PER_CARD
                    ),
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
                "fact_grouped_candidate_ids": [
                    item.candidate_card_id for item in fact_grouped
                ],
                "selected_candidate_ids": [item.candidate_card_id for item in selected],
                "screened_related_candidate_ids": list(screened_related_ids),
                "verification_candidate_ids": list(kept_ids),
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
                    hit for hit in hits if hit.candidate_card_id not in excluded_ids
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
                            "summary_hits": sum(
                                item.recall_view == "summary" for item in hits
                            ),
                            "focus_hits": sum(
                                item.recall_view == "focus_evidence" for item in hits
                            ),
                            "hit_details": [asdict(item) for item in hits],
                        },
                        status_message="completed",
                    )
            langfuse_update_span(
                output={
                    route_id: {
                        "hits": len(hits),
                        "summary_hits": sum(
                            item.recall_view == "summary" for item in hits
                        ),
                        "focus_hits": sum(
                            item.recall_view == "focus_evidence" for item in hits
                        ),
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
                                    summaries[candidate_id].metadata.get(
                                        "source_published_at"
                                    )
                                    or ""
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
    def _merge_candidates(
        route_hits: list[RouteCandidateHit],
    ) -> list[MergedRelationCandidate]:
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

    @staticmethod
    def _select_verification_budget(
        candidates: list[MergedRelationCandidate],
        *,
        related_candidate_ids: list[str],
    ) -> tuple[list[str], list[str]]:
        """Cap expensive evidence verification while preserving RRF rank order."""

        related = set(related_candidate_ids)
        ranked_ids = [
            item.candidate_card_id
            for item in candidates
            if item.candidate_card_id in related
        ]
        limit = max(1, settings.KG_RELATION_VERIFY_MAX_CANDIDATES_PER_CARD)
        return ranked_ids[:limit], ranked_ids[limit:]

    def _group_candidates_by_existing_fact(
        self,
        candidates: list[MergedRelationCandidate],
        *,
        adapter_name: str,
    ) -> tuple[list[MergedRelationCandidate], list[str]]:
        """Keep the best candidate per known equivalent-fact group before screening.

        A new Card starts with a singleton fact identity. Existing reports that
        assert an equivalent atomic fact must not consume the screening budget,
        while different facets connected by same_event remain independent.
        """

        if not candidates:
            return [], []
        manifests = self._repository.list_atomic_cognitive_card_manifests_by_ids(
            adapter_name,
            cognitive_card_ids=[item.candidate_card_id for item in candidates],
            status="active",
        )
        fact_by_card_id = {
            item.cognitive_card_id: (item.fact_id or f"card:{item.cognitive_card_id}")
            for item in manifests
        }
        selected: list[MergedRelationCandidate] = []
        collapsed_card_ids: list[str] = []
        seen_fact_ids: set[str] = set()
        for item in candidates:
            fact_id = fact_by_card_id.get(
                item.candidate_card_id,
                f"card:{item.candidate_card_id}",
            )
            if fact_id in seen_fact_ids:
                collapsed_card_ids.append(item.candidate_card_id)
                continue
            seen_fact_ids.add(fact_id)
            selected.append(item)
        return selected, collapsed_card_ids

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
                    "source_published_at": source_summary.metadata.get(
                        "source_published_at"
                    )
                    or "",
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
                    max_tokens=RELATION_SCREEN_MAX_TOKENS,
                    reasoning_effort="low",
                )
                expected_ids = [item.candidate_card_id for item in batch]
                repaired_output = False
                try:
                    related_ids = _parse_screening_candidate_ids(
                        data, expected_ids=expected_ids
                    )
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
                        max_tokens=RELATION_SCREEN_MAX_TOKENS,
                        reasoning_effort="low",
                        use_cache=False,
                    )
                    related_ids = _parse_screening_candidate_ids(
                        repaired, expected_ids=expected_ids
                    )
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
                source_published_at=str(
                    source_summary.metadata.get("source_published_at") or ""
                ),
                source_card_summary=source_summary.text,
                source_chunk_summary=str(
                    source_summary.metadata.get("chunk_summary") or ""
                ),
                candidate_card_id=candidate_manifest.cognitive_card_id,
                candidate_evidence_context=candidate_context,
                candidate_focus_refs=candidate_refs,
                candidate_published_at=str(
                    candidate_summary.metadata.get("source_published_at") or ""
                ),
                candidate_card_summary=candidate_summary.text,
                candidate_chunk_summary=str(
                    candidate_summary.metadata.get("chunk_summary") or ""
                ),
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
                        max_tokens=RELATION_VERIFY_MAX_TOKENS,
                        reasoning_effort="low",
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
                            max_tokens=RELATION_VERIFY_MAX_TOKENS,
                            reasoning_effort="low",
                            use_cache=False,
                        )
                        decision = _parse_verified_decision(repaired, package)
                    if decision.relation_kind == "same_fact":
                        decision = await self._apply_same_fact_gate(
                            package=package,
                            decision=decision,
                        )
                    langfuse_update_span(
                        output=decision.as_dict(), status_message="completed"
                    )
                    return decision

        return (
            list(await asyncio.gather(*(verify(item) for item in packages)))
            if packages
            else []
        )

    async def _apply_same_fact_gate(
        self,
        *,
        package: PairEvidencePackage,
        decision: VerifiedRelationDecision,
    ) -> VerifiedRelationDecision:
        payload = {
            "source_summary": package.source_card_summary,
            "target_summary": package.candidate_card_summary,
        }
        data = await self._call_structured_llm(
            task="kg_same_fact_gate",
            system_prompt=_SAME_FACT_GATE_SYSTEM_PROMPT,
            payload=payload,
            schema=_same_fact_gate_schema(),
            max_tokens=RELATION_SAME_FACT_MAX_TOKENS,
            reasoning_effort="low",
        )
        try:
            gate = _parse_same_fact_gate(data)
        except ValueError as exc:
            repaired = await self._call_structured_llm(
                task="kg_same_fact_gate",
                system_prompt=_SAME_FACT_GATE_SYSTEM_PROMPT,
                payload={
                    **payload,
                    "repair": {
                        "validation_error": str(exc),
                        "previous_output": data,
                        "instruction": "只修复事实身份裁决的内部一致性。",
                    },
                },
                schema=_same_fact_gate_schema(),
                max_tokens=RELATION_SAME_FACT_MAX_TOKENS,
                reasoning_effort="low",
                use_cache=False,
            )
            gate = _parse_same_fact_gate(repaired)

        if gate["equivalent"]:
            return decision
        if gate["same_event"]:
            return VerifiedRelationDecision(
                source_card_id=decision.source_card_id,
                target_card_id=decision.target_card_id,
                decision_class="observed",
                relation_kind="same_event",
                relation_type="同一现实事件的不同事实侧面",
                direction="对称关系：双方描述同一现实事件的不同原子事实",
                basis=gate["basis"],
                source_evidence_refs=list(decision.source_evidence_refs),
                target_evidence_refs=list(decision.target_evidence_refs),
                inference_mechanism="",
                confidence=min(decision.confidence, 0.95),
            )
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
        cache_pipeline_version = (
            RELATION_SCREEN_CACHE_VERSION
            if task == "kg_relation_candidate_screen"
            else RELATION_DISCOVERY_PIPELINE_VERSION
        )
        provider_options: dict[str, Any] = {}
        if reasoning_effort != "disabled":
            # GLM-5.3 requires thinking to remain enabled. Relation extraction
            # is a bounded classification task, so use the lowest adaptive
            # effort and preserve the remaining output budget for JSON.
            provider_options["thinking_type"] = "enabled"
            provider_options["reasoning_effort"] = reasoning_effort
        request = LLMProxyRequest(
            model=model,
            system_prompt=system_prompt,
            prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            temperature=0,
            max_tokens=max_tokens,
            json_schema=schema,
            provider_options=provider_options,
            metadata={
                "task": task,
                "pipeline_version": RELATION_DISCOVERY_PIPELINE_VERSION,
                "_cache_key_metadata": {
                    "task": task,
                    "pipeline_version": cache_pipeline_version,
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
            "relations": {
                "type": "array",
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "source_card_id": {"type": "string", "enum": card_ids},
                        "target_card_id": {"type": "string", "enum": card_ids},
                        "decision_class": {
                            "type": "string",
                            "enum": ["observed", "inferred"],
                        },
                        "relation_kind": {
                            "type": "string",
                            "enum": sorted(RELATION_KINDS),
                        },
                        "relation_type": {"type": "string", "maxLength": 120},
                        "direction": {"type": "string", "maxLength": 120},
                        "basis": {"type": "string", "maxLength": 1000},
                        "inference_mechanism": {
                            "type": "string",
                            "maxLength": 1000,
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": [
                        "source_card_id",
                        "target_card_id",
                        "decision_class",
                        "relation_kind",
                        "relation_type",
                        "direction",
                        "basis",
                        "inference_mechanism",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["relations"],
        "additionalProperties": False,
    }


def _same_fact_gate_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "source_claims": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string", "maxLength": 120},
            },
            "target_claims": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string", "maxLength": 120},
            },
            "equivalent": {"type": "boolean"},
            "same_event": {"type": "boolean"},
            "basis": {"type": "string", "maxLength": 300},
        },
        "required": [
            "source_claims",
            "target_claims",
            "equivalent",
            "same_event",
            "basis",
        ],
        "additionalProperties": False,
    }


def _parse_same_fact_gate(data: dict[str, Any]) -> dict[str, Any]:
    source_claims = data.get("source_claims")
    target_claims = data.get("target_claims")
    equivalent = data.get("equivalent")
    same_event = data.get("same_event")
    basis = str(data.get("basis") or "").strip()
    if not isinstance(equivalent, bool) or not isinstance(same_event, bool):
        raise ValueError("事实身份门禁字段必须为 boolean")
    if not all(
        isinstance(claims, list)
        and claims
        and all(isinstance(item, str) and item.strip() for item in claims)
        for claims in (source_claims, target_claims)
    ):
        raise ValueError("事实身份门禁 claims 必须是非空字符串数组")
    if equivalent and len(source_claims) != len(target_claims):
        raise ValueError("双方主张数量不同时 equivalent 不能为 true")
    if equivalent and not same_event:
        raise ValueError("equivalent=true 时 same_event 必须为 true")
    if not basis:
        raise ValueError("事实身份门禁 basis 不能为空")
    return {
        "source_claims": [str(item).strip() for item in source_claims],
        "target_claims": [str(item).strip() for item in target_claims],
        "equivalent": equivalent,
        "same_event": same_event,
        "basis": basis,
    }


def _parse_verified_decision(
    data: dict[str, Any],
    package: PairEvidencePackage,
) -> VerifiedRelationDecision:
    relations = data.get("relations")
    if not isinstance(relations, list) or len(relations) > 1:
        raise ValueError("关系核验 relations 必须是最多包含一项的数组")
    if not relations:
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
    if not isinstance(relations[0], dict):
        raise ValueError("关系核验 relations 元素必须是对象")
    data = relations[0]
    decision_class = str(data.get("decision_class") or "")
    if decision_class not in {"observed", "inferred"}:
        raise ValueError(f"未知关系决定: {decision_class}")

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
    if relation_kind in {"same_fact", "same_event"} and decision_class != "observed":
        raise ValueError(f"{relation_kind} 只能裁决为 observed")
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
            "card_summary": package.source_card_summary,
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
            "card_summary": package.candidate_card_summary,
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
    return [
        item
        for item in dict.fromkeys(str(value) for value in values if str(value).strip())
    ]


def _merge_edge_persistence_checkpoints(
    checkpoints: list[dict[str, Any]],
    *,
    checkpointed_card_ids: list[str],
    workflow_id: str,
) -> dict[str, Any]:
    """Merge per-Card durable write results into the historical batch result shape."""

    list_fields = (
        "touched_edge_ids",
        "changed_edge_ids",
        "milvus_upserted_edge_ids",
        "milvus_deleted_edge_ids",
        "graph_event_ids",
        "affected_card_ids",
        "card_fact_ids",
        "fact_id_changed_card_ids",
    )
    result: dict[str, Any] = {field: [] for field in list_fields}
    for checkpoint in checkpoints:
        for field in list_fields:
            result[field].extend(
                str(item)
                for item in checkpoint.get(field) or []
                if str(item).strip()
            )
    for field in list_fields:
        result[field] = list(dict.fromkeys(result[field]))
    result.update(
        {
            "checkpoint_count": len(checkpoints),
            "checkpointed_card_ids": list(
                dict.fromkeys(
                    str(item)
                    for item in checkpointed_card_ids
                    if str(item).strip()
                )
            ),
            "workflow_id": str(workflow_id or "").strip(),
        }
    )
    return result


def _other_card_id(decision: VerifiedRelationDecision, source_card_id: str) -> str:
    return (
        decision.target_card_id
        if decision.source_card_id == source_card_id
        else decision.source_card_id
    )
