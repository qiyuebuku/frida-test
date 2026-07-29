"""Fact-report and conditional-projection use cases for Graph Communities."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Awaitable, Callable

import redis

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.domain.knowledge.relation_graph_cognition import (
    FACT_REPORT_GENERATOR_VERSION,
    FACT_REPORT_JSON_SCHEMA,
    PROJECTION_GENERATOR_VERSION,
    PROJECTION_JSON_SCHEMA,
    CommunityCardMaterial,
    CommunityCognitionMaterial,
    CommunityEdgeMaterial,
    ConditionalProjection,
    fact_semantic_version,
    parse_conditional_projections,
    parse_fact_report,
    projection_semantic_version,
    projection_target_id,
    render_projection_text,
)
from src.infrastructure.clients.embedding import embed_texts
from src.infrastructure.config import settings
from src.infrastructure.llm_proxy import (
    LLMGatewayService,
    LLMProxyRequest,
    get_llm_gateway_service,
)
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.relation_graph_community_repository import (
    CommunityDerivationSnapshot,
    RelationGraphCommunityRepository,
)
from src.infrastructure.tasks.jettask_dispatcher import (
    send_kg_graph_community_projections,
)
from src.infrastructure.vector_store.milvus_hybrid_store import (
    MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION,
    MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT,
    MilvusHybridDocument,
    MilvusTypedHybridStore,
)
from src.infrastructure.vector_store.relation_candidate_store import (
    MilvusRelationCandidateStore,
)


logger = logging.getLogger(__name__)

COMMUNITY_COGNITION_LOCK_TTL_SECONDS = 900
COMMUNITY_COGNITION_LOCK_BLOCKING_TIMEOUT_SECONDS = 900
COMMUNITY_COGNITION_LOCK_RENEW_SECONDS = 60

FACT_REPORT_SYSTEM_PROMPT = """你负责根据已经完成原文核验的关系子图，生成 Graph Community 事实性高级认知报告。

输入中的 Card 是原子事实或来源观点，relations.observed 是原文已明确支持的关系，relations.inferred 是根据两端事实推断出的可能关系。你的职责是综合这些已知材料，不是重新发现关系、重新分类或修复成员。

要求：
1. 输出一份连续、自然、可直接交付给 Agent 阅读的报告，重点说明多个节点共同揭示的状态、演化、印证、冲突、约束和传导，不要按 Card 顺序逐条复述。
2. 只能使用输入中的 Card 和 Edge；不得补充外部知识、隐藏前提、新事件、新关系或主题标签。
3. 先综合 Card 直接陈述的事实，再组织 observed 关系，最后把 inferred 关系作为可能的解释或联系融入对应段落。仅由 inferred 支持的关系，每次出现都使用“可能”“或与……有关”“可视为共同背景”等边界清晰的措辞。
4. 禁止先把 inferred 关系写成确定事实，再在报告末尾集中补免责声明；也不要单独设置“推断关系说明”段落。无法自然保留推断边界时，宁可只并列相关事实。
5. Card 中的机构判断、作者观点和条件性表述必须保持其来源属性，不得改写为无条件客观事实。
6. 事实报告不得包含尚未发生的未来预测、投资建议、交易指令或确定性承诺。
7. 报告应覆盖完整关系子图，但不为长度重复信息；小型子图可以简洁，复杂子图应完整。
8. source_context 中相同 source_alias 表示同一条采集来源记录拆出的多个 Card，不能算作多来源交叉印证；不同 source_alias 对同一事实提供一致材料时，可以表达为“当前输入中的多条来源记录集中报道或相互印证”，但不得仅凭来源数量断言客观社会热度、市场热度或事实真实性。
9. Card 的 fact_card_count 大于 1 表示底层有多张 Card 陈述可互相替代的同一原子事实。它可以作为材料覆盖信号，但不是新增事实，也不能单独证明市场热度或真实性；报告只陈述一次事实，必要时补充“多条材料重复报道”。
10. 同一事实的多个来源快照包含重叠的主体清单、数值或表现时，先合并共同事实，再只写差异带来的认知增量，例如覆盖范围扩大、数值变化或时间推进；不要按来源逐份复述清单。来源间没有增量时，一次事实陈述加一句来源覆盖说明即可。
11. 完整覆盖关系子图是指保留所有不同的事实和关系模式，不是为每条 Edge 写一句话。多条 Edge 若只是把同一个共同驱动、印证、时序或约束模式应用到不同 Card 对，应聚合成一次整体说明，不得在报告末尾逐对枚举。referenced_edge_aliases 负责完整记录这些被聚合的 Edge。
12. inferred 关系的自然语言只能压缩或改写输入已有的 basis 与 inference_mechanism，不得新增“避险情绪”“资金轮动”等输入未提供的潜在驱动或解释。
13. 先完成 report_text，再据此生成最短充分的 title。title 只能组合 Card 直接陈述或 observed 关系支持的事实；若标题中的谓词依赖 inferred 关系，必须使用“与”“同期”“可能关联”等非确定性表达，不得使用“驱动”“引发”“导致”等确定性因果谓词。不要把报告中的全部结果串成标题。
14. alias 只用于 referenced_card_aliases 和 referenced_edge_aliases；title、report_text 中不得出现 source alias、Card alias、Edge alias 或稳定 ID。
15. referenced_card_aliases 和 referenced_edge_aliases 必须各自完整列出输入中的全部 alias，不得遗漏、虚构或重复。
16. 只输出 JSON Schema 规定的字段。"""

PROJECTION_SYSTEM_PROMPT = """你负责在已经校验并发布的 Graph Community 事实报告基础上，生成条件性未来推演。

这是封闭世界的关系链延伸，不是开放式预测。预测不能反向修改事实图。先判断当前输入是否已经给出可延续的状态变化、方向、约束或传导机制；缺少完整路径时返回空 projections。

要求：
1. 每条推演必须沿用输入中已经存在的“条件状态 -> 关系机制 -> 结果对象”。只允许把既有结果状态表达为继续、增强、减弱、维持或反转，不得创建输入中没有出现的新事件类型、处置结果、主体行为或状态。
2. conditions 只能复述输入中已经存在且需要继续成立的状态，不得把未来可能发生的新动作、新政策、新结论或“没有出现新因素”设为条件。
3. possible_result 的主体、对象和变化维度必须已经出现在 Card、Edge 或事实报告中。常识上合理但输入没有提供的后果也必须省略。
4. observation_indicators 只能把输入中已有的指标、数量、状态或结果方向改写成后续可观察信号；不得发明新的公告、处罚、技术指标、数值阈值、行为动作或示例。
5. invalidation_conditions 只能是 conditions 的停止、反转，或输入中已知约束的触发；不得新增融资、重组、救助、政策调整等输入外解决方案。
6. 机构、分析师或行业人士的观点仍是观点。以观点为条件时，结果只能延续该观点已经指向的对象和方向，不能据此新增资产价格、公司行动或政策结果。
7. inferred Edge 只能作为带推断边界的机制依据，不能被包装为已确认事实；不确定性必须保留在 conditional_judgement 和 possible_result 中。
8. time_horizon 只能沿用输入中的明确时间信息；输入没有时间范围时填写“未明确”，不得自行估计天、月、季度或年份。
9. alias 只用于 supporting_card_aliases 和 supporting_edge_aliases；其他自然语言字段不得出现 alias、Card ID 或 Edge ID。
10. 只能引用输入中的 Card 和 Edge alias，不得引入 Community 外部事实、行业常识或新的因果关系。
11. 不输出伪精确概率、投资建议、买卖指令或确定性承诺。
12. 每条 projection 必须代表不同的未来结果路径。相同结果对象朝同一方向变化时必须合并，不得因条件或支持材料不同而拆分。
13. 不为了填充结构而机械生成预测。无法同时满足以上要求时，空数组是正确结果。
14. 只输出 JSON Schema 规定的字段。"""


ProjectionDispatcher = Callable[[list[dict]], Awaitable[list[str]]]


class RelationGraphCommunityCognitionService:
    """Generate, publish and version current Community cognition."""

    def __init__(
        self,
        *,
        target: str = "prod",
        repository: RelationGraphCommunityRepository | Any | None = None,
        llm: LLMGatewayService | Any | None = None,
        card_store: MilvusRelationCandidateStore | Any | None = None,
        vector_store: MilvusTypedHybridStore | Any | None = None,
        redis_client: Any | None = None,
        projection_dispatcher: ProjectionDispatcher | None = None,
    ) -> None:
        self._target = target
        self._repository = repository or RelationGraphCommunityRepository(
            target=target  # type: ignore[arg-type]
        )
        self._llm = llm or get_llm_gateway_service()
        self._card_store = card_store
        self._vector_store = vector_store
        self._redis = redis_client
        self._projection_dispatcher = (
            projection_dispatcher or send_kg_graph_community_projections
        )

    async def generate_fact_report(
        self,
        *,
        community_id: str,
        expected_graph_fingerprint: str,
    ) -> dict[str, Any]:
        async with self._community_lock(community_id):
            fact_ready = False
            snapshot = await asyncio.to_thread(
                self._repository.load_derivation_snapshot,
                community_id=community_id,
                expected_graph_fingerprint=expected_graph_fingerprint,
            )
            if snapshot is None:
                return {
                    "status": "skipped",
                    "reason": "community_missing_or_graph_changed",
                    "community_id": community_id,
                    "expected_graph_fingerprint": expected_graph_fingerprint,
                }
            try:
                material = await self._build_material(snapshot)
                report_version = snapshot.fact_report_version
                reused = (
                    snapshot.fact_report
                    and snapshot.fact_report_graph_fingerprint
                    == expected_graph_fingerprint
                    and snapshot.fact_report_generator_version
                    == FACT_REPORT_GENERATOR_VERSION
                )
                if not reused:
                    claimed = await asyncio.to_thread(
                        self._repository.mark_fact_generating,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                    )
                    if not claimed:
                        return {
                            "status": "skipped",
                            "reason": "graph_changed_before_generation",
                            "community_id": community_id,
                        }
                    report = await self._generate_fact(material)
                    report_version = await asyncio.to_thread(
                        self._repository.save_fact_report,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        title=report.title,
                        report_text=report.report_text,
                        referenced_card_ids=list(report.referenced_card_ids),
                        referenced_edge_ids=list(report.referenced_edge_ids),
                        generator_version=FACT_REPORT_GENERATOR_VERSION,
                    )
                    if report_version is None:
                        return {
                            "status": "skipped",
                            "reason": "graph_changed_before_fact_save",
                            "community_id": community_id,
                        }
                    snapshot = await self._reload_snapshot(
                        community_id,
                        expected_graph_fingerprint,
                    )
                semantic_version = fact_semantic_version(
                    graph_fingerprint=expected_graph_fingerprint,
                    report_version=report_version,
                )
                if snapshot.fact_semantic_synced_version != semantic_version:
                    await self._publish_fact(snapshot)
                    marked = await asyncio.to_thread(
                        self._repository.mark_fact_semantic_ready,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        report_version=report_version,
                        semantic_version=semantic_version,
                    )
                    if not marked:
                        await self._delete_fact_targets(
                            snapshot.adapter_name,
                            [community_id],
                        )
                        return {
                            "status": "skipped",
                            "reason": "graph_changed_during_fact_publish",
                            "community_id": community_id,
                        }
                    snapshot = await self._reload_snapshot(
                        community_id,
                        expected_graph_fingerprint,
                    )
                elif snapshot.fact_report_status != "ready":
                    await asyncio.to_thread(
                        self._repository.mark_fact_semantic_ready,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        report_version=report_version,
                        semantic_version=semantic_version,
                    )
                    snapshot = await self._reload_snapshot(
                        community_id,
                        expected_graph_fingerprint,
                    )
                fact_ready = snapshot.fact_report_status == "ready"

                event_ids: list[str] = []
                if (
                    snapshot.projection_task_dispatched_version
                    < snapshot.fact_report_version
                ):
                    event_ids = await self._projection_dispatcher(
                        [
                            {
                                "community_id": community_id,
                                "graph_fingerprint": expected_graph_fingerprint,
                                "fact_report_version": snapshot.fact_report_version,
                            }
                        ]
                    )
                    if not event_ids:
                        raise RuntimeError(
                            f"Community projection task 投递失败: {community_id}"
                        )
                    marked = await asyncio.to_thread(
                        self._repository.mark_projection_task_dispatched,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        fact_report_version=snapshot.fact_report_version,
                    )
                    if not marked:
                        return {
                            "status": "skipped",
                            "reason": "graph_changed_after_projection_dispatch",
                            "community_id": community_id,
                            "event_ids": event_ids,
                        }
                return {
                    "status": "completed",
                    "community_id": community_id,
                    "graph_fingerprint": expected_graph_fingerprint,
                    "fact_report_version": snapshot.fact_report_version,
                    "fact_report_chars": len(snapshot.fact_report),
                    "title": snapshot.title,
                    "reused_report": bool(reused),
                    "projection_event_ids": event_ids,
                }
            except Exception as exc:
                if not fact_ready:
                    await asyncio.to_thread(
                        self._repository.mark_fact_failed,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        error=str(exc),
                    )
                raise

    async def generate_projection(
        self,
        *,
        community_id: str,
        expected_graph_fingerprint: str,
        expected_fact_report_version: int,
    ) -> dict[str, Any]:
        async with self._community_lock(community_id):
            snapshot = await asyncio.to_thread(
                self._repository.load_derivation_snapshot,
                community_id=community_id,
                expected_graph_fingerprint=expected_graph_fingerprint,
            )
            if (
                snapshot is None
                or snapshot.fact_report_status != "ready"
                or snapshot.fact_report_graph_fingerprint
                != expected_graph_fingerprint
                or snapshot.fact_report_version != expected_fact_report_version
            ):
                return {
                    "status": "skipped",
                    "reason": "fact_report_missing_or_changed",
                    "community_id": community_id,
                    "expected_fact_report_version": expected_fact_report_version,
                }
            try:
                material = await self._build_material(snapshot)
                reused = (
                    snapshot.projection_graph_fingerprint
                    == expected_graph_fingerprint
                    and snapshot.projection_fact_report_version
                    == expected_fact_report_version
                    and snapshot.projection_generator_version
                    == PROJECTION_GENERATOR_VERSION
                    and snapshot.projection_status
                    in {"publishing", "ready", "empty"}
                )
                if not reused:
                    claimed = await asyncio.to_thread(
                        self._repository.mark_projection_generating,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        fact_report_version=expected_fact_report_version,
                    )
                    if not claimed:
                        return {
                            "status": "skipped",
                            "reason": "fact_report_changed_before_projection",
                            "community_id": community_id,
                        }
                    projections = await self._generate_projections(
                        material,
                        snapshot=snapshot,
                    )
                    projection_version = await asyncio.to_thread(
                        self._repository.save_projections,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        fact_report_version=expected_fact_report_version,
                        projections=[item.as_dict() for item in projections],
                        generator_version=PROJECTION_GENERATOR_VERSION,
                    )
                    if projection_version is None:
                        return {
                            "status": "skipped",
                            "reason": "fact_report_changed_before_projection_save",
                            "community_id": community_id,
                        }
                    snapshot = await self._reload_snapshot(
                        community_id,
                        expected_graph_fingerprint,
                    )
                projection_version = snapshot.projection_version
                semantic_version = projection_semantic_version(
                    graph_fingerprint=expected_graph_fingerprint,
                    fact_report_version=expected_fact_report_version,
                    projection_version=projection_version,
                )
                empty = not snapshot.conditional_projections
                if snapshot.projection_semantic_synced_version != semantic_version:
                    if empty:
                        await self._delete_projection_targets(
                            snapshot.adapter_name,
                            [community_id],
                        )
                    else:
                        await self._publish_projection(snapshot)
                    marked = await asyncio.to_thread(
                        self._repository.mark_projection_semantic_ready,
                        community_id=community_id,
                        graph_fingerprint=expected_graph_fingerprint,
                        fact_report_version=expected_fact_report_version,
                        projection_version=projection_version,
                        semantic_version=semantic_version,
                        empty=empty,
                    )
                    if not marked:
                        await self._delete_projection_targets(
                            snapshot.adapter_name,
                            [community_id],
                        )
                        return {
                            "status": "skipped",
                            "reason": "graph_changed_during_projection_publish",
                            "community_id": community_id,
                        }
                return {
                    "status": "completed",
                    "community_id": community_id,
                    "graph_fingerprint": expected_graph_fingerprint,
                    "fact_report_version": expected_fact_report_version,
                    "projection_version": projection_version,
                    "projection_count": len(snapshot.conditional_projections),
                    "projection_status": "empty" if empty else "ready",
                    "reused_projection": bool(reused),
                }
            except Exception as exc:
                await asyncio.to_thread(
                    self._repository.mark_projection_failed,
                    community_id=community_id,
                    graph_fingerprint=expected_graph_fingerprint,
                    error=str(exc),
                )
                raise

    async def delete_stale_targets(
        self,
        *,
        adapter_name: str,
        fact_community_ids: list[str],
        projection_community_ids: list[str],
    ) -> None:
        await asyncio.gather(
            self._delete_fact_targets(adapter_name, fact_community_ids),
            self._delete_projection_targets(
                adapter_name,
                projection_community_ids,
            ),
        )

    async def _build_material(
        self,
        snapshot: CommunityDerivationSnapshot,
    ) -> CommunityCognitionMaterial:
        with langfuse_observation(
            name="kg.community.materials.build",
            as_type="span",
            input={
                "community_id": snapshot.community_id,
                "graph_fingerprint": snapshot.graph_fingerprint,
                "card_count": len(snapshot.member_card_ids),
                "edge_count": len(snapshot.member_edge_ids),
            },
        ):
            summaries = await self._candidate_store().get_summaries(
                list(snapshot.member_card_ids),
                adapter_name=snapshot.adapter_name,
                target=self._target,
            )
            missing = sorted(set(snapshot.member_card_ids) - set(summaries))
            if missing:
                raise ValueError(
                    f"Community Card Summary 在 Milvus 中缺失: {missing}"
                )
            degree = {card_id: 0 for card_id in snapshot.member_card_ids}
            for edge in snapshot.edges:
                degree[edge.source_card_id] += 1
                degree[edge.target_card_id] += 1
            ordered_card_ids = sorted(
                snapshot.member_card_ids,
                key=lambda card_id: (-degree[card_id], card_id),
            )
            card_alias = {
                card_id: f"c{index:04d}"
                for index, card_id in enumerate(ordered_card_ids, start=1)
            }
            card_record_by_id = {
                card.card_id: card for card in snapshot.cards
            }
            source_alias_by_identity: dict[tuple[str, str], str] = {}
            source_alias_by_card: dict[str, str] = {}
            for card_id in ordered_card_ids:
                record = card_record_by_id[card_id]
                source_identity = _source_identity(record)
                source_alias = source_alias_by_identity.setdefault(
                    source_identity,
                    f"s{len(source_alias_by_identity) + 1:04d}",
                )
                source_alias_by_card[card_id] = source_alias
            cards = tuple(
                CommunityCardMaterial(
                    alias=card_alias[card_id],
                    card_id=card_id,
                    summary=_required_summary(summaries[card_id].text, card_id),
                    source_alias=source_alias_by_card[card_id],
                    source_published_at=_published_at(
                        summaries[card_id].metadata
                    ),
                    fact_card_count=card_record_by_id[
                        card_id
                    ].fact_card_count,
                )
                for card_id in ordered_card_ids
            )
            ordered_edges = sorted(
                snapshot.edges,
                key=lambda edge: (
                    0 if edge.decision_class == "observed" else 1,
                    edge.relation_kind,
                    edge.edge_id,
                ),
            )
            edges = tuple(
                CommunityEdgeMaterial(
                    alias=f"e{index:04d}",
                    edge_id=edge.edge_id,
                    source_card_alias=card_alias[edge.source_card_id],
                    target_card_alias=card_alias[edge.target_card_id],
                    relation_kind=edge.relation_kind,
                    relation_type=edge.relation_type,
                    direction=edge.direction,
                    decision_class=edge.decision_class,
                    basis=edge.basis,
                    inference_mechanism=edge.inference_mechanism,
                )
                for index, edge in enumerate(ordered_edges, start=1)
            )
            result = CommunityCognitionMaterial(
                community_id=snapshot.community_id,
                adapter_name=snapshot.adapter_name,
                graph_fingerprint=snapshot.graph_fingerprint,
                graph_version=snapshot.graph_version,
                cards=cards,
                edges=edges,
            )
            langfuse_update_span(
                output={
                    "cards": len(cards),
                    "edges": len(edges),
                    "source_records": len(source_alias_by_identity),
                    "observed_edges": sum(
                        item.decision_class == "observed" for item in edges
                    ),
                    "inferred_edges": sum(
                        item.decision_class == "inferred" for item in edges
                    ),
                },
                status_message="completed",
            )
            return result

    async def _generate_fact(
        self,
        material: CommunityCognitionMaterial,
    ):
        payload = material.fact_payload()
        with langfuse_observation(
            name="kg.community.fact_report.generate",
            as_type="span",
            input=payload,
        ):
            response = await self._llm.generate(
                LLMProxyRequest(
                    model=resolve_kg_llm_model("kg_graph_community_report"),
                    system_prompt=FACT_REPORT_SYSTEM_PROMPT,
                    prompt=json.dumps(payload, ensure_ascii=False, indent=2),
                    temperature=0,
                    max_tokens=settings.KG_GRAPH_COMMUNITY_REPORT_MAX_TOKENS,
                    json_schema=FACT_REPORT_JSON_SCHEMA,
                    metadata={
                        "task": "kg_graph_community_report",
                        "community_id": material.community_id,
                        "graph_fingerprint": material.graph_fingerprint,
                        "generator_version": FACT_REPORT_GENERATOR_VERSION,
                        "_cache_key_metadata": {
                            "task": "kg_graph_community_report",
                            "generator_version": FACT_REPORT_GENERATOR_VERSION,
                        },
                    },
                    use_cache=True,
                )
            )
            output = _structured_object(
                response.structured_output,
                "kg_graph_community_report",
            )
            report = parse_fact_report(output, material)
            langfuse_update_span(
                output={
                    **output,
                    "usage": dict(response.usage or {}),
                    "model": resolve_kg_llm_model(
                        "kg_graph_community_report"
                    ),
                },
                status_message="completed",
            )
            return report

    async def _generate_projections(
        self,
        material: CommunityCognitionMaterial,
        *,
        snapshot: CommunityDerivationSnapshot,
    ) -> tuple[ConditionalProjection, ...]:
        payload = material.projection_payload(
            title=snapshot.title,
            fact_report=snapshot.fact_report,
        )
        with langfuse_observation(
            name="kg.community.projection.generate",
            as_type="span",
            input=payload,
        ):
            response = await self._llm.generate(
                LLMProxyRequest(
                    model=resolve_kg_llm_model(
                        "kg_graph_community_projection"
                    ),
                    system_prompt=PROJECTION_SYSTEM_PROMPT,
                    prompt=json.dumps(payload, ensure_ascii=False, indent=2),
                    temperature=0,
                    max_tokens=(
                        settings.KG_GRAPH_COMMUNITY_PROJECTION_MAX_TOKENS
                    ),
                    json_schema=PROJECTION_JSON_SCHEMA,
                    metadata={
                        "task": "kg_graph_community_projection",
                        "community_id": material.community_id,
                        "graph_fingerprint": material.graph_fingerprint,
                        "fact_report_version": snapshot.fact_report_version,
                        "generator_version": PROJECTION_GENERATOR_VERSION,
                        "_cache_key_metadata": {
                            "task": "kg_graph_community_projection",
                            "generator_version": PROJECTION_GENERATOR_VERSION,
                        },
                    },
                    use_cache=True,
                )
            )
            output = _structured_object(
                response.structured_output,
                "kg_graph_community_projection",
            )
            projections = parse_conditional_projections(output, material)
            langfuse_update_span(
                output={
                    **output,
                    "usage": dict(response.usage or {}),
                    "model": resolve_kg_llm_model(
                        "kg_graph_community_projection"
                    ),
                },
                status_message="completed",
            )
            return projections

    async def _publish_fact(
        self,
        snapshot: CommunityDerivationSnapshot,
    ) -> None:
        if not snapshot.title or not snapshot.fact_report:
            raise ValueError("事实报告为空，不能发布到 Milvus")
        text = f"# {snapshot.title}\n\n{snapshot.fact_report}".strip()
        document = MilvusHybridDocument(
            chunk_id=snapshot.community_id,
            text=text,
            metadata={
                "target_id": snapshot.community_id,
                "target_type": "community_fact_report",
                "document_type": "community_fact_report",
                "source_type": "kg_graph_community_fact_report",
                "source_id": snapshot.community_id,
                "community_id": snapshot.community_id,
                "community_title": snapshot.title,
                "graph_fingerprint": snapshot.graph_fingerprint,
                "graph_version": snapshot.graph_version,
                "fact_report_version": snapshot.fact_report_version,
                "cognitive_card_ids": list(snapshot.member_card_ids),
                "edge_ids": list(snapshot.member_edge_ids),
            },
        )
        await self._upsert_document(
            role=MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT,
            adapter_name=snapshot.adapter_name,
            document=document,
            kg_version=FACT_REPORT_GENERATOR_VERSION,
        )

    async def _publish_projection(
        self,
        snapshot: CommunityDerivationSnapshot,
    ) -> None:
        projections = tuple(
            _stored_projection(item)
            for item in snapshot.conditional_projections
        )
        text = render_projection_text(snapshot.title, projections)
        target_id = projection_target_id(snapshot.community_id)
        document = MilvusHybridDocument(
            chunk_id=target_id,
            text=text,
            metadata={
                "target_id": target_id,
                "target_type": "community_conditional_projection",
                "document_type": "community_conditional_projection",
                "source_type": "kg_graph_community_projection",
                "source_id": snapshot.community_id,
                "community_id": snapshot.community_id,
                "community_title": snapshot.title,
                "graph_fingerprint": snapshot.graph_fingerprint,
                "graph_version": snapshot.graph_version,
                "fact_report_version": snapshot.fact_report_version,
                "projection_version": snapshot.projection_version,
                "cognitive_card_ids": list(snapshot.member_card_ids),
                "edge_ids": list(snapshot.member_edge_ids),
            },
        )
        await self._upsert_document(
            role=MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION,
            adapter_name=snapshot.adapter_name,
            document=document,
            kg_version=PROJECTION_GENERATOR_VERSION,
        )

    async def _upsert_document(
        self,
        *,
        role: str,
        adapter_name: str,
        document: MilvusHybridDocument,
        kg_version: str,
    ) -> None:
        vectors = await embed_texts([document.text])
        if len(vectors) != 1 or not vectors[0]:
            raise RuntimeError(f"Community embedding 失败: {document.target_id}")
        await asyncio.to_thread(
            self._semantic_store().upsert_documents_by_role,
            adapter_name=adapter_name,
            target=self._target,
            documents_by_role={role: [document]},
            vectors_by_role={role: vectors},
            embedding_model=settings.EMBEDDING_MODEL,
            kg_version=kg_version,
        )

    async def _delete_fact_targets(
        self,
        adapter_name: str,
        community_ids: list[str],
    ) -> None:
        identities = _ordered_unique(community_ids)
        if not identities:
            return
        await asyncio.to_thread(
            self._semantic_store().delete_documents_by_role,
            collection_role=MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT,
            adapter_name=adapter_name,
            target=self._target,
            target_ids=identities,
        )

    async def _delete_projection_targets(
        self,
        adapter_name: str,
        community_ids: list[str],
    ) -> None:
        identities = [
            projection_target_id(item)
            for item in _ordered_unique(community_ids)
        ]
        if not identities:
            return
        await asyncio.to_thread(
            self._semantic_store().delete_documents_by_role,
            collection_role=MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION,
            adapter_name=adapter_name,
            target=self._target,
            target_ids=identities,
        )

    async def _reload_snapshot(
        self,
        community_id: str,
        graph_fingerprint: str,
    ) -> CommunityDerivationSnapshot:
        snapshot = await asyncio.to_thread(
            self._repository.load_derivation_snapshot,
            community_id=community_id,
            expected_graph_fingerprint=graph_fingerprint,
        )
        if snapshot is None:
            raise RuntimeError(
                f"Community 在派生过程中发生变化: {community_id}"
            )
        return snapshot

    def _candidate_store(self) -> MilvusRelationCandidateStore:
        if self._card_store is None:
            self._card_store = MilvusRelationCandidateStore()
        return self._card_store

    def _semantic_store(self) -> MilvusTypedHybridStore:
        if self._vector_store is None:
            self._vector_store = MilvusTypedHybridStore()
            self._vector_store.ensure_ready()
        return self._vector_store

    def _community_lock(self, community_id: str) -> "_RenewingRedisLock":
        return _RenewingRedisLock(
            self._redis_client().lock(
                (
                    f"{settings.JETTASK_PREFIX}:lock:"
                    f"kg_graph_community_cognition:{community_id}"
                ),
                timeout=COMMUNITY_COGNITION_LOCK_TTL_SECONDS,
                blocking_timeout=(
                    COMMUNITY_COGNITION_LOCK_BLOCKING_TIMEOUT_SECONDS
                ),
                thread_local=False,
            )
        )

    def _redis_client(self) -> Any:
        if self._redis is None:
            self._redis = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
            )
        return self._redis


class _RenewingRedisLock:
    def __init__(self, lock: Any) -> None:
        self._lock = lock
        self._stop = asyncio.Event()
        self._renew_task: asyncio.Task | None = None

    async def __aenter__(self) -> "_RenewingRedisLock":
        acquired = await asyncio.to_thread(
            self._lock.acquire,
            blocking=True,
            blocking_timeout=(
                COMMUNITY_COGNITION_LOCK_BLOCKING_TIMEOUT_SECONDS
            ),
        )
        if not acquired:
            raise TimeoutError("Community cognition lock 获取超时")
        self._renew_task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._renew_task is not None:
            self._renew_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._renew_task
        with suppress(Exception):
            await asyncio.to_thread(self._lock.release)

    async def _renew_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(COMMUNITY_COGNITION_LOCK_RENEW_SECONDS)
            if self._stop.is_set():
                return
            extended = await asyncio.to_thread(
                self._lock.extend,
                COMMUNITY_COGNITION_LOCK_TTL_SECONDS,
                replace_ttl=True,
            )
            if not extended:
                raise RuntimeError("Community cognition lock 续租失败")


def _structured_object(value: Any, task: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if (
        isinstance(value, list)
        and len(value) == 1
        and isinstance(value[0], dict)
    ):
        return value[0]
    raise ValueError(
        f"{task} 顶层输出必须是 JSON object, actual={type(value).__name__}"
    )


def _required_summary(value: str, card_id: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Card Summary 为空: {card_id}")
    return text


def _source_identity(card: Any) -> tuple[str, str]:
    source_id = str(card.source_id or "").strip()
    if source_id:
        return str(card.source_type or "").strip(), source_id
    primary_chunk_id = str(card.primary_chunk_id or "").strip()
    if primary_chunk_id:
        return "chunk", primary_chunk_id
    return "card", str(card.card_id)


def _published_at(metadata: dict[str, Any]) -> str:
    return str(
        metadata.get("source_published_at")
        or metadata.get("published_at")
        or ""
    ).strip()


def _stored_projection(value: dict) -> ConditionalProjection:
    return ConditionalProjection(
        conditional_judgement=str(
            value.get("conditional_judgement") or ""
        ).strip(),
        conditions=tuple(
            str(item).strip()
            for item in value.get("conditions") or []
            if str(item).strip()
        ),
        possible_result=str(value.get("possible_result") or "").strip(),
        observation_indicators=tuple(
            str(item).strip()
            for item in value.get("observation_indicators") or []
            if str(item).strip()
        ),
        invalidation_conditions=tuple(
            str(item).strip()
            for item in value.get("invalidation_conditions") or []
            if str(item).strip()
        ),
        time_horizon=str(value.get("time_horizon") or "").strip(),
        supporting_card_ids=tuple(
            str(item)
            for item in value.get("supporting_card_ids") or []
            if item
        ),
        supporting_edge_ids=tuple(
            str(item)
            for item in value.get("supporting_edge_ids") or []
            if item
        ),
    )


def _ordered_unique(values: list[str]) -> list[str]:
    return [
        item
        for item in dict.fromkeys(str(value).strip() for value in values)
        if item
    ]
