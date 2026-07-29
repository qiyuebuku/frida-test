"""Community Insight 高级认知报告异步刷新服务。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from src.application.services.knowledge_llm_config import resolve_kg_llm_model
from src.infrastructure.clients.embedding import EMBEDDING_MODEL, embed_texts
from src.infrastructure.connections import get_session
from src.infrastructure.db import redis_lock
from src.infrastructure.llm_proxy.service import get_llm_gateway_service
from src.infrastructure.llm_proxy.types import LLMProxyRequest, LLMProxyResponse
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_propagation_context,
    langfuse_update_span,
)
from src.infrastructure.persistence.models.knowledge import (
    KnowledgeCommunityAssignment,
    KnowledgeCommunityInsight,
    KnowledgeGraphCommunity,
)
from src.infrastructure.vector_store.milvus_hybrid_store import (
    MILVUS_COLLECTION_COMMUNITY_INSIGHT,
    MilvusHybridDocument,
    MilvusTypedHybridStore,
)

logger = logging.getLogger(__name__)

COMMUNITY_INSIGHT_LOCK_NAME = "kg:community_insight_refresh"
COMMUNITY_INSIGHT_LOCK_TTL_SECONDS = int(os.getenv("KG_COMMUNITY_INSIGHT_LOCK_TTL_SECONDS", "300"))
COMMUNITY_INSIGHT_LOCK_RENEW_SECONDS = int(os.getenv("KG_COMMUNITY_INSIGHT_LOCK_RENEW_SECONDS", "30"))
COMMUNITY_INSIGHT_STABLE_WINDOW_SECONDS = int(os.getenv("KG_COMMUNITY_INSIGHT_STABLE_WINDOW_SECONDS", "300"))
COMMUNITY_INSIGHT_SCAN_LIMIT = int(os.getenv("KG_COMMUNITY_INSIGHT_SCAN_LIMIT", "200"))
COMMUNITY_INSIGHT_BATCH_LIMIT = int(os.getenv("KG_COMMUNITY_INSIGHT_BATCH_LIMIT", "5"))
COMMUNITY_INSIGHT_MAX_MATERIALS = int(os.getenv("KG_COMMUNITY_INSIGHT_MAX_MATERIALS", "80"))
COMMUNITY_INSIGHT_MAX_TOKENS = int(os.getenv("KG_COMMUNITY_INSIGHT_MAX_TOKENS", "4500"))


_INSIGHT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["insight_full_report", "report_json"],
    "properties": {
        "insight_full_report": {"type": "string"},
        "report_json": {
            "type": "object",
            "additionalProperties": True,
        },
    },
}

COMMUNITY_INSIGHT_SYSTEM_PROMPT = "\n".join(
    [
        "你是 Community Insight 高级认知报告生成器。",
        "输出对象是检索和决策 Agent，不是普通消费者、投资者或交易员。",
        "你的任务不是总结单条新闻，也不是罗列 community 成员。",
        "报告只做一件事：把同一 community 下多条材料整合成 GraphRAG 风格的 Community Report，帮助 Agent 获得单条 chunk 难以提供的跨材料结论。",
        "",
        "事实边界：",
        "- 所有判断只能基于用户输入材料中明确提供的信息；不得引入输入之外的行情、价格、公司、时间、政策或外部知识。",
        "- 不得创造输入中不存在的时间、数字、公司名、政策名、交易结论或因果关系。",
        "- 不要把输入中的“预期、观点、研报判断”改写成已经发生的事实。",
        "",
        "写作要求：",
        "- 必须同时输出 insight_full_report 和 report_json。",
        "- report_json 是结构化 Community Report，必须包含 summary、findings、key_entities、key_relationships。",
        "- insight_full_report 是给 Agent 直接阅读的自然语言报告，必须与 report_json 的发现一致。",
        "- 报告第一段必须先给出整体判断：这个 community 下的多条材料合在一起说明了什么。",
        "- 第一段之后围绕少量关键综合观察展开；每个观察必须回答“这些材料放在一起新增了什么认知”，不能按材料顺序逐条改写。",
        "- 每个主体段落都必须体现跨材料关系，例如共同机制、递进关系、相互印证、约束条件或影响传导；如果只是把同类材料放在一起复述，需要改写成综合判断。",
        "- findings 是核心产物：每个 finding 必须是一条跨材料发现，包含 summary、explanation、supporting_sources。",
        "- finding.summary 写发现本身，不写材料标题；finding.explanation 解释多条材料之间如何共同支撑、传导、对照或约束该发现。",
        "- finding.supporting_sources 只能填输入材料中的 source ID，例如 ft_news:123；不要填原文片段。",
        "- key_relationships 写材料之间形成的核心关系链，例如“收益率下行 -> 理财认购降温”或“订单增长 -> 产能扩张 -> 产业链景气改善”。",
        "- key_entities 写当前 community 报告中最关键的公司、资产、产品、产业链环节、政策或风险对象。",
        "- 不要输出证据清单、basis 列表、材料逐条摘要或 card 抽取结果复述。",
        "- 不要把每条材料改写成一段。只保留能支撑整体结论的代表性事实。",
        "- 输出一份完整高级认知报告，不要逐条复述输入，也不要只输出口号式结论。",
        "- 输出一个围绕当前 community 的整体认知报告，不要在报告内部把材料拆成多个独立主题。",
        "- 如果材料包含多个方面，只能作为同一 community 内的证据层次、传导环节或影响对象来组织，并说明它们为什么共同支撑同一个整体判断。",
        "- 报告可以自然包含代表性证据、输入信号类型、驱动因素、传导链条、影响对象、确定性边界，但这些内容必须服务于整体总结，不要做成字段罗列。",
        "- 对代表性证据要保留可检索细节，例如关键公司、产品、技术路线、价格/订单/产能/资本开支/政策/业绩等输入中已经出现的信息。",
        "- 引用代表性证据时必须使用材料中的 source，例如 ft_news:123，不要使用材料行号或 #1/#2 这类序号引用。",
        "- 输入材料中的 evidence 是从原文精确复制的证据片段，是唯一事实载体；报告中的事实细节必须受 evidence 约束。",
        "- date、importance、signals 表示该材料的时间、重要性和关键信号；它们可用于组织报告，但不是新的事实证据。",
        "- 如果同一 community 横跨多个产业环节，需要说明这些环节之间是否存在传导关系；没有传导关系时只能说明它们是同一 community 下的不同证据面，不能拆成独立主题。",
        "- 不要把 parent_themes、broad_topics、risk_type 简单拼接成报告。",
        "- 不要把短期行情、概念股涨跌或单条研报观点写成主结论；除非输入中存在机制、供需、订单、价格或产能等支撑。",
        "- 不要输出 community 质量提示、边界风险提示或二次拆分建议；这些问题必须由上游 Assignment 解决。",
        "- 风险、约束和反转条件是条件触发的判断维度，不是固定输出章节。只有输入材料支持且有助于理解整体结论时才写；表达成中性条件，不要写成行动建议。",
        "- 不输出买入、卖出、持仓、配置、关注等建议口吻；需要表达后续变量时，写成“后续变量是……”或“约束来自……”。",
        "- 即使输入材料包含建议性措辞，也必须改写为中性事实、市场观点归因或资金行为描述，不要原样保留建议口吻。",
        "- report_json 不是证据清单；不要在 report_json 中输出完整 evidence、basis 或材料逐条摘要。",
        "",
        "内部分析步骤：",
        "- 在写报告前，先判断材料是否围绕同一稳定对象或机制。",
        "- 判断材料之间的关系类型：同向印证、上下游传导、因果链条、阶段递进、分化对照、约束/反证、弱相关背景。",
        "- 判断每条材料在整体报告中的角色：核心证据、补充证据、约束证据或背景证据。",
        "- 只把能形成跨材料新增认知的关系写入 findings 和 insight_full_report；不要展示自检过程。",
        "",
        "报告长度由输入材料复杂度自然决定。不要为了凑字数扩写；但如果 community 包含大量来源、多个强主线或多个产业环节，不要压缩成几句短行情评论。",
        "信息保真优先于短小。凡是对 Agent 后续检索、判断主题机制、识别代表性证据和限制条件有帮助的传导链条，都应该保留。",
        "请输出 JSON 对象，必须包含 insight_full_report 和 report_json。",
    ]
)


@dataclass(frozen=True)
class _InsightCandidate:
    community: KnowledgeGraphCommunity
    existing: KnowledgeCommunityInsight | None
    source_count: int
    cognitive_card_count: int
    assignment_count: int


class CommunityInsightService:
    """扫描并刷新 Community Insight 的应用层用例。"""

    def __init__(
        self,
        *,
        target: str = "prod",
        vector_store: MilvusTypedHybridStore | None = None,
    ):
        self._target = target
        self._llm = get_llm_gateway_service()
        self._vector_store = vector_store or MilvusTypedHybridStore()

    async def refresh_due_insights(
        self,
        *,
        limit: int = COMMUNITY_INSIGHT_BATCH_LIMIT,
        scan_limit: int = COMMUNITY_INSIGHT_SCAN_LIMIT,
    ) -> dict[str, Any]:
        """刷新一批需要生成或更新的 Community Insight。"""

        t0 = time.time()
        metadata = {
            "task": "kg_community_insight_refresh",
            "target": self._target,
            "limit": limit,
            "scan_limit": scan_limit,
        }
        with langfuse_propagation_context(
            trace_name="kg.community_insight.refresh_due",
            tags=["kg", "community_insight", "refresh"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.community_insight.refresh_due",
                as_type="span",
                input=metadata,
                metadata=metadata,
            ):
                try:
                    with redis_lock.acquire(COMMUNITY_INSIGHT_LOCK_NAME, ttl=COMMUNITY_INSIGHT_LOCK_TTL_SECONDS) as lock:
                        if not lock:
                            result = {
                                "skipped": True,
                                "reason": "lock_held",
                                "refreshed": 0,
                                "failed": 0,
                            }
                            langfuse_update_span(output=result, status_message="lock_held")
                            return result
                        langfuse_update_span(metadata={"lock_acquired": True})

                        stop_renew = asyncio.Event()
                        lock_lost = asyncio.Event()
                        renew_task = asyncio.create_task(_renew_lock_loop(lock, stop_renew, lock_lost))
                        try:
                            result = await self._refresh_due_insights_locked(
                                limit=limit,
                                scan_limit=scan_limit,
                                lock_lost=lock_lost,
                            )
                            result["duration_seconds"] = round(time.time() - t0, 3)
                            langfuse_update_span(output=result, status_message="completed")
                            return result
                        finally:
                            stop_renew.set()
                            await renew_task
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise

    async def refresh_community_ids(
        self,
        community_ids: list[str],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        """刷新指定 community，供验收脚本和人工诊断使用。"""

        selected_ids = [community_id for community_id in dict.fromkeys(community_ids) if community_id]
        if not selected_ids:
            return {
                "skipped": True,
                "reason": "no_community_ids",
                "refreshed": 0,
                "failed": 0,
            }

        t0 = time.time()
        metadata = {
            "task": "kg_community_insight_refresh_ids",
            "target": self._target,
            "requested": len(selected_ids),
            "force": force,
        }
        with langfuse_propagation_context(
            trace_name="kg.community_insight.refresh_ids",
            tags=["kg", "community_insight", "refresh_ids"],
            metadata=metadata,
        ):
            with langfuse_observation(
                name="kg.community_insight.refresh_ids",
                as_type="span",
                input={"community_ids": selected_ids, **metadata},
                metadata=metadata,
            ):
                try:
                    with redis_lock.acquire(COMMUNITY_INSIGHT_LOCK_NAME, ttl=COMMUNITY_INSIGHT_LOCK_TTL_SECONDS) as lock:
                        if not lock:
                            result = {
                                "skipped": True,
                                "reason": "lock_held",
                                "requested": len(selected_ids),
                                "refreshed": 0,
                                "failed": 0,
                            }
                            langfuse_update_span(output=result, status_message="lock_held")
                            return result
                        langfuse_update_span(metadata={"lock_acquired": True})

                        stop_renew = asyncio.Event()
                        lock_lost = asyncio.Event()
                        renew_task = asyncio.create_task(_renew_lock_loop(lock, stop_renew, lock_lost))
                        try:
                            candidates = self._load_candidates_by_ids(selected_ids, force=force)
                            refreshed = 0
                            skipped = 0
                            failed = 0
                            errors: list[dict[str, str]] = []
                            for candidate in candidates:
                                if lock_lost.is_set():
                                    raise RuntimeError("kg_community_insight_refresh 在指定刷新前丢失分布式锁")
                                try:
                                    if await self._refresh_one(candidate):
                                        refreshed += 1
                                    else:
                                        skipped += 1
                                except Exception as exc:
                                    failed += 1
                                    logger.exception(
                                        "[kg_community_insight_refresh] 指定 community 刷新失败: %s",
                                        candidate.community.community_id,
                                    )
                                    errors.append({"community_id": candidate.community.community_id, "error": str(exc)[:500]})
                                    self._mark_failed(candidate.community, str(exc))

                            result = {
                                "skipped": False,
                                "requested": len(selected_ids),
                                "selected": len(candidates),
                                "refreshed": refreshed,
                                "skipped_items": skipped,
                                "failed": failed,
                                "errors": errors[:10],
                                "duration_seconds": round(time.time() - t0, 3),
                            }
                            langfuse_update_span(output=result, status_message="completed")
                            return result
                        finally:
                            stop_renew.set()
                            await renew_task
                except Exception as exc:
                    langfuse_update_span(
                        metadata={"error_type": exc.__class__.__name__},
                        level="ERROR",
                        status_message=str(exc),
                    )
                    raise

    async def _refresh_due_insights_locked(
        self,
        *,
        limit: int,
        scan_limit: int,
        lock_lost: asyncio.Event,
    ) -> dict[str, Any]:
        candidates = self._load_due_candidates(limit=limit, scan_limit=scan_limit)
        refreshed = 0
        skipped = 0
        failed = 0
        errors: list[dict[str, str]] = []

        for candidate in candidates:
            if lock_lost.is_set():
                raise RuntimeError("kg_community_insight_refresh 在刷新前丢失分布式锁")
            try:
                if await self._refresh_one(candidate):
                    refreshed += 1
                else:
                    skipped += 1
            except Exception as exc:
                failed += 1
                logger.exception("[kg_community_insight_refresh] community 刷新失败: %s", candidate.community.community_id)
                errors.append({"community_id": candidate.community.community_id, "error": str(exc)[:500]})
                self._mark_failed(candidate.community, str(exc))

        return {
            "skipped": False,
            "scanned": scan_limit,
            "due": len(candidates),
            "refreshed": refreshed,
            "skipped_items": skipped,
            "failed": failed,
            "errors": errors[:10],
        }

    def _load_due_candidates(self, *, limit: int, scan_limit: int) -> list[_InsightCandidate]:
        now = datetime.now(timezone.utc)
        with langfuse_observation(
            name="kg.community_insight.pg.load_due_candidates",
            as_type="span",
            input={"limit": limit, "scan_limit": scan_limit},
        ):
            with get_session(self._target) as session:
                communities = session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(KnowledgeGraphCommunity.status == "active")
                    .order_by(KnowledgeGraphCommunity.updated_at.asc())
                    .limit(scan_limit)
                ).all()
                community_ids = [community.community_id for community in communities]
                insights = session.scalars(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id.in_(community_ids))
                ).all() if community_ids else []
                insight_by_community = {insight.community_id: insight for insight in insights}

                candidates: list[_InsightCandidate] = []
                for community in communities:
                    source_count, card_count, assignment_count = _community_counts(community)
                    if max(source_count, card_count) <= 1:
                        continue
                    existing = insight_by_community.get(community.community_id)
                    if not _community_needs_insight_refresh(community, existing, now=now):
                        continue
                    candidates.append(
                        _InsightCandidate(
                            community=community,
                            existing=existing,
                            source_count=source_count,
                            cognitive_card_count=card_count,
                            assignment_count=assignment_count,
                        )
                    )
                    if len(candidates) >= limit:
                        break
                langfuse_update_span(
                    output={
                        "scanned": len(communities),
                        "existing_insights": len(insights),
                        "selected": len(candidates),
                        "candidate_ids": [item.community.community_id for item in candidates[:20]],
                    },
                    status_message="completed",
                )
                return candidates

    def _load_candidates_by_ids(self, community_ids: list[str], *, force: bool) -> list[_InsightCandidate]:
        now = datetime.now(timezone.utc)
        with langfuse_observation(
            name="kg.community_insight.pg.load_candidates_by_ids",
            as_type="span",
            input={"requested": len(community_ids), "force": force, "community_ids": community_ids[:50]},
        ):
            with get_session(self._target) as session:
                communities = session.scalars(
                    select(KnowledgeGraphCommunity)
                    .where(KnowledgeGraphCommunity.community_id.in_(community_ids))
                    .where(KnowledgeGraphCommunity.status == "active")
                ).all()
                insights = session.scalars(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id.in_(community_ids))
                ).all()
                insight_by_community = {insight.community_id: insight for insight in insights}
                order = {community_id: index for index, community_id in enumerate(community_ids)}

                candidates: list[_InsightCandidate] = []
                for community in sorted(communities, key=lambda item: order.get(item.community_id, len(order))):
                    source_count, card_count, assignment_count = _community_counts(community)
                    if max(source_count, card_count) <= 1:
                        continue
                    existing = insight_by_community.get(community.community_id)
                    if not force and not _community_needs_insight_refresh(community, existing, now=now):
                        continue
                    candidates.append(
                        _InsightCandidate(
                            community=community,
                            existing=existing,
                            source_count=source_count,
                            cognitive_card_count=card_count,
                            assignment_count=assignment_count,
                        )
                    )
                langfuse_update_span(
                    output={
                        "loaded": len(communities),
                        "existing_insights": len(insights),
                        "selected": len(candidates),
                        "candidate_ids": [item.community.community_id for item in candidates[:20]],
                    },
                    status_message="completed",
                )
                return candidates

    async def _refresh_one(self, candidate: _InsightCandidate) -> bool:
        community = candidate.community
        metadata = {
            "community_id": community.community_id,
            "title": community.title,
            "source_count": candidate.source_count,
            "cognitive_card_count": candidate.cognitive_card_count,
            "assignment_count": candidate.assignment_count,
        }
        with langfuse_observation(
            name="kg.community_insight.refresh_one",
            as_type="span",
            input=metadata,
            metadata=metadata,
        ):
            try:
                materials = self._load_materials(community)
                if len(materials) <= 1:
                    logger.info("[kg_community_insight_refresh] community 材料不足，跳过: %s", community.community_id)
                    deactivated = self._deactivate_insight(
                        community,
                        reason="insufficient_materials",
                        materials=len(materials),
                    )
                    langfuse_update_span(
                        output={
                            "skipped": True,
                            "reason": "insufficient_materials",
                            "materials": len(materials),
                            "deactivated_existing_insight": deactivated,
                        },
                        status_message="insufficient_materials",
                    )
                    return False

                data, response = await self._generate_insight(community=community, materials=materials)
                report = str(data.get("insight_full_report") or "").strip()
                report_json = data.get("report_json") if isinstance(data.get("report_json"), dict) else {}
                retried = False
                retry_detail = _quality_retry_reason(report=report, report_json=report_json)
                if retry_detail:
                    retried = True
                    data, response = await self._generate_insight(
                        community=community,
                        materials=materials,
                        retry_reason=retry_detail,
                    )
                    report = str(data.get("insight_full_report") or "").strip()
                    report_json = data.get("report_json") if isinstance(data.get("report_json"), dict) else {}
                if len(report) < 80:
                    raise RuntimeError(f"community insight report invalid or too short: {community.community_id}; chars={len(report)}")

                insight = self._save_insight(
                    candidate,
                    materials=materials,
                    report=report,
                    report_json=report_json,
                    response=response,
                )
                await self._upsert_milvus(community=community, insight=insight)
                output = {
                    "community_id": community.community_id,
                    "insight_id": insight.insight_id,
                    "insight_version": insight.insight_version,
                    "materials": len(materials),
                    "report_chars": len(report),
                    "cache_hit": response.cache_hit,
                    "retried": retried,
                }
                langfuse_update_span(output=output, status_message="completed")
                logger.info(
                    "[kg_community_insight_refresh] 完成 community=%s version=%s report_chars=%s cache_hit=%s",
                    community.community_id,
                    insight.insight_version,
                    len(report),
                    response.cache_hit,
                )
                return True
            except Exception as exc:
                langfuse_update_span(
                    metadata={"error_type": exc.__class__.__name__},
                    level="ERROR",
                    status_message=str(exc),
                )
                raise

    async def _generate_insight(
        self,
        *,
        community: KnowledgeGraphCommunity,
        materials: list[dict[str, Any]],
        retry_reason: str = "",
    ) -> tuple[dict[str, Any], LLMProxyResponse]:
        prompt = _build_prompt(community, materials, retry_reason=retry_reason)
        metadata = {
            "task": "kg_community_insight",
            "adapter_name": community.adapter_name,
            "projection": community.projection,
            "community_id": community.community_id,
            "material_count": len(materials),
            "retry_reason": retry_reason,
        }
        model = resolve_kg_llm_model("kg_community_insight")
        response = await self._llm.generate(
            LLMProxyRequest(
                prompt=prompt,
                system_prompt=COMMUNITY_INSIGHT_SYSTEM_PROMPT,
                model=model,
                json_schema=_INSIGHT_SCHEMA,
                temperature=0.0,
                max_tokens=COMMUNITY_INSIGHT_MAX_TOKENS,
                metadata=metadata,
                provider_options={"reasoning_effort": "high"},
                use_cache=not retry_reason,
            )
        )
        data = _parse_json_object_from_response(response)
        if data is None:
            raise RuntimeError(f"community insight output is not object: {community.community_id}")
        return data, response

    def _load_materials(self, community: KnowledgeGraphCommunity) -> list[dict[str, Any]]:
        with langfuse_observation(
            name="kg.community_insight.materials.load",
            as_type="span",
            input={"community_id": community.community_id, "max_materials": COMMUNITY_INSIGHT_MAX_MATERIALS},
        ):
            with get_session(self._target) as session:
                assignments = session.scalars(
                    select(KnowledgeCommunityAssignment)
                    .where(KnowledgeCommunityAssignment.community_id == community.community_id)
                    .where(KnowledgeCommunityAssignment.adapter_name == community.adapter_name)
                    .where(KnowledgeCommunityAssignment.status == "active")
                    .order_by(
                        KnowledgeCommunityAssignment.updated_at.desc().nullslast(),
                        KnowledgeCommunityAssignment.assignment_id,
                    )
                ).all()
                if assignments:
                    raw_materials = [
                        _trim_material(_material_from_assignment(assignment))
                        for assignment in assignments
                    ]
                    grounded_raw_materials = _filter_grounded_materials(raw_materials)
                    materials = _dedupe_materials(grounded_raw_materials)[:COMMUNITY_INSIGHT_MAX_MATERIALS]
                    langfuse_update_span(
                        output={
                            "source": "pg.assignments.topic_intent",
                            "assignments": len(assignments),
                            "materials": len(materials),
                            "raw_materials": len(raw_materials),
                            "grounded_raw_materials": len(grounded_raw_materials),
                            "dropped_without_evidence": len(raw_materials) - len(grounded_raw_materials),
                            "deduped_grounded_materials": len(materials),
                        },
                        status_message="completed",
                    )
                    return materials
            langfuse_update_span(
                output={
                    "source": "no_assignment_materials",
                    "materials": 0,
                },
                status_message="completed",
            )
            return []

    def _save_insight(
        self,
        candidate: _InsightCandidate,
        *,
        materials: list[dict[str, Any]],
        report: str,
        report_json: dict[str, Any],
        response: LLMProxyResponse,
    ) -> KnowledgeCommunityInsight:
        community = candidate.community
        now = datetime.now(timezone.utc)
        insight_id = _insight_id(community.community_id)
        material_stats = _prompt_material_stats(materials)
        evidence_ids = _dedupe_strings(
            material.get("evidence_id")
            for material in materials
            if material.get("evidence_id")
        )
        chunk_ids = _dedupe_strings(
            chunk_id
            for material in materials
            for chunk_id in [
                *(material.get("chunk_ids") or []),
                material.get("primary_chunk_id"),
            ]
            if chunk_id
        )
        cognitive_card_ids = _dedupe_strings(
            material.get("cognitive_card_id")
            for material in materials
            if material.get("cognitive_card_id")
        )
        with langfuse_observation(
            name="kg.community_insight.pg.save",
            as_type="span",
            input={
                "community_id": community.community_id,
                "insight_id": insight_id,
                "report_chars": len(report),
            },
        ):
            with get_session(self._target) as session:
                existing = session.scalar(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id == community.community_id)
                )
                created = existing is None
                if existing is None:
                    existing = KnowledgeCommunityInsight(
                        insight_id=insight_id,
                        community_id=community.community_id,
                        adapter_name=community.adapter_name,
                        projection=community.projection,
                        insight_version=1,
                    )
                    session.add(existing)
                else:
                    existing.insight_version = int(existing.insight_version or 0) + 1

                existing.title = community.title
                existing.insight_full_report = report
                existing.report_json = report_json
                existing.source_count = material_stats["source_count"]
                existing.cognitive_card_count = material_stats["cognitive_card_count"]
                existing.assignment_count = material_stats["assignment_count"]
                existing.evidence_ids = evidence_ids
                existing.chunk_ids = chunk_ids
                existing.cognitive_card_ids = cognitive_card_ids
                existing.status = "active"
                existing.error_message = ""
                existing.payload = {
                    "llm_usage": dict(response.usage or {}),
                    "cache_hit": response.cache_hit,
                    "model": resolve_kg_llm_model("kg_community_insight"),
                    "generated_at": now.isoformat(),
                    "quality_diagnostics": _quality_diagnostics(report=report, report_json=report_json),
                }
                existing.updated_at = now

                db_community = session.get(KnowledgeGraphCommunity, community.community_id)
                if db_community is not None:
                    db_community.last_insight_generated_at = now

                session.flush()
                session.refresh(existing)
                langfuse_update_span(
                    output={
                        "created": created,
                        "insight_id": existing.insight_id,
                        "insight_version": existing.insight_version,
                        "quality_diagnostics": existing.payload.get("quality_diagnostics"),
                    },
                    status_message="completed",
                )
                session.expunge(existing)
                return existing

    def _deactivate_insight(self, community: KnowledgeGraphCommunity, *, reason: str, materials: int) -> bool:
        insight_id = _insight_id(community.community_id)
        now = datetime.now(timezone.utc)
        with langfuse_observation(
            name="kg.community_insight.deactivate",
            as_type="span",
            input={
                "community_id": community.community_id,
                "insight_id": insight_id,
                "reason": reason,
                "materials": materials,
            },
        ):
            with get_session(self._target) as session:
                existing = session.scalar(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id == community.community_id)
                )
                if existing is None:
                    langfuse_update_span(output={"deactivated": False, "reason": "not_found"}, status_message="not_found")
                    return False
                existing.status = "inactive"
                existing.error_message = reason
                payload = existing.payload if isinstance(existing.payload, dict) else {}
                existing.payload = {
                    **payload,
                    "deactivated_at": now.isoformat(),
                    "deactivated_reason": reason,
                    "deactivated_material_count": materials,
                }
                existing.updated_at = now
                db_community = session.get(KnowledgeGraphCommunity, community.community_id)
                if db_community is not None:
                    db_community.last_insight_generated_at = now

            self._vector_store.delete_documents_by_role(
                collection_role=MILVUS_COLLECTION_COMMUNITY_INSIGHT,
                adapter_name=community.adapter_name,
                target=self._target,
                target_ids=[insight_id],
            )
            langfuse_update_span(output={"deactivated": True, "milvus_deleted": True}, status_message="completed")
            return True

    def _mark_failed(self, community: KnowledgeGraphCommunity, error: str) -> None:
        with langfuse_observation(
            name="kg.community_insight.pg.mark_failed",
            as_type="span",
            input={"community_id": community.community_id, "error": error[:500]},
            level="ERROR",
        ):
            with get_session(self._target) as session:
                existing = session.scalar(
                    select(KnowledgeCommunityInsight).where(KnowledgeCommunityInsight.community_id == community.community_id)
                )
                created = existing is None
                if existing is None:
                    existing = KnowledgeCommunityInsight(
                        insight_id=_insight_id(community.community_id),
                        community_id=community.community_id,
                        adapter_name=community.adapter_name,
                        projection=community.projection,
                        title=community.title,
                        status="error",
                    )
                    session.add(existing)
                existing.status = "error"
                existing.error_message = error[:2000]
                langfuse_update_span(
                    output={"created": created, "status": "error"},
                    level="ERROR",
                    status_message=error[:500],
                )

    async def _upsert_milvus(self, *, community: KnowledgeGraphCommunity, insight: KnowledgeCommunityInsight) -> None:
        with langfuse_observation(
            name="kg.community_insight.milvus.upsert",
            as_type="span",
            input={
                "community_id": community.community_id,
                "insight_id": insight.insight_id,
                "collection_role": MILVUS_COLLECTION_COMMUNITY_INSIGHT,
                "report_chars": len(insight.insight_full_report or ""),
            },
        ):
            search_text = _insight_retrieval_text(insight)
            document = MilvusHybridDocument(
                chunk_id=insight.insight_id,
                evidence_id=insight.evidence_ids[0] if insight.evidence_ids else "",
                text=search_text,
                metadata={
                    "target_id": insight.insight_id,
                    "target_type": "community_insight",
                    "document_type": "community_insight",
                    "source_type": "kg_community_insight",
                    "source_id": community.community_id,
                    "community_id": community.community_id,
                    "community_title": community.title,
                    "projection": community.projection,
                    "insight_version": insight.insight_version,
                    "source_count": insight.source_count,
                    "cognitive_card_count": insight.cognitive_card_count,
                    "assignment_count": insight.assignment_count,
                    "insight_full_report_chars": len(insight.insight_full_report or ""),
                    "retrieval_text_chars": len(search_text),
                    "retrieval_text_source": "insight_report_json_and_full_report",
                    "cited_evidence_ids": insight.evidence_ids,
                    "cited_chunk_ids": insight.chunk_ids,
                    "cognitive_card_ids": insight.cognitive_card_ids,
                    "latest_evidence_at": (community.metrics or {}).get("latest_source_published_at") or "",
                    "earliest_evidence_at": (community.metrics or {}).get("earliest_source_published_at") or "",
                    "event_time_start": (community.metrics or {}).get("earliest_source_published_at") or "",
                    "event_time_end": (community.metrics or {}).get("latest_source_published_at") or "",
                },
            )
            vectors = await embed_texts([document.text])
            if len(vectors) != 1:
                raise RuntimeError(f"community insight embedding failed: {community.community_id}")
            self._vector_store.upsert_documents_by_role(
                adapter_name=community.adapter_name,
                target=self._target,
                documents_by_role={MILVUS_COLLECTION_COMMUNITY_INSIGHT: [document]},
                vectors_by_role={MILVUS_COLLECTION_COMMUNITY_INSIGHT: vectors},
                embedding_model=EMBEDDING_MODEL,
                kg_version="community_insight_v1",
            )
            langfuse_update_span(
                output={
                    "documents": 1,
                    "vectors": len(vectors),
                    "retrieval_text_chars": len(search_text),
                    "embedding_model": EMBEDDING_MODEL,
                    "collection_role": MILVUS_COLLECTION_COMMUNITY_INSIGHT,
                },
                status_message="completed",
            )


async def _renew_lock_loop(lock, stop_event: asyncio.Event, lost_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=COMMUNITY_INSIGHT_LOCK_RENEW_SECONDS)
        except asyncio.TimeoutError:
            renewed = lock.renew()
            if renewed:
                logger.info("[kg_community_insight_refresh] 分布式锁续租成功 ttl=%ss", COMMUNITY_INSIGHT_LOCK_TTL_SECONDS)
                continue
            logger.error("[kg_community_insight_refresh] 分布式锁续租失败，当前锁持有者可能已经变化")
            lost_event.set()
            return


def _community_counts(community: KnowledgeGraphCommunity) -> tuple[int, int, int]:
    metrics = community.metrics or {}
    source_count = _int_metric(metrics.get("unique_source_count") or metrics.get("source_count"), len(community.evidence_ids or []))
    card_count = _int_metric(
        metrics.get("cognitive_card_count") or metrics.get("assigned_intent_count"),
        len(metrics.get("cognitive_card_ids") or []),
    )
    assignment_count = _int_metric(metrics.get("assignment_count") or metrics.get("assigned_intent_count"), 0)
    return source_count, card_count, assignment_count


def _community_needs_insight_refresh(
    community: KnowledgeGraphCommunity,
    existing: KnowledgeCommunityInsight | None,
    *,
    now: datetime,
) -> bool:
    if existing is None:
        return True
    generated_at = community.last_insight_generated_at or existing.updated_at
    if generated_at is None:
        return True
    updated_at = community.updated_at
    if updated_at is None or updated_at <= generated_at:
        return False
    if existing.status != "active":
        age_seconds = (now - updated_at).total_seconds()
        return age_seconds >= COMMUNITY_INSIGHT_STABLE_WINDOW_SECONDS
    age_seconds = (now - updated_at).total_seconds()
    return age_seconds >= COMMUNITY_INSIGHT_STABLE_WINDOW_SECONDS


def _build_prompt(
    community: KnowledgeGraphCommunity,
    materials: list[dict[str, Any]],
    *,
    retry_reason: str = "",
) -> str:
    metrics = community.metrics or {}
    material_stats = _prompt_material_stats(materials)
    payload = {
        "community": {
            "community_id": community.community_id,
            "title": community.title,
            "scope": metrics.get("scope") or "",
            "source_count": material_stats["source_count"],
            "cognitive_card_count": material_stats["cognitive_card_count"],
            "assignment_count": material_stats["assignment_count"],
            "time_range": material_stats["time_range"],
        },
        "community_signals": _material_community_signal_summary(materials),
        "materials_format": (
            "materials 是核心认知材料数组。每个对象使用报告写作字段：source=来源ID，"
            "date=发布日期，evidence=从原文精确复制的证据片段，"
            "importance=材料重要性，signals=少量关键驱动/影响/风险信号。"
        ),
        "materials": _insight_materials_payload(materials),
    }
    if retry_reason:
        payload["retry"] = {
            "reason": retry_reason,
            "instruction": "本次需要输出有效报告，但不要为了篇幅扩写。",
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _prompt_material_stats(materials: list[dict[str, Any]]) -> dict[str, Any]:
    source_ids = [
        _material_identity(item)
        for item in materials
        if _material_identity(item)
    ]
    card_ids = [
        _clean_context_text(item.get("cognitive_card_id"))
        for item in materials
        if _clean_context_text(item.get("cognitive_card_id"))
    ]
    assignment_ids = [
        _clean_context_text(item.get("assignment_id"))
        for item in materials
        if _clean_context_text(item.get("assignment_id"))
    ]
    published_times = sorted(
        {
            _clean_context_text(item.get("source_published_at"))
            for item in materials
            if _clean_context_text(item.get("source_published_at"))
        },
    )
    return {
        "source_count": len(set(source_ids)),
        "cognitive_card_count": len(set(card_ids)),
        "assignment_count": len(set(assignment_ids)),
        "time_range": {
            "start": published_times[0] if published_times else "",
            "end": published_times[-1] if published_times else "",
        },
    }


def _material_community_signal_summary(materials: list[dict[str, Any]]) -> dict[str, Any]:
    topic_profile = _dedupe_strings(
        value
        for material in materials
        for value in [
            *(material.get("parent_themes") or []),
            *(material.get("broad_topics") or []),
        ]
    )[:12]
    key_targets = _dedupe_strings(
        value
        for material in materials
        for value in (material.get("impact_target") or [])
    )[:12]
    risk_hints = _dedupe_strings(
        value
        for material in materials
        for value in (material.get("risk_type") or [])
    )[:8]
    result = {
        "topic_profile": topic_profile,
        "key_targets": key_targets,
        "risk_hints": risk_hints,
    }
    return {key: value for key, value in result.items() if value}


def _dedupe_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_context_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _insight_materials_payload(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for material in materials:
        item = {
            "source": _display_source_id(material),
            "date": _date_only(material.get("source_published_at")),
            "evidence": _clean_context_text(material.get("evidence_span")),
            "importance": _material_importance(material),
            "signals": _insight_material_signals(material),
        }
        payload.append({key: value for key, value in item.items() if value not in ("", None, [])})
    return payload


def _material_importance(material: dict[str, Any]) -> Any:
    event_classification = material.get("event_classification") if isinstance(material.get("event_classification"), dict) else {}
    if event_classification.get("importance") not in (None, ""):
        return event_classification.get("importance")
    if material.get("importance") not in (None, ""):
        return material.get("importance")
    return None


def _insight_material_signals(material: dict[str, Any]) -> dict[str, list[str]]:
    signals = {
        "drivers": _compact_list(material.get("driver"), item_limit=3, item_chars=40),
        "targets": _compact_list(material.get("impact_target"), item_limit=3, item_chars=40),
        "risks": _compact_list(material.get("risk_type"), item_limit=3, item_chars=40),
    }
    return {key: value for key, value in signals.items() if value}


def _date_only(value: Any) -> str:
    text = _clean_context_text(value)
    if "T" in text:
        return text.split("T", 1)[0]
    return text[:10] if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-" else text


def _display_source_id(material: dict[str, Any]) -> str:
    """给 LLM 的可引用来源 ID，优先归一到真实业务来源。"""

    identity = _material_identity(material)
    if identity:
        return identity
    return _clean_context_text(material.get("source_id"))


def _compact_text(value: Any, limit: int) -> str:
    text = str(value or "").replace("\n", " ").replace("\r", " ").strip()
    if limit <= 0:
        return text
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _compact_list(value: Any, *, item_limit: int, item_chars: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _compact_text(item, item_chars)
        if text:
            result.append(text)
        if len(result) >= item_limit:
            break
    return result


def _ordered_unique_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _community_signal_summary(metrics: dict[str, Any]) -> dict[str, Any]:
    topic_profile = _ordered_unique_texts(
        [
            *_string_items(metrics.get("parent_themes")),
            *_string_items(metrics.get("topic_tags")),
        ]
    )
    return {
        "topic_profile": topic_profile[:12],
        "key_targets": _string_items(metrics.get("impact_tags"))[:12],
        "risk_hints": _string_items(metrics.get("risk_tags"))[:5],
        "maturity_level": metrics.get("maturity_level") or "",
        "topic_diversity_count": metrics.get("topic_diversity_count") or 0,
        "high_weight_assignment_count": metrics.get("high_weight_assignment_count") or 0,
        "medium_weight_assignment_count": metrics.get("medium_weight_assignment_count") or 0,
        "low_weight_assignment_count": metrics.get("low_weight_assignment_count") or 0,
    }


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value for text in [_compact_text(item, 40)] if text]


def _quality_diagnostics(*, report: str, report_json: dict[str, Any]) -> dict[str, Any]:
    summary = str(report_json.get("summary") or "").strip()
    findings = _report_findings(report_json)
    key_relationships = _string_items(report_json.get("key_relationships"))
    warnings: list[str] = []
    if len(report.strip()) < 120:
        warnings.append("report_too_short_to_use")
    if not summary:
        warnings.append("report_json_missing_summary")
    if not findings:
        warnings.append("report_json_missing_findings")
    if findings and not any(len(_string_items(item.get("supporting_sources"))) >= 2 for item in findings):
        warnings.append("findings_lack_cross_material_support")
    if not key_relationships:
        warnings.append("report_json_missing_key_relationships")
    if _looks_like_material_rewrite(report):
        warnings.append("report_looks_like_material_rewrite")
    if _looks_like_advice(report):
        warnings.append("report_contains_advice_tone")
    if _looks_like_category_listing(report):
        warnings.append("report_looks_like_category_listing")
    return {
        "report_chars": len(report),
        "summary_chars": len(summary),
        "finding_count": len(findings),
        "key_relationship_count": len(key_relationships),
        "warnings": warnings,
    }


def _quality_retry_reason(*, report: str, report_json: dict[str, Any]) -> str:
    diagnostics = _quality_diagnostics(report=report, report_json=report_json)
    warnings = diagnostics.get("warnings") or []
    if not warnings:
        return ""
    guidance = {
        "report_too_short_to_use": "上一版报告过短，未形成可交付给 Agent 的跨材料认知报告",
        "report_json_missing_summary": "上一版 report_json 缺少 summary，需要输出 GraphRAG 风格社区摘要",
        "report_json_missing_findings": "上一版 report_json 缺少 findings，需要输出跨材料发现",
        "findings_lack_cross_material_support": "上一版 findings 缺少由多个来源共同支撑的发现，需要分析材料之间的关系",
        "report_json_missing_key_relationships": "上一版 report_json 缺少 key_relationships，需要输出材料之间的核心关系链",
        "report_looks_like_material_rewrite": "上一版报告像材料逐条改写，需要改成跨材料综合判断",
        "report_contains_advice_tone": "上一版报告包含建议口吻，需要改成中性事实、市场观点归因或资金行为描述",
        "report_looks_like_category_listing": "上一版报告像分类汇总，需要说明跨材料关系和新增认知",
    }
    details = [guidance.get(str(item), str(item)) for item in warnings]
    return "；".join(details)


def _insight_retrieval_text(insight: KnowledgeCommunityInsight) -> str:
    """索引最终报告和 GraphRAG 风格发现，方便 Agent 直接命中高阶认知。"""

    report_json = insight.report_json if isinstance(insight.report_json, dict) else {}
    lines = [
        f"Community Insight: {insight.title or insight.community_id}",
        f"Summary: {report_json.get('summary') or ''}",
        _findings_text(report_json),
        _relationships_text(report_json),
        _entities_text(report_json),
        "",
        str(insight.insight_full_report or "").strip(),
    ]
    return "\n".join(line for line in lines if line is not None).strip()


def _report_findings(report_json: dict[str, Any]) -> list[dict[str, Any]]:
    value = report_json.get("findings")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _findings_text(report_json: dict[str, Any]) -> str:
    lines: list[str] = []
    for index, finding in enumerate(_report_findings(report_json), start=1):
        summary = _clean_context_text(finding.get("summary"))
        explanation = _clean_context_text(finding.get("explanation"))
        sources = "、".join(_string_items(finding.get("supporting_sources"))[:8])
        if summary or explanation:
            lines.append(f"Finding {index}: {summary}。{explanation} Sources: {sources}".strip())
    return "\n".join(lines)


def _relationships_text(report_json: dict[str, Any]) -> str:
    relationships = _string_items(report_json.get("key_relationships"))
    if not relationships:
        return ""
    return "Key Relationships: " + "；".join(relationships)


def _entities_text(report_json: dict[str, Any]) -> str:
    entities = _string_items(report_json.get("key_entities"))
    if not entities:
        return ""
    return "Key Entities: " + "；".join(entities)


def _looks_like_material_rewrite(report: str) -> bool:
    text = report.strip()
    if not text:
        return False
    source_refs = text.count("ft_news:")
    bullet_count = sum(1 for line in text.splitlines() if line.strip().startswith(("-", "*", "1.", "2.", "3.")))
    return source_refs >= 6 and bullet_count >= 4


def _looks_like_advice(report: str) -> bool:
    advice_terms = (
        "建议投资者",
        "投资者应",
        "投资者需",
        "消费者应",
        "建议关注",
        "可关注",
        "配置价值",
        "持仓建议",
    )
    return any(term in report for term in advice_terms)


def _looks_like_category_listing(report: str) -> bool:
    text = report.strip()
    if not text:
        return False
    connector_terms = (
        "共同",
        "同时",
        "反映",
        "显示",
        "指向",
        "因此",
        "从而",
        "驱动",
        "传导",
        "印证",
        "约束",
        "形成",
    )
    ordinal_count = len(re.findall(r"(第一|第二|第三|第四|首先|其次|再次|最后)[，,]", text))
    connector_count = sum(text.count(term) for term in connector_terms)
    return ordinal_count >= 3 and connector_count < ordinal_count * 2


def _dedupe_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按原始来源去重，避免同一新闻被 demo/session source_id 包装后重复参与总结。"""

    selected: dict[str, dict[str, Any]] = {}
    for material in materials:
        key = _material_identity(material)
        if not key:
            key = f"intent:{material.get('intent_id') or id(material)}"
        existing = selected.get(key)
        if existing is None or _material_quality_score(material) > _material_quality_score(existing):
            selected[key] = material
    return list(selected.values())


def _filter_grounded_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insight 只使用具备精确原文证据的材料。"""

    return [material for material in materials if _has_evidence(material)]


def _has_evidence(material: dict[str, Any]) -> bool:
    return bool(_clean_context_text(material.get("evidence_span")))


def _material_identity(material: dict[str, Any]) -> str:
    for key in ("source_id", "evidence_id"):
        value = str(material.get(key) or "")
        match = re.search(r"ft_news:\d+", value)
        if match:
            return match.group(0)
    return str(material.get("source_id") or "").strip()


def _material_quality_score(material: dict[str, Any]) -> tuple[float, float, float, float]:
    evidence_len = len(str(material.get("evidence_span") or ""))
    delta_len = len(str(material.get("insight_delta") or ""))
    weight = _float_or_zero(material.get("assignment_weight"))
    confidence = _float_or_zero(material.get("assignment_confidence"))
    return float(evidence_len), float(delta_len), weight, confidence


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _intent_contexts(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    contexts: dict[str, dict[str, Any]] = {}
    for item in value:
        if not isinstance(item, dict):
            continue
        context = _intent_material_context(item)
        for key in _assignment_context_keys(item):
            if key and key not in contexts:
                contexts[key] = context
    return contexts


def _material_from_metric_assignment(item: dict[str, Any], intent_contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    intent_context: dict[str, Any] = {}
    for key in _assignment_context_keys(item):
        if key in intent_contexts:
            intent_context = intent_contexts[key]
            break
    assignment_context = _assignment_context(item)
    material = {**intent_context, **assignment_context}
    evidence_span = _clean_context_text(item.get("evidence_span")) or _clean_context_text(material.get("evidence_span"))
    insight_delta = _clean_context_text(item.get("insight_delta")) or _clean_context_text(material.get("insight_delta"))
    material.update(
        {
            "assignment_id": _clean_context_text(item.get("assignment_id")),
            "cognitive_card_id": _clean_context_text(item.get("cognitive_card_id") or material.get("cognitive_card_id")),
            "intent_id": _clean_context_text(item.get("intent_id") or material.get("intent_id")),
            "evidence_span": evidence_span,
            "insight_delta": insight_delta,
        }
    )
    return material


def _material_from_assignment(assignment: KnowledgeCommunityAssignment) -> dict[str, Any]:
    topic_intent = assignment.topic_intent if isinstance(assignment.topic_intent, dict) else {}
    decision_assignment = _decision_assignment_for_row(assignment)
    evidence_span = _clean_context_text(decision_assignment.get("evidence_span")) or _clean_context_text(
        topic_intent.get("evidence_span")
    )
    insight_delta = _clean_context_text(decision_assignment.get("insight_delta"))
    return {
        **_intent_material_context(topic_intent),
        "assignment_id": assignment.assignment_id,
        "evidence_span": evidence_span,
        "insight_delta": insight_delta,
        "intent_id": assignment.intent_id,
        "cognitive_card_id": assignment.cognitive_card_id,
        **_assignment_context(
            {
                "action": assignment.action,
                "weight": assignment.weight,
                "confidence": assignment.confidence,
                "fit_type": _clean_context_text(decision_assignment.get("fit_type")) or _fit_type_from_reason(assignment.reason),
                "reason": assignment.reason,
                "resolved_community_id": assignment.community_id,
                "assignment_id": assignment.assignment_id,
            }
        ),
    }


def _decision_assignment_for_row(assignment: KnowledgeCommunityAssignment) -> dict[str, Any]:
    decision = assignment.decision if isinstance(assignment.decision, dict) else {}
    for item in decision.get("assignments") or []:
        if not isinstance(item, dict):
            continue
        if _clean_context_text(item.get("assignment_id")) == assignment.assignment_id:
            return item
        if _clean_context_text(item.get("community_id")) == assignment.community_id:
            return item
    return {}


def _intent_material_context(item: dict[str, Any]) -> dict[str, Any]:
    evidence_span = _clean_context_text(item.get("evidence_span"))
    return {
        "cognitive_card_id": _clean_context_text(item.get("cognitive_card_id")),
        "intent_id": _clean_context_text(item.get("intent_id")),
        "source_id": _clean_context_text(item.get("source_id")),
        "evidence_id": _clean_context_text(item.get("evidence_id")),
        "chunk_ids": item.get("chunk_ids") if isinstance(item.get("chunk_ids"), list) else [],
        "primary_chunk_id": _clean_context_text(item.get("primary_chunk_id")),
        "source_published_at": _clean_context_text(item.get("source_published_at")),
        "title_candidate": _clean_context_text(item.get("title_candidate") or item.get("raw_theme")),
        "evidence_span": evidence_span,
        "evidence_support": item.get("evidence_support"),
        "event_classification": item.get("event_classification") if isinstance(item.get("event_classification"), dict) else {},
        "parent_themes": item.get("parent_themes") if isinstance(item.get("parent_themes"), list) else [],
        "broad_topics": item.get("broad_topics") if isinstance(item.get("broad_topics"), list) else [],
        "mid_topics": item.get("mid_topics") if isinstance(item.get("mid_topics"), list) else [],
        "event_thread": item.get("event_thread") if isinstance(item.get("event_thread"), list) else [],
        "driver": item.get("driver") if isinstance(item.get("driver"), list) else [],
        "event_action": item.get("event_action") if isinstance(item.get("event_action"), list) else [],
        "impact_target": item.get("impact_target") if isinstance(item.get("impact_target"), list) else [],
        "risk_type": item.get("risk_type") if isinstance(item.get("risk_type"), list) else [],
        "importance": item.get("importance"),
    }


def _assignment_context(item: dict[str, Any]) -> dict[str, Any]:
    action = _clean_context_text(item.get("action"))
    return {
        "assignment_action": action,
        "assignment_relation_type": _assignment_relation_type(action),
        "assignment_weight": item.get("weight"),
        "assignment_confidence": item.get("confidence"),
        "assignment_fit_type": _clean_context_text(item.get("fit_type")) or _fit_type_from_reason(str(item.get("reason") or "")),
        "assignment_reason": _clean_context_text(item.get("reason")),
        "assignment_resolved_community_id": _clean_context_text(
            item.get("resolved_community_id") or item.get("community_id")
        ),
        "assignment_absorb_community_ids": item.get("absorb_community_ids") if isinstance(item.get("absorb_community_ids"), list) else [],
        "assignment_absorbed_from_community_id": _clean_context_text(item.get("absorbed_from_community_id")),
    }


def _assignment_context_keys(item: dict[str, Any]) -> list[str]:
    intent_id = str(item.get("intent_id") or "").strip()
    card_id = str(item.get("cognitive_card_id") or "").strip()
    intent_index = item.get("intent_index")
    keys = []
    if intent_id:
        keys.append(f"intent:{intent_id}")
        return keys
    if card_id and intent_index not in (None, ""):
        keys.append(f"card_intent:{card_id}:{intent_index}")
        return keys
    if card_id:
        keys.append(f"card:{card_id}")
    return keys


def _fit_type_from_reason(reason: str) -> str:
    prefix = "fit_type="
    if not reason.startswith(prefix):
        return ""
    value = reason[len(prefix) :].split(";", 1)[0].strip()
    return value


def _assignment_relation_type(action: str) -> str:
    if action == "create_new":
        return "created_new_topic"
    if action == "attach_existing":
        return "attached_to_existing_topic"
    if action == "create_parent_and_absorb_existing":
        return "created_parent_and_absorbed_existing_topics"
    return action or ""


def _clean_context_text(value: Any) -> str:
    return str(value or "").strip()[:500]


def _trim_material(item: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "assignment_id",
        "assignment_action",
        "assignment_relation_type",
        "assignment_weight",
        "assignment_confidence",
        "assignment_fit_type",
        "assignment_reason",
        "assignment_resolved_community_id",
        "assignment_absorb_community_ids",
        "assignment_absorbed_from_community_id",
        "cognitive_card_id",
        "intent_id",
        "source_id",
        "evidence_id",
        "chunk_ids",
        "primary_chunk_id",
        "source_published_at",
        "assignment_weight",
        "fit_type",
        "title_candidate",
        "evidence_span",
        "evidence_support",
        "event_classification",
        "insight_delta",
        "parent_themes",
        "broad_topics",
        "mid_topics",
        "event_thread",
        "driver",
        "event_action",
        "impact_target",
        "risk_type",
        "importance",
    }
    result: dict[str, Any] = {}
    for key in allowed:
        if key not in item:
            continue
        value = item.get(key)
        if value in (None, "", [], {}):
            continue
        result[key] = _clip_value(value)
    return result


def _clip_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:500]
    if isinstance(value, list):
        return [_clip_value(item) for item in value[:12]]
    if isinstance(value, dict):
        return {str(key): _clip_value(item) for key, item in list(value.items())[:20]}
    return value


def _parse_json_object_from_response(response: LLMProxyResponse) -> dict[str, Any] | None:
    if isinstance(response.structured_output, dict):
        return response.structured_output
    if isinstance(response.structured_output, str):
        return _parse_json_object(response.structured_output)
    return _parse_json_object(response.text)


def _parse_json_object(text: str | None) -> dict[str, Any] | None:
    candidate = str(text or "").strip()
    if not candidate:
        return None
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _insight_id(community_id: str) -> str:
    return f"kgi:{community_id}"[:220]


def _int_metric(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback
