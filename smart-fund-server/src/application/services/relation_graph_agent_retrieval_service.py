"""Agent-facing retrieval over verified Cognitive Card relationship graphs."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Any

from src.infrastructure.clients.reranker import RerankerClient
from src.infrastructure.config import settings
from src.infrastructure.connections.database import Target
from src.infrastructure.observability.langfuse_tracing import (
    langfuse_observation,
    langfuse_update_span,
)
from src.infrastructure.persistence.repositories.relation_graph_agent_repository import (
    AgentGraphCardRecord,
    AgentGraphCommunityRecord,
    AgentGraphCommunityRelationRecord,
    AgentGraphEdgeRecord,
    RelationGraphAgentRepository,
)
from src.infrastructure.vector_store.relation_candidate_store import (
    MilvusRelationCandidateStore,
    RelationCardSearchHit,
    RelationCardText,
)


class RelationGraphAgentRetrievalService:
    """Stable Tool Kernel for Card, Edge, and Community retrieval operations."""

    def __init__(
        self,
        *,
        target: Target = "prod",
        repository: RelationGraphAgentRepository | Any | None = None,
        card_store: MilvusRelationCandidateStore | Any | None = None,
        reranker: RerankerClient | Any | None = None,
    ) -> None:
        self.target = target
        self._repository = repository or RelationGraphAgentRepository(
            target=target
        )
        self._card_store = card_store
        self._reranker = reranker or RerankerClient()

    async def search(
        self,
        *,
        query: str,
        adapter_name: str = "financial",
        seed_limit: int = 8,
        candidate_limit: int = 32,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_query = _required_text(query, "query")
        adapter = _required_text(adapter_name, "adapter_name")
        _bounded(seed_limit, "seed_limit", 1, 30)
        _bounded(candidate_limit, "candidate_limit", seed_limit, 100)
        with langfuse_observation(
            name="kg.relation_graph_agent.search",
            as_type="tool",
            input={
                "query": normalized_query,
                "adapter_name": adapter,
                "target": self.target,
                "seed_limit": seed_limit,
                "candidate_limit": candidate_limit,
                "time_start": _iso(time_start),
                "time_end": _iso(time_end),
            },
        ):
            store, owns_store = await self._card_store_for_call()
            try:
                recall_limit = min(100, candidate_limit * 2)
                recalled = await store.search_cards(
                    normalized_query,
                    adapter_name=adapter,
                    target=self.target,
                    limit=recall_limit,
                    time_start=time_start,
                    time_end=time_end,
                )
                active_cards = await asyncio.to_thread(
                    self._repository.load_cards,
                    adapter_name=adapter,
                    card_ids=[hit.card_id for hit in recalled],
                )
                active_ids = {card.card_id for card in active_cards}
                active_card_by_id = {
                    card.card_id: card
                    for card in active_cards
                }
                active_recalled = [
                    hit
                    for hit in recalled
                    if hit.card_id in active_ids and hit.summary.strip()
                ][:candidate_limit]
                reranked = await self._rerank_cards(
                    normalized_query,
                    active_recalled,
                    top_n=candidate_limit,
                )
                ranked, duplicate_fact_count = _group_ranked_cards_by_fact(
                    reranked,
                    card_by_id=active_card_by_id,
                    limit=seed_limit,
                )
                seed_ids = [item["hit"].card_id for item in ranked]
                selected_fact_ids = [
                    active_card_by_id[card_id].fact_id
                    for card_id in seed_ids
                    if card_id in active_card_by_id
                    and active_card_by_id[card_id].fact_id
                ]
                fact_card_counts = await asyncio.to_thread(
                    self._repository.load_fact_card_counts,
                    adapter_name=adapter,
                    fact_ids=selected_fact_ids,
                )
                snapshot = await asyncio.to_thread(
                    self._repository.load_subgraph,
                    adapter_name=adapter,
                    seed_card_ids=seed_ids,
                    hop_limit=0,
                    node_limit=seed_limit,
                    edge_limit=1,
                    relation_kinds=[],
                    decision_classes=["observed", "inferred"],
                    min_confidence=0.0,
                )
                summary_by_id = await store.get_summaries(
                    [card.card_id for card in snapshot.cards],
                    adapter_name=adapter,
                    target=self.target,
                )
            finally:
                if owns_store:
                    await asyncio.to_thread(store.store.close)

            rank_by_id = {
                item["hit"].card_id: {
                    "retrieval_rank": rank,
                    "rerank_score": item["rerank_score"],
                    "matched_views": list(item["hit"].matched_views),
                }
                for rank, item in enumerate(ranked, start=1)
            }
            community_ids_by_card = _community_ids_by_card(
                snapshot.communities
            )
            community_ids_by_card.update(
                {
                    card_id: list(community_ids)
                    for card_id, community_ids
                    in snapshot.community_ids_by_card_id.items()
                }
            )
            cards = [
                _card_payload(
                    card,
                    summary=summary_by_id.get(card.card_id),
                    focus=None,
                    community_ids=community_ids_by_card.get(card.card_id, []),
                    relation_ids=[],
                    hop=0,
                    retrieval=rank_by_id.get(card.card_id),
                )
                for card in sorted(
                    snapshot.cards,
                    key=lambda item: rank_by_id.get(
                        item.card_id,
                        {},
                    ).get("retrieval_rank", seed_limit + 1),
                )
            ]
            for card in cards:
                card["fact_card_count"] = fact_card_counts.get(
                    card["fact_id"],
                    1,
                )
            result = {
                "operation": "search",
                "query": normalized_query,
                "cards": cards,
                "communities": [
                    _community_summary_payload(community)
                    for community in snapshot.communities
                ],
                "diagnostics": {
                    "recall_limit": recall_limit,
                    "recalled_candidate_count": len(recalled),
                    "active_candidate_count": len(active_recalled),
                    "fact_duplicate_count": duplicate_fact_count,
                    "seed_count": len(cards),
                    "missing_or_inactive_seed_count": max(
                        0,
                        len(seed_ids) - len(cards),
                    ),
                },
                "next_operations": [
                    "kg_card_open",
                    "kg_card_expand",
                    "kg_community_open",
                ],
            }
            langfuse_update_span(
                output={
                    "card_ids": [card["card_id"] for card in cards],
                    "community_ids": [
                        community["community_id"]
                        for community in result["communities"]
                    ],
                    "diagnostics": result["diagnostics"],
                },
                status_message="completed",
            )
            return result

    async def expand_cards(
        self,
        *,
        card_ids: list[str],
        adapter_name: str = "financial",
        hop_limit: int = 1,
        node_limit: int = 40,
        edge_limit: int = 80,
        relation_kinds: list[str] | None = None,
        decision_classes: list[str] | None = None,
        min_confidence: float = 0.0,
    ) -> dict[str, Any]:
        identities = _required_ids(card_ids, "card_ids", limit=30)
        adapter = _required_text(adapter_name, "adapter_name")
        _bounded(hop_limit, "hop_limit", 1, 2)
        _bounded(node_limit, "node_limit", len(identities), 100)
        _bounded(edge_limit, "edge_limit", 1, 200)
        _bounded_float(min_confidence, "min_confidence", 0.0, 1.0)
        classes = _decision_classes(decision_classes)
        kinds = _clean_values(relation_kinds or [])
        with langfuse_observation(
            name="kg.relation_graph_agent.card_expand",
            as_type="tool",
            input={
                "card_ids": identities,
                "adapter_name": adapter,
                "target": self.target,
                "hop_limit": hop_limit,
                "node_limit": node_limit,
                "edge_limit": edge_limit,
                "relation_kinds": kinds,
                "decision_classes": classes,
                "min_confidence": min_confidence,
            },
        ):
            snapshot = await asyncio.to_thread(
                self._repository.load_subgraph,
                adapter_name=adapter,
                seed_card_ids=identities,
                hop_limit=hop_limit,
                node_limit=node_limit,
                edge_limit=edge_limit,
                relation_kinds=kinds,
                decision_classes=classes,
                min_confidence=min_confidence,
            )
            summaries = await self._get_summaries(
                [card.card_id for card in snapshot.cards],
                adapter_name=adapter,
            )
            community_ids_by_card = _community_ids_by_card(
                snapshot.communities
            )
            community_ids_by_card.update(
                {
                    card_id: list(community_ids)
                    for card_id, community_ids
                    in snapshot.community_ids_by_card_id.items()
                }
            )
            relation_ids_by_card = _relation_ids_by_card(snapshot.edges)
            result = {
                "operation": "card_expand",
                "seed_card_ids": identities,
                "cards": [
                    _card_payload(
                        card,
                        summary=summaries.get(card.card_id),
                        focus=None,
                        community_ids=community_ids_by_card.get(
                            card.card_id, []
                        ),
                        relation_ids=relation_ids_by_card.get(
                            card.card_id, []
                        ),
                        hop=snapshot.hop_by_card_id.get(card.card_id, 0),
                    )
                    for card in snapshot.cards
                ],
                "edges": [
                    _edge_handle_payload(edge)
                    for edge in snapshot.edges
                ],
                "communities": [
                    _community_summary_payload(community)
                    for community in snapshot.communities
                ],
                "truncated": snapshot.truncated,
                "next_operations": [
                    "kg_card_open",
                    "kg_edge_open",
                    "kg_community_open",
                ],
            }
            langfuse_update_span(
                output={
                    "cards": len(result["cards"]),
                    "edges": len(result["edges"]),
                    "communities": len(result["communities"]),
                    "truncated": result["truncated"],
                },
                status_message="completed",
            )
            return result

    async def expand_communities(
        self,
        *,
        community_ids: list[str],
        adapter_name: str = "financial",
        hop_limit: int = 1,
        community_limit: int = 30,
        relation_limit: int = 60,
        relation_kinds: list[str] | None = None,
    ) -> dict[str, Any]:
        identities = _required_ids(
            community_ids,
            "community_ids",
            limit=20,
        )
        adapter = _required_text(adapter_name, "adapter_name")
        _bounded(hop_limit, "hop_limit", 1, 2)
        _bounded(
            community_limit,
            "community_limit",
            len(identities),
            100,
        )
        _bounded(relation_limit, "relation_limit", 1, 200)
        kinds = _clean_values(relation_kinds or [])
        with langfuse_observation(
            name="kg.relation_graph_agent.community_expand",
            as_type="tool",
            input={
                "community_ids": identities,
                "adapter_name": adapter,
                "target": self.target,
                "hop_limit": hop_limit,
                "community_limit": community_limit,
                "relation_limit": relation_limit,
                "relation_kinds": kinds,
            },
        ):
            snapshot = await asyncio.to_thread(
                self._repository.load_community_neighborhood,
                adapter_name=adapter,
                seed_community_ids=identities,
                hop_limit=hop_limit,
                community_limit=community_limit,
                relation_limit=relation_limit,
                relation_kinds=kinds,
            )
            anchor_summaries = await self._get_summaries(
                [
                    community.identity_anchor_card_id
                    for community in snapshot.communities
                ],
                adapter_name=adapter,
            )
            result = {
                "operation": "community_expand",
                "seed_community_ids": identities,
                "communities": [
                    {
                        **_community_summary_payload(community),
                        "hop": snapshot.hop_by_community_id.get(
                            community.community_id, 0
                        ),
                        "representative_summary": (
                            anchor_summaries[
                                community.identity_anchor_card_id
                            ].text
                            if community.identity_anchor_card_id
                            in anchor_summaries
                            else ""
                        ),
                    }
                    for community in snapshot.communities
                ],
                "community_relations": [
                    _community_relation_payload(relation)
                    for relation in snapshot.relations
                ],
                "truncated": snapshot.truncated,
                "next_operations": ["kg_community_open"],
            }
            langfuse_update_span(
                output={
                    "communities": len(result["communities"]),
                    "community_relations": len(
                        result["community_relations"]
                    ),
                    "truncated": result["truncated"],
                },
                status_message="completed",
            )
            return result

    async def open_cards(
        self,
        *,
        card_ids: list[str],
        adapter_name: str = "financial",
        incident_edge_limit: int = 40,
    ) -> dict[str, Any]:
        identities = _required_ids(card_ids, "card_ids", limit=30)
        adapter = _required_text(adapter_name, "adapter_name")
        _bounded(incident_edge_limit, "incident_edge_limit", 1, 200)
        with langfuse_observation(
            name="kg.relation_graph_agent.card_open",
            as_type="tool",
            input={
                "card_ids": identities,
                "adapter_name": adapter,
                "target": self.target,
                "incident_edge_limit": incident_edge_limit,
            },
        ):
            snapshot = await asyncio.to_thread(
                self._repository.load_subgraph,
                adapter_name=adapter,
                seed_card_ids=identities,
                hop_limit=1,
                node_limit=min(100, len(identities) + incident_edge_limit),
                edge_limit=incident_edge_limit,
                relation_kinds=[],
                decision_classes=["observed", "inferred"],
                min_confidence=0.0,
            )
            requested = set(identities)
            cards = [
                card
                for card in snapshot.cards
                if card.card_id in requested
            ]
            summaries, focus_evidence = await self._get_card_views(
                [card.card_id for card in cards],
                adapter_name=adapter,
            )
            community_ids_by_card = _community_ids_by_card(
                snapshot.communities
            )
            community_ids_by_card.update(
                {
                    card_id: list(community_ids)
                    for card_id, community_ids
                    in snapshot.community_ids_by_card_id.items()
                }
            )
            relation_ids_by_card = _relation_ids_by_card(snapshot.edges)
            payloads = [
                _card_payload(
                    card,
                    summary=summaries.get(card.card_id),
                    focus=focus_evidence.get(card.card_id),
                    community_ids=community_ids_by_card.get(
                        card.card_id, []
                    ),
                    relation_ids=relation_ids_by_card.get(
                        card.card_id, []
                    ),
                    hop=0,
                )
                for card in cards
            ]
            result = {
                "operation": "card_open",
                "cards": payloads,
                "missing_card_ids": [
                    card_id
                    for card_id in identities
                    if card_id not in {card["card_id"] for card in payloads}
                ],
                "missing_summary_card_ids": [
                    card.card_id
                    for card in cards
                    if card.card_id not in summaries
                ],
                "missing_focus_evidence_card_ids": [
                    card.card_id
                    for card in cards
                    if card.card_id not in focus_evidence
                ],
                "incident_relations_truncated": snapshot.truncated,
                "next_operations": [
                    "kg_card_expand",
                    "kg_edge_open",
                    "kg_community_open",
                ],
            }
            langfuse_update_span(
                output={
                    "cards": len(payloads),
                    "missing_card_ids": result["missing_card_ids"],
                    "missing_summary_card_ids": result[
                        "missing_summary_card_ids"
                    ],
                    "missing_focus_evidence_card_ids": result[
                        "missing_focus_evidence_card_ids"
                    ],
                    "incident_relations_truncated": snapshot.truncated,
                },
                status_message="completed",
            )
            return result

    async def open_edges(
        self,
        *,
        edge_ids: list[str],
        adapter_name: str = "financial",
    ) -> dict[str, Any]:
        identities = _required_ids(edge_ids, "edge_ids", limit=50)
        adapter = _required_text(adapter_name, "adapter_name")
        with langfuse_observation(
            name="kg.relation_graph_agent.edge_open",
            as_type="tool",
            input={
                "edge_ids": identities,
                "adapter_name": adapter,
                "target": self.target,
            },
        ):
            edges = await asyncio.to_thread(
                self._repository.load_edges,
                adapter_name=adapter,
                edge_ids=identities,
            )
            endpoint_ids = _clean_values(
                [
                    card_id
                    for edge in edges
                    for card_id in (
                        edge.source_card_id,
                        edge.target_card_id,
                    )
                ]
            )
            cards = await asyncio.to_thread(
                self._repository.load_cards,
                adapter_name=adapter,
                card_ids=endpoint_ids,
            )
            summaries, focus_evidence = await self._get_card_views(
                endpoint_ids,
                adapter_name=adapter,
            )
            card_by_id = {card.card_id: card for card in cards}
            result_edges = [
                {
                    **_edge_payload(edge),
                    "source_card": _edge_endpoint_payload(
                        card_by_id.get(edge.source_card_id),
                        summary=summaries.get(edge.source_card_id),
                        focus=focus_evidence.get(edge.source_card_id),
                    ),
                    "target_card": _edge_endpoint_payload(
                        card_by_id.get(edge.target_card_id),
                        summary=summaries.get(edge.target_card_id),
                        focus=focus_evidence.get(edge.target_card_id),
                    ),
                }
                for edge in edges
            ]
            result = {
                "operation": "edge_open",
                "edges": result_edges,
                "missing_edge_ids": [
                    edge_id
                    for edge_id in identities
                    if edge_id
                    not in {edge["edge_id"] for edge in result_edges}
                ],
                "next_operations": ["kg_card_open", "kg_card_expand"],
            }
            langfuse_update_span(
                output={
                    "edges": len(result_edges),
                    "missing_edge_ids": result["missing_edge_ids"],
                },
                status_message="completed",
            )
            return result

    async def open_communities(
        self,
        *,
        community_ids: list[str],
        adapter_name: str = "financial",
        member_limit: int = 40,
        edge_limit: int = 80,
    ) -> dict[str, Any]:
        identities = _required_ids(
            community_ids,
            "community_ids",
            limit=20,
        )
        adapter = _required_text(adapter_name, "adapter_name")
        _bounded(member_limit, "member_limit", 1, 100)
        _bounded(edge_limit, "edge_limit", 1, 200)
        with langfuse_observation(
            name="kg.relation_graph_agent.community_open",
            as_type="tool",
            input={
                "community_ids": identities,
                "adapter_name": adapter,
                "target": self.target,
                "member_limit": member_limit,
                "edge_limit": edge_limit,
            },
        ):
            communities = await asyncio.to_thread(
                self._repository.load_communities,
                adapter_name=adapter,
                community_ids=identities,
            )
            selected_card_ids = _clean_values(
                [
                    card_id
                    for community in communities
                    for card_id in community.member_card_ids[:member_limit]
                ]
            )
            selected_edge_ids = _clean_values(
                [
                    edge_id
                    for community in communities
                    for edge_id in community.member_edge_ids[:edge_limit]
                ]
            )
            cards, edges = await asyncio.gather(
                asyncio.to_thread(
                    self._repository.load_cards,
                    adapter_name=adapter,
                    card_ids=selected_card_ids,
                ),
                asyncio.to_thread(
                    self._repository.load_edges,
                    adapter_name=adapter,
                    edge_ids=selected_edge_ids,
                ),
            )
            summaries = await self._get_summaries(
                [card.card_id for card in cards],
                adapter_name=adapter,
            )
            card_by_id = {card.card_id: card for card in cards}
            edge_by_id = {edge.edge_id: edge for edge in edges}
            result_communities = []
            for community in communities:
                member_ids = list(
                    community.member_card_ids[:member_limit]
                )
                member_edge_ids = list(
                    community.member_edge_ids[:edge_limit]
                )
                result_communities.append(
                    {
                        **_community_summary_payload(community),
                        "members": [
                            {
                                "card_id": card_id,
                                "summary": (
                                    summaries[card_id].text
                                    if card_id in summaries
                                    else ""
                                ),
                                "source_id": (
                                    card_by_id[card_id].source_id
                                    if card_id in card_by_id
                                    else ""
                                ),
                                "source_published_at": (
                                    _published_at(
                                        summaries.get(card_id)
                                    )
                                ),
                            }
                            for card_id in member_ids
                            if card_id in card_by_id
                        ],
                        "edges": [
                            _edge_handle_payload(edge_by_id[edge_id])
                            for edge_id in member_edge_ids
                            if edge_id in edge_by_id
                        ],
                        "members_truncated": (
                            len(community.member_card_ids) > member_limit
                        ),
                        "edges_truncated": (
                            len(community.member_edge_ids) > edge_limit
                        ),
                    }
                )
            result = {
                "operation": "community_open",
                "communities": result_communities,
                "missing_community_ids": [
                    community_id
                    for community_id in identities
                    if community_id
                    not in {
                        community["community_id"]
                        for community in result_communities
                    }
                ],
                "next_operations": [
                    "kg_card_open",
                    "kg_edge_open",
                    "kg_community_expand",
                ],
            }
            langfuse_update_span(
                output={
                    "communities": len(result_communities),
                    "cards": len(cards),
                    "edges": len(edges),
                    "missing_community_ids": result[
                        "missing_community_ids"
                    ],
                },
                status_message="completed",
            )
            return result

    async def _rerank_cards(
        self,
        query: str,
        hits: list[RelationCardSearchHit],
        *,
        top_n: int,
    ) -> list[dict[str, Any]]:
        if not hits:
            return []
        response = await self._reranker.rerank(
            query=query,
            documents=[hit.summary for hit in hits],
            top_n=min(top_n, len(hits)),
        )
        result = []
        for item in response.results:
            if item.index < 0 or item.index >= len(hits):
                raise RuntimeError(
                    f"reranker 返回非法 index: {item.index}"
                )
            score = float(item.relevance_score)
            if score < settings.KG_RELATION_RERANK_MIN_SCORE:
                continue
            result.append(
                {
                    "hit": hits[item.index],
                    "rerank_score": score,
                }
            )
        return sorted(
            result,
            key=lambda item: (
                -float(item["rerank_score"]),
                -float(item["hit"].fusion_score),
                item["hit"].card_id,
            ),
        )

    async def _get_summaries(
        self,
        card_ids: list[str],
        *,
        adapter_name: str,
    ) -> dict[str, RelationCardText]:
        store, owns_store = await self._card_store_for_call()
        try:
            return await store.get_summaries(
                card_ids,
                adapter_name=adapter_name,
                target=self.target,
            )
        finally:
            if owns_store:
                await asyncio.to_thread(store.store.close)

    async def _get_card_views(
        self,
        card_ids: list[str],
        *,
        adapter_name: str,
    ) -> tuple[
        dict[str, RelationCardText],
        dict[str, RelationCardText],
    ]:
        store, owns_store = await self._card_store_for_call()
        try:
            return await asyncio.gather(
                store.get_summaries(
                    card_ids,
                    adapter_name=adapter_name,
                    target=self.target,
                ),
                store.get_focus_evidence(
                    card_ids,
                    adapter_name=adapter_name,
                    target=self.target,
                ),
            )
        finally:
            if owns_store:
                await asyncio.to_thread(store.store.close)

    async def _card_store_for_call(
        self,
    ) -> tuple[MilvusRelationCandidateStore | Any, bool]:
        if self._card_store is not None:
            return self._card_store, False
        return await asyncio.to_thread(MilvusRelationCandidateStore), True


def create_relation_graph_agent_retrieval_service(
    *,
    target: Target = "prod",
) -> RelationGraphAgentRetrievalService:
    return RelationGraphAgentRetrievalService(target=target)


def _card_payload(
    card: AgentGraphCardRecord,
    *,
    summary: RelationCardText | None,
    focus: RelationCardText | None,
    community_ids: list[str],
    relation_ids: list[str],
    hop: int,
    retrieval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "card_id": card.card_id,
        "fact_id": card.fact_id,
        "summary": summary.text if summary else "",
        "focus_evidence": focus.text if focus else "",
        "source_type": card.source_type,
        "source_id": card.source_id,
        "source_published_at": _published_at(summary or focus),
        "evidence_id": card.evidence_id,
        "primary_chunk_id": card.primary_chunk_id,
        "focus_evidence_refs": list(card.focus_evidence_refs),
        "community_ids": list(community_ids),
        "relation_ids": list(relation_ids),
        "hop": hop,
    }
    if retrieval:
        payload["retrieval"] = retrieval
    return payload


def _edge_endpoint_payload(
    card: AgentGraphCardRecord | None,
    *,
    summary: RelationCardText | None,
    focus: RelationCardText | None,
) -> dict[str, Any]:
    return {
        "card_id": card.card_id if card else "",
        "fact_id": card.fact_id if card else "",
        "summary": summary.text if summary else "",
        "focus_evidence": focus.text if focus else "",
        "source_id": card.source_id if card else "",
        "source_published_at": _published_at(summary or focus),
        "evidence_id": card.evidence_id if card else "",
        "primary_chunk_id": card.primary_chunk_id if card else "",
    }


def _edge_payload(edge: AgentGraphEdgeRecord) -> dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "source_card_id": edge.source_card_id,
        "target_card_id": edge.target_card_id,
        "relation_kind": edge.relation_kind,
        "relation_type": edge.relation_type,
        "direction": edge.direction,
        "decision_class": edge.decision_class,
        "basis": edge.basis,
        "inference_mechanism": edge.inference_mechanism,
        "confidence": edge.confidence,
        "source_evidence_refs": list(edge.source_evidence_refs),
        "target_evidence_refs": list(edge.target_evidence_refs),
        "relation_evidence_refs": [
            dict(item)
            for item in edge.relation_evidence_refs
        ],
        "created_at": _iso(edge.created_at),
        "updated_at": _iso(edge.updated_at),
    }


def _edge_handle_payload(edge: AgentGraphEdgeRecord) -> dict[str, Any]:
    """Return enough graph metadata to select an Edge without leaking evidence."""

    return {
        "edge_id": edge.edge_id,
        "source_card_id": edge.source_card_id,
        "target_card_id": edge.target_card_id,
        "relation_kind": edge.relation_kind,
        "relation_type": edge.relation_type,
        "direction": edge.direction,
        "decision_class": edge.decision_class,
        "confidence": edge.confidence,
        "created_at": _iso(edge.created_at),
        "updated_at": _iso(edge.updated_at),
    }


def _group_ranked_cards_by_fact(
    ranked: list[dict[str, Any]],
    *,
    card_by_id: dict[str, AgentGraphCardRecord],
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Keep the highest-ranked Card for each event without discarding sources."""

    result: list[dict[str, Any]] = []
    seen_fact_ids: set[str] = set()
    duplicate_fact_count = 0
    for item in ranked:
        card_id = item["hit"].card_id
        card = card_by_id.get(card_id)
        fact_id = (
            card.fact_id
            if card and card.fact_id
            else f"card:{card_id}"
        )
        if fact_id in seen_fact_ids:
            duplicate_fact_count += 1
            continue
        seen_fact_ids.add(fact_id)
        if len(result) < limit:
            result.append(item)
    return result, duplicate_fact_count


def _community_summary_payload(
    community: AgentGraphCommunityRecord,
) -> dict[str, Any]:
    return {
        "community_id": community.community_id,
        "title": community.title,
        "identity_anchor_card_id": community.identity_anchor_card_id,
        "card_count": len(community.member_card_ids),
        "edge_count": len(community.member_edge_ids),
        "graph_version": community.graph_version,
        "graph_changed_at": _iso(community.graph_changed_at),
    }


def _community_relation_payload(
    relation: AgentGraphCommunityRelationRecord,
) -> dict[str, Any]:
    return {
        "relation_id": relation.relation_id,
        "source_community_id": relation.source_community_id,
        "target_community_id": relation.target_community_id,
        "relation_kind": relation.relation_kind,
        "supporting_edge_ids": list(relation.supporting_edge_ids),
        "observed_edge_count": relation.observed_edge_count,
        "inferred_edge_count": relation.inferred_edge_count,
    }


def _community_ids_by_card(
    communities: tuple[AgentGraphCommunityRecord, ...],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for community in communities:
        for card_id in community.member_card_ids:
            result[card_id].append(community.community_id)
    return {
        card_id: sorted(community_ids)
        for card_id, community_ids in result.items()
    }


def _relation_ids_by_card(
    edges: tuple[AgentGraphEdgeRecord, ...],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        result[edge.source_card_id].append(edge.edge_id)
        result[edge.target_card_id].append(edge.edge_id)
    return {
        card_id: sorted(edge_ids)
        for card_id, edge_ids in result.items()
    }


def _published_at(card: RelationCardText | None) -> str:
    if card is None:
        return ""
    return str(
        card.metadata.get("source_published_at")
        or card.metadata.get("published_at")
        or ""
    )


def _decision_classes(values: list[str] | None) -> list[str]:
    cleaned = _clean_values(
        values if values is not None else ["observed", "inferred"]
    )
    invalid = sorted(set(cleaned).difference({"observed", "inferred"}))
    if invalid:
        raise ValueError(f"decision_classes 非法: {invalid}")
    return cleaned


def _required_text(value: str, name: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{name} 不能为空")
    return cleaned


def _required_ids(
    values: list[str],
    name: str,
    *,
    limit: int,
) -> list[str]:
    cleaned = _clean_values(values)
    if not cleaned:
        raise ValueError(f"{name} 不能为空")
    if len(cleaned) > limit:
        raise ValueError(f"{name} 数量不能超过 {limit}")
    return cleaned


def _clean_values(values: list[str]) -> list[str]:
    return list(
        dict.fromkeys(
            str(value).strip()
            for value in values
            if str(value).strip()
        )
    )


def _bounded(value: int, name: str, minimum: int, maximum: int) -> None:
    if value < minimum or value > maximum:
        raise ValueError(
            f"{name} 必须在 {minimum} 到 {maximum} 之间"
        )


def _bounded_float(
    value: float,
    name: str,
    minimum: float,
    maximum: float,
) -> None:
    if value < minimum or value > maximum:
        raise ValueError(
            f"{name} 必须在 {minimum} 到 {maximum} 之间"
        )


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""
