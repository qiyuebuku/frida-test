"""Milvus dense+sparse hybrid index for KG evidence chunks."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.domain.knowledge.schemas import EvidenceChunk
from src.domain.knowledge.retrieval_profile import profile_event, profile_span
from src.infrastructure.config import settings

logger = logging.getLogger(__name__)

MILVUS_READ_AFTER_WRITE_CONSISTENCY = "Strong"
MILVUS_TEXT_MAX_BYTES = 8192
_EMPTY_SCOPE_WARNED: set[tuple[str, str, str]] = set()


MILVUS_COLLECTION_CHUNK = "chunk"
MILVUS_COLLECTION_COGNITIVE_CARD = "cognitive_card"
MILVUS_COLLECTION_COGNITIVE_CARD_FOCUS = "cognitive_card_focus"
MILVUS_COLLECTION_ENTITY = "entity"
MILVUS_COLLECTION_RELATION = "relation"
MILVUS_COLLECTION_CARD_RELATION = "card_relation"
MILVUS_COLLECTION_COMMUNITY = "community"
MILVUS_COLLECTION_COMMUNITY_INSIGHT = "community_insight"
MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT = "graph_community_report"
MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION = "graph_community_projection"
MILVUS_COLLECTION_ASSIGNMENT_BUCKET = "assignment_bucket"
MILVUS_COLLECTION_ROLES = (
    MILVUS_COLLECTION_CHUNK,
    MILVUS_COLLECTION_COGNITIVE_CARD,
    MILVUS_COLLECTION_COGNITIVE_CARD_FOCUS,
    MILVUS_COLLECTION_ENTITY,
    MILVUS_COLLECTION_RELATION,
    MILVUS_COLLECTION_CARD_RELATION,
    MILVUS_COLLECTION_COMMUNITY,
    MILVUS_COLLECTION_COMMUNITY_INSIGHT,
    MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT,
    MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION,
    MILVUS_COLLECTION_ASSIGNMENT_BUCKET,
)


@dataclass(frozen=True)
class MilvusCollectionRegistry:
    chunk: str
    cognitive_card: str
    entity: str
    relation: str
    community: str
    community_insight: str
    assignment_bucket: str
    cognitive_card_focus: str = ""
    card_relation: str = ""
    graph_community_report: str = ""
    graph_community_projection: str = ""

    @classmethod
    def from_settings(cls) -> "MilvusCollectionRegistry":
        return cls(
            chunk=settings.MILVUS_CHUNK_COLLECTION,
            cognitive_card=settings.MILVUS_COGNITIVE_CARD_COLLECTION,
            entity=settings.MILVUS_ENTITY_COLLECTION,
            relation=settings.MILVUS_RELATION_COLLECTION,
            card_relation=settings.MILVUS_CARD_RELATION_COLLECTION,
            community=settings.MILVUS_COMMUNITY_COLLECTION,
            community_insight=settings.MILVUS_COMMUNITY_INSIGHT_COLLECTION,
            graph_community_report=settings.MILVUS_GRAPH_COMMUNITY_REPORT_COLLECTION,
            graph_community_projection=(
                settings.MILVUS_GRAPH_COMMUNITY_PROJECTION_COLLECTION
            ),
            assignment_bucket=settings.MILVUS_ASSIGNMENT_BUCKET_COLLECTION,
            cognitive_card_focus=settings.MILVUS_COGNITIVE_CARD_FOCUS_COLLECTION,
        )

    def name_for(self, collection_role: str) -> str:
        if collection_role == MILVUS_COLLECTION_CHUNK:
            return self.chunk
        if collection_role == MILVUS_COLLECTION_COGNITIVE_CARD:
            return self.cognitive_card
        if collection_role == MILVUS_COLLECTION_COGNITIVE_CARD_FOCUS:
            return self.cognitive_card_focus or settings.MILVUS_COGNITIVE_CARD_FOCUS_COLLECTION
        if collection_role == MILVUS_COLLECTION_ENTITY:
            return self.entity
        if collection_role == MILVUS_COLLECTION_RELATION:
            return self.relation
        if collection_role == MILVUS_COLLECTION_CARD_RELATION:
            return self.card_relation or settings.MILVUS_CARD_RELATION_COLLECTION
        if collection_role == MILVUS_COLLECTION_COMMUNITY:
            return self.community
        if collection_role == MILVUS_COLLECTION_COMMUNITY_INSIGHT:
            return self.community_insight
        if collection_role == MILVUS_COLLECTION_GRAPH_COMMUNITY_REPORT:
            return (
                self.graph_community_report
                or settings.MILVUS_GRAPH_COMMUNITY_REPORT_COLLECTION
            )
        if collection_role == MILVUS_COLLECTION_GRAPH_COMMUNITY_PROJECTION:
            return (
                self.graph_community_projection
                or settings.MILVUS_GRAPH_COMMUNITY_PROJECTION_COLLECTION
            )
        if collection_role == MILVUS_COLLECTION_ASSIGNMENT_BUCKET:
            return self.assignment_bucket
        raise ValueError(f"unsupported Milvus collection role: {collection_role}")


@dataclass(frozen=True)
class MilvusHybridHit:
    chunk_id: str
    evidence_id: str
    text: str
    score: float
    metadata: dict[str, Any]

    @property
    def target_id(self) -> str:
        return str(self.metadata.get("target_id") or self.chunk_id)


@dataclass(frozen=True)
class MilvusHybridDocument:
    chunk_id: str
    text: str
    evidence_id: str = ""
    metadata: dict[str, Any] | None = None

    @property
    def target_id(self) -> str:
        return str((self.metadata or {}).get("target_id") or self.chunk_id)


class MilvusHybridStore:
    """Thin wrapper around the required Milvus hybrid index."""

    def __init__(
        self,
        *,
        uri: str | None = None,
        token: str | None = None,
        collection_name: str | None = None,
        dim: int | None = None,
        metric_type: str | None = None,
        rrf_k: int | None = None,
    ):
        self.uri = uri or settings.MILVUS_URI
        self.token = token if token is not None else settings.MILVUS_TOKEN
        self.collection_name = collection_name or settings.MILVUS_COLLECTION
        self.dim = int(dim or settings.EMBEDDING_DIM)
        self.metric_type = metric_type or settings.MILVUS_METRIC_TYPE
        self.rrf_k = int(rrf_k or settings.MILVUS_RRF_K)
        self._client = None
        self._imports: dict[str, Any] | None = None

    @property
    def available(self) -> bool:
        try:
            self.ensure_ready()
            return True
        except Exception:
            return False

    def ensure_ready(self) -> None:
        with profile_span("milvus_store.load_imports"):
            self._load_imports()
        with profile_span("milvus_store.get_client", uri=self.uri, collection=self.collection_name):
            self._get_client()

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception:
            logger.warning("Failed to close Milvus client", exc_info=True)
        finally:
            self._client = None

    def ensure_collection(self, *, recreate_on_dim_mismatch: bool = False) -> None:
        imports = self._load_imports()
        client = self._get_client()
        if client.has_collection(self.collection_name):
            existing_dim = _collection_dense_dim(client, self.collection_name)
            has_target_id = _collection_has_field(client, self.collection_name, "target_id")
            has_metadata_json = _collection_has_field(client, self.collection_name, "metadata_json")
            has_time_fields = all(
                _collection_has_field(client, self.collection_name, field)
                for field in ("published_at_ts", "event_time_start_ts", "event_time_end_ts")
            )
            if existing_dim in {None, self.dim} and has_target_id and has_metadata_json and has_time_fields:
                return
            if not recreate_on_dim_mismatch:
                raise RuntimeError(
                    f"Milvus collection {self.collection_name} schema mismatch: "
                    f"existing_dim={existing_dim} configured_dim={self.dim} "
                    f"has_target_id={has_target_id} has_metadata_json={has_metadata_json} "
                    f"has_time_fields={has_time_fields}. "
                    "Rebuild the semantic hybrid index before retrieval."
                )
            logger.warning(
                "Milvus collection %s schema mismatch: existing_dim=%s configured_dim=%s "
                "has_target_id=%s has_metadata_json=%s has_time_fields=%s; recreating collection",
                self.collection_name,
                existing_dim,
                self.dim,
                has_target_id,
                has_metadata_json,
                has_time_fields,
            )
            client.drop_collection(self.collection_name)

        schema = client.create_schema()
        data_type = imports["DataType"]
        schema.add_field("target_id", data_type.VARCHAR, is_primary=True, max_length=260)
        schema.add_field("chunk_id", data_type.VARCHAR, max_length=220)
        schema.add_field("evidence_id", data_type.VARCHAR, max_length=180)
        schema.add_field("adapter_name", data_type.VARCHAR, max_length=64)
        schema.add_field("target", data_type.VARCHAR, max_length=16)
        schema.add_field("target_type", data_type.VARCHAR, max_length=64)
        schema.add_field("source_type", data_type.VARCHAR, max_length=80)
        schema.add_field("source_id", data_type.VARCHAR, max_length=160)
        schema.add_field(
            "text",
            data_type.VARCHAR,
            max_length=MILVUS_TEXT_MAX_BYTES,
            enable_analyzer=True,
        )
        schema.add_field("dense_vector", data_type.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("sparse_vector", data_type.SPARSE_FLOAT_VECTOR)
        schema.add_field("content_hash", data_type.VARCHAR, max_length=64)
        schema.add_field("embedding_model", data_type.VARCHAR, max_length=80)
        schema.add_field("kg_version", data_type.VARCHAR, max_length=80)
        schema.add_field("published_at_ts", data_type.INT64)
        schema.add_field("event_time_start_ts", data_type.INT64)
        schema.add_field("event_time_end_ts", data_type.INT64)
        schema.add_field("metadata_json", data_type.JSON)

        bm25_function = imports["Function"](
            name="bm25",
            function_type=imports["FunctionType"].BM25,
            input_field_names=["text"],
            output_field_names=["sparse_vector"],
        )
        schema.add_function(bm25_function)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="AUTOINDEX",
            metric_type=self.metric_type,
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
        )
        client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
            consistency_level=MILVUS_READ_AFTER_WRITE_CONSISTENCY,
        )

    def replace_chunks(
        self,
        *,
        adapter_name: str,
        target: str,
        chunks: list[EvidenceChunk],
        vectors: list[list[float]],
        embedding_model: str,
        kg_version: str = "",
    ) -> int:
        documents = [
            _document_from_chunk(chunk)
            for chunk in chunks
        ]
        return self.replace_documents(
            adapter_name=adapter_name,
            target=target,
            documents=documents,
            vectors=vectors,
            embedding_model=embedding_model,
            kg_version=kg_version,
        )

    def replace_documents(
        self,
        *,
        adapter_name: str,
        target: str,
        documents: list[MilvusHybridDocument],
        vectors: list[list[float]],
        embedding_model: str,
        kg_version: str = "",
    ) -> int:
        _validate_document_vectors(documents, vectors, dim=self.dim)
        with profile_span("milvus_store.replace_documents.ensure_collection", collection=self.collection_name):
            self.ensure_collection(recreate_on_dim_mismatch=True)
        with profile_span("milvus_store.replace_documents.delete_scope", adapter=adapter_name, target=target):
            self.delete_scope(adapter_name=adapter_name, target=target)
        with profile_span("milvus_store.replace_documents.build_rows", documents=len(documents)):
            rows = _document_rows(
                adapter_name=adapter_name,
                target=target,
                documents=documents,
                vectors=vectors,
                embedding_model=embedding_model,
                kg_version=kg_version,
            )
        if not rows:
            return 0
        with profile_span("milvus_store.replace_documents.insert", rows=len(rows)):
            self._upsert_rows(rows)
        return len(rows)

    def upsert_documents(
        self,
        *,
        adapter_name: str,
        target: str,
        documents: list[MilvusHybridDocument],
        vectors: list[list[float]],
        embedding_model: str,
        kg_version: str = "",
    ) -> int:
        _validate_document_vectors(documents, vectors, dim=self.dim)
        with profile_span("milvus_store.upsert_documents.ensure_collection", collection=self.collection_name):
            self.ensure_collection(recreate_on_dim_mismatch=True)
        with profile_span("milvus_store.upsert_documents.build_rows", documents=len(documents)):
            rows = _document_rows(
                adapter_name=adapter_name,
                target=target,
                documents=documents,
                vectors=vectors,
                embedding_model=embedding_model,
                kg_version=kg_version,
            )
        if not rows:
            return 0
        with profile_span("milvus_store.upsert_documents.insert", rows=len(rows)):
            self._upsert_rows(rows)
        return len(rows)

    def delete_scope(self, *, adapter_name: str, target: str) -> None:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return
        client.delete(
            collection_name=self.collection_name,
            filter=f'adapter_name == "{adapter_name}" and target == "{target}"',
        )

    def delete_documents(self, *, adapter_name: str, target: str, chunk_ids: list[str]) -> None:
        chunk_ids = [chunk_id for chunk_id in chunk_ids if chunk_id]
        if not chunk_ids:
            return
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return
        for start in range(0, len(chunk_ids), settings.MILVUS_BATCH_SIZE):
            batch = chunk_ids[start : start + settings.MILVUS_BATCH_SIZE]
            client.delete(
                collection_name=self.collection_name,
                filter=_scoped_in_filter(
                    adapter_name=adapter_name,
                    target=target,
                    field="target_id",
                    values=batch,
                ),
            )

    def list_target_ids(
        self,
        *,
        adapter_name: str,
        target: str,
        source_type: str | None = None,
        limit: int = 1_000_000,
    ) -> list[str]:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return []
        max_results = max(1, int(limit or 1))
        page_size = min(max(settings.MILVUS_BATCH_SIZE, 1), max_results)
        filters = [
            f'adapter_name == "{_escape_filter_value(adapter_name)}"',
            f'target == "{_escape_filter_value(target)}"',
        ]
        if source_type:
            filters.append(f'source_type == "{_escape_filter_value(source_type)}"')
        target_ids: list[str] = []
        offset = 0
        filter_text = " and ".join(filters)
        while len(target_ids) < max_results:
            current_limit = min(page_size, max_results - len(target_ids))
            rows = client.query(
                collection_name=self.collection_name,
                filter=filter_text,
                output_fields=["target_id"],
                limit=current_limit,
                offset=offset,
            )
            if not rows:
                break
            target_ids.extend(
                str(row.get("target_id") or "").strip()
                for row in rows
                if str(row.get("target_id") or "").strip()
            )
            if len(rows) < current_limit:
                break
            offset += len(rows)
        return target_ids

    def delete_evidence(self, *, adapter_name: str, target: str, evidence_ids: list[str]) -> None:
        evidence_ids = [evidence_id for evidence_id in dict.fromkeys(evidence_ids) if evidence_id]
        if not evidence_ids:
            return
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return
        for start in range(0, len(evidence_ids), settings.MILVUS_BATCH_SIZE):
            batch = evidence_ids[start : start + settings.MILVUS_BATCH_SIZE]
            client.delete(
                collection_name=self.collection_name,
                filter=_scoped_in_filter(
                    adapter_name=adapter_name,
                    target=target,
                    field="evidence_id",
                    values=batch,
                ),
            )

    def get_documents(
        self,
        *,
        adapter_name: str,
        target: str,
        target_ids: list[str],
    ) -> list[MilvusHybridHit]:
        target_ids = [target_id for target_id in dict.fromkeys(target_ids) if target_id]
        if not target_ids:
            return []
        with profile_span("milvus_store.get_documents.ensure_collection", collection=self.collection_name):
            self.ensure_collection()
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return []
        output_fields = [
            "target_id",
            "chunk_id",
            "evidence_id",
            "text",
            "adapter_name",
            "target",
            "target_type",
            "source_type",
            "source_id",
            "published_at_ts",
            "event_time_start_ts",
            "event_time_end_ts",
            "metadata_json",
        ]
        hits: list[MilvusHybridHit] = []
        for start in range(0, len(target_ids), settings.MILVUS_BATCH_SIZE):
            batch = target_ids[start : start + settings.MILVUS_BATCH_SIZE]
            with profile_span(
                "milvus_store.get_documents.query",
                batch_size=len(batch),
                total=len(target_ids),
            ):
                rows = self._query_with_connection_retry(
                    filter_text=_scoped_in_filter(
                        adapter_name=adapter_name,
                        target=target,
                        field="target_id",
                        values=batch,
                    ),
                    output_fields=output_fields,
                    limit=len(batch),
                )
            for row in rows or []:
                entity = row if isinstance(row, dict) else dict(row)
                hits.append(_hit_from_entity(entity, score=1.0))
        return hits

    def _upsert_rows(self, rows: list[dict[str, Any]]) -> None:
        client = self._get_client()
        for start in range(0, len(rows), settings.MILVUS_BATCH_SIZE):
            batch = rows[start : start + settings.MILVUS_BATCH_SIZE]
            with profile_span(
                "milvus_store.upsert_batch",
                batch_start=start,
                batch_size=len(batch),
                total=len(rows),
            ):
                client.upsert(collection_name=self.collection_name, data=batch)

    def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        adapter_name: str,
        target: str,
        limit: int,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
        target_type: str | None = None,
    ) -> list[MilvusHybridHit]:
        if not query_text.strip() or not query_vector:
            return []
        if not _finite_vector(query_vector):
            logger.warning("Milvus hybrid search skipped because query vector contains NaN or Inf values")
            return []
        with profile_span("milvus_store.hybrid_search_scope_check", adapter=adapter_name, target=target):
            if not self._scope_has_rows(adapter_name=adapter_name, target=target):
                _log_empty_scope_once(self.collection_name, adapter_name, target)
                profile_event(
                    "milvus_store.hybrid_search_empty_scope",
                    collection=self.collection_name,
                    adapter=adapter_name,
                    target=target,
                )
                return []
        with profile_span("milvus_store.ensure_collection", collection=self.collection_name):
            self.ensure_collection()
        imports = self._load_imports()
        dense_req = imports["AnnSearchRequest"](
            data=[query_vector],
            anns_field="dense_vector",
            param={"metric_type": self.metric_type},
            limit=limit,
        )
        sparse_req = imports["AnnSearchRequest"](
            data=[query_text],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=limit,
        )
        output_fields = [
            "target_id",
            "chunk_id",
            "evidence_id",
            "text",
            "adapter_name",
            "target",
            "target_type",
            "source_type",
            "source_id",
            "published_at_ts",
            "event_time_start_ts",
            "event_time_end_ts",
            "metadata_json",
        ]
        scope_filter = f'adapter_name == "{adapter_name}" and target == "{target}"'
        time_filter = _time_range_filter(time_start=time_start, time_end=time_end)
        if target_type:
            scope_filter = f'({scope_filter}) and target_type == "{_escape_filter_value(target_type)}"'
        if time_filter:
            scope_filter = f"({scope_filter}) and ({time_filter})"
        with profile_span(
            "milvus_store.hybrid_search_rpc",
            collection=self.collection_name,
            adapter=adapter_name,
            target=target,
            limit=limit,
            query_text_len=len(query_text),
            vector_dim=len(query_vector),
        ):
            results = self._hybrid_search_with_connection_retry(
                reqs=[dense_req, sparse_req],
                ranker=imports["RRFRanker"](k=self.rrf_k),
                scope_filter=scope_filter,
                limit=limit,
                output_fields=output_fields,
                query_vector=query_vector,
            )
        hits: list[MilvusHybridHit] = []
        for hit in results[0] if results else []:
            entity = _hit_value(hit, "entity") or {}
            hits.append(
                _hit_from_entity(
                    entity,
                    score=float(_hit_value(hit, "distance") or _hit_value(hit, "score") or 0.0),
                    fallback_chunk_id=str(_hit_value(hit, "id") or ""),
                )
            )
        profile_event("milvus_store.hybrid_search_result", hits=len(hits))
        return hits

    def vector_search(
        self,
        *,
        query_vector: list[float],
        adapter_name: str,
        target: str,
        limit: int,
        target_type: str | None = None,
    ) -> list[MilvusHybridHit]:
        if not query_vector:
            return []
        if not _finite_vector(query_vector):
            logger.warning("Milvus vector search skipped because query vector contains NaN or Inf values")
            return []
        with profile_span("milvus_store.vector_search_scope_check", adapter=adapter_name, target=target):
            if not self._scope_has_rows(adapter_name=adapter_name, target=target):
                _log_empty_scope_once(self.collection_name, adapter_name, target)
                return []
        with profile_span("milvus_store.vector_search.ensure_collection", collection=self.collection_name):
            self.ensure_collection()
        output_fields = [
            "target_id",
            "chunk_id",
            "evidence_id",
            "text",
            "adapter_name",
            "target",
            "target_type",
            "source_type",
            "source_id",
            "published_at_ts",
            "event_time_start_ts",
            "event_time_end_ts",
            "metadata_json",
        ]
        scope_filter = f'adapter_name == "{_escape_filter_value(adapter_name)}" and target == "{_escape_filter_value(target)}"'
        if target_type:
            scope_filter = f'({scope_filter}) and target_type == "{_escape_filter_value(target_type)}"'
        with profile_span(
            "milvus_store.vector_search.rpc",
            collection=self.collection_name,
            adapter=adapter_name,
            target=target,
            target_type=target_type or "",
            limit=limit,
            vector_dim=len(query_vector),
        ):
            results = self._vector_search_with_connection_retry(
                query_vector=query_vector,
                scope_filter=scope_filter,
                limit=limit,
                output_fields=output_fields,
            )
        hits: list[MilvusHybridHit] = []
        for hit in results[0] if results else []:
            entity = _hit_value(hit, "entity") or {}
            hits.append(
                _hit_from_entity(
                    entity,
                    score=float(_hit_value(hit, "distance") or _hit_value(hit, "score") or 0.0),
                    fallback_chunk_id=str(_hit_value(hit, "id") or ""),
                )
            )
        profile_event("milvus_store.vector_search_result", hits=len(hits))
        return hits

    def _scope_has_rows(self, *, adapter_name: str, target: str) -> bool:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return False
        try:
            rows = client.query(
                collection_name=self.collection_name,
                filter=f'adapter_name == "{_escape_filter_value(adapter_name)}" and target == "{_escape_filter_value(target)}"',
                output_fields=["target_id"],
                limit=1,
                consistency_level=MILVUS_READ_AFTER_WRITE_CONSISTENCY,
            )
        except Exception:
            logger.warning(
                "Failed to inspect Milvus scoped vector rows for collection=%s adapter=%s target=%s",
                self.collection_name,
                adapter_name,
                target,
                exc_info=True,
            )
            raise
        return bool(rows)

    def _get_client(self):
        if self._client is not None:
            profile_event("milvus_store.client_reuse", uri=self.uri)
            return self._client
        imports = self._load_imports()
        if self.uri.endswith(".db") or self.uri.startswith("./") or self.uri.startswith("/"):
            Path(self.uri).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        kwargs = {
            "uri": self.uri,
            "timeout": settings.MILVUS_CLIENT_TIMEOUT,
        }
        if self.token:
            kwargs["token"] = self.token
        # Use a dedicated client to avoid pymilvus reusing a stale Milvus Lite
        # Unix socket kept in the process-wide connection manager.
        kwargs["dedicated"] = True
        profile_event("milvus_store.client_create", uri=self.uri, collection=self.collection_name)
        for attempt in range(2):
            try:
                self._client = imports["MilvusClient"](**kwargs)
                break
            except Exception as exc:
                self._client = None
                if attempt > 0:
                    raise
                profile_event(
                    "milvus_store.client_create_retry",
                    uri=self.uri,
                    error=type(exc).__name__,
                )
                self._close_connection_manager(imports)
        return self._client

    def _hybrid_search_with_connection_retry(
        self,
        *,
        reqs: list[Any],
        ranker: Any,
        scope_filter: str,
        limit: int,
        output_fields: list[str],
        query_vector: list[float],
    ) -> Any:
        for attempt in range(2):
            try:
                return self._get_client().hybrid_search(
                    collection_name=self.collection_name,
                    reqs=reqs,
                    ranker=ranker,
                    filter=scope_filter,
                    limit=limit,
                    output_fields=output_fields,
                    consistency_level=MILVUS_READ_AFTER_WRITE_CONSISTENCY,
                )
            except Exception as exc:
                if _is_sparse_row_error(exc):
                    logger.warning(
                        "Milvus sparse BM25 hybrid search failed; falling back to dense-only search: %s",
                        exc,
                    )
                    profile_event(
                        "milvus_store.hybrid_search_dense_fallback",
                        error=type(exc).__name__,
                        reason="sparse_vector_nan_or_inf",
                    )
                    return self._get_client().search(
                        collection_name=self.collection_name,
                        data=[query_vector],
                        anns_field="dense_vector",
                        search_params={"metric_type": self.metric_type},
                        filter=scope_filter,
                        limit=limit,
                        output_fields=output_fields,
                        consistency_level=MILVUS_READ_AFTER_WRITE_CONSISTENCY,
                    )
                if attempt == 0 and _is_transient_milvus_connection_error(exc):
                    logger.warning(
                        "Milvus hybrid search connection failed; resetting client and retrying once: %s",
                        exc,
                    )
                    profile_event(
                        "milvus_store.hybrid_search_connection_retry",
                        collection=self.collection_name,
                        error=type(exc).__name__,
                    )
                    self._reset_client_connection()
                    continue
                raise

    def _vector_search_with_connection_retry(
        self,
        *,
        query_vector: list[float],
        scope_filter: str,
        limit: int,
        output_fields: list[str],
    ) -> Any:
        for attempt in range(2):
            try:
                return self._get_client().search(
                    collection_name=self.collection_name,
                    data=[query_vector],
                    anns_field="dense_vector",
                    search_params={"metric_type": self.metric_type},
                    filter=scope_filter,
                    limit=limit,
                    output_fields=output_fields,
                    consistency_level=MILVUS_READ_AFTER_WRITE_CONSISTENCY,
                )
            except Exception as exc:
                if attempt == 0 and _is_transient_milvus_connection_error(exc):
                    logger.warning(
                        "Milvus vector search connection failed; resetting client and retrying once: %s",
                        exc,
                    )
                    profile_event(
                        "milvus_store.vector_search_connection_retry",
                        collection=self.collection_name,
                        error=type(exc).__name__,
                    )
                    self._reset_client_connection()
                    continue
                raise

    def _query_with_connection_retry(
        self,
        *,
        filter_text: str,
        output_fields: list[str],
        limit: int,
    ) -> Any:
        for attempt in range(2):
            try:
                return self._get_client().query(
                    collection_name=self.collection_name,
                    filter=filter_text,
                    output_fields=output_fields,
                    limit=limit,
                    consistency_level=MILVUS_READ_AFTER_WRITE_CONSISTENCY,
                )
            except Exception as exc:
                if attempt == 0 and _is_transient_milvus_connection_error(exc):
                    logger.warning(
                        "Milvus exact query connection failed; resetting client and retrying once: %s",
                        exc,
                    )
                    profile_event(
                        "milvus_store.exact_query_connection_retry",
                        collection=self.collection_name,
                        error=type(exc).__name__,
                    )
                    self._reset_client_connection()
                    continue
                raise

    def _reset_client_connection(self) -> None:
        imports = self._load_imports()
        self.close()
        self._close_connection_manager(imports)

    def _load_imports(self) -> dict[str, Any]:
        if self._imports is not None:
            return self._imports
        try:
            from pymilvus import (  # type: ignore
                AnnSearchRequest,
                DataType,
                Function,
                FunctionType,
                MilvusClient,
                RRFRanker,
            )
            from pymilvus.client.connection_manager import ConnectionManager  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional local package
            raise RuntimeError("pymilvus is required for Milvus hybrid retrieval") from exc
        self._imports = {
            "AnnSearchRequest": AnnSearchRequest,
            "ConnectionManager": ConnectionManager,
            "DataType": DataType,
            "Function": Function,
            "FunctionType": FunctionType,
            "MilvusClient": MilvusClient,
            "RRFRanker": RRFRanker,
        }
        return self._imports

    def _close_connection_manager(self, imports: dict[str, Any]) -> None:
        try:
            imports["ConnectionManager"].get_instance().close_all()
        except Exception:
            logger.warning("Failed to close pymilvus connection manager", exc_info=True)


class MilvusTypedHybridStore:
    """Typed Milvus collection registry for KG semantic targets."""

    def __init__(
        self,
        *,
        registry: MilvusCollectionRegistry | None = None,
        uri: str | None = None,
        token: str | None = None,
        dim: int | None = None,
        metric_type: str | None = None,
        rrf_k: int | None = None,
    ):
        self.registry = registry or MilvusCollectionRegistry.from_settings()
        self._stores = {
            role: MilvusHybridStore(
                uri=uri,
                token=token,
                collection_name=self.registry.name_for(role),
                dim=dim,
                metric_type=metric_type,
                rrf_k=rrf_k,
            )
            for role in MILVUS_COLLECTION_ROLES
        }

    @property
    def available(self) -> bool:
        try:
            self.ensure_ready()
            return True
        except Exception:
            return False

    def ensure_ready(self) -> None:
        # Connectivity is URI-scoped. Opening one dedicated gRPC client per
        # collection here creates unnecessary keepalive traffic in Milvus Lite;
        # each role opens its own client lazily when first used.
        role, store = next(iter(self._stores.items()))
        with profile_span("milvus_typed_store.ensure_ready", collection_role=role):
            store.ensure_ready()

    def close(self) -> None:
        for store in self._stores.values():
            store.close()

    def store_for(self, collection_role: str) -> MilvusHybridStore:
        try:
            return self._stores[collection_role]
        except KeyError as exc:
            raise ValueError(f"unsupported Milvus collection role: {collection_role}") from exc

    def hybrid_search(
        self,
        *,
        collection_role: str,
        query_text: str,
        query_vector: list[float],
        adapter_name: str,
        target: str,
        limit: int,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
        target_type: str | None = None,
    ) -> list[MilvusHybridHit]:
        return self.store_for(collection_role).hybrid_search(
            query_text=query_text,
            query_vector=query_vector,
            adapter_name=adapter_name,
            target=target,
            limit=limit,
            time_start=time_start,
            time_end=time_end,
            target_type=target_type,
        )

    def vector_search(
        self,
        *,
        collection_role: str,
        query_vector: list[float],
        adapter_name: str,
        target: str,
        limit: int,
        target_type: str | None = None,
    ) -> list[MilvusHybridHit]:
        return self.store_for(collection_role).vector_search(
            query_vector=query_vector,
            adapter_name=adapter_name,
            target=target,
            limit=limit,
            target_type=target_type,
        )

    def get_documents(
        self,
        *,
        collection_role: str,
        adapter_name: str,
        target: str,
        target_ids: list[str],
    ) -> list[MilvusHybridHit]:
        return self.store_for(collection_role).get_documents(
            adapter_name=adapter_name,
            target=target,
            target_ids=target_ids,
        )

    def replace_documents_by_role(
        self,
        *,
        adapter_name: str,
        target: str,
        documents_by_role: dict[str, list[MilvusHybridDocument]],
        vectors_by_role: dict[str, list[list[float]]],
        embedding_model: str,
        kg_version: str = "",
    ) -> int:
        total = 0
        for role in self._stores:
            if role == MILVUS_COLLECTION_COMMUNITY_INSIGHT and not documents_by_role.get(role):
                continue
            total += self.store_for(role).replace_documents(
                adapter_name=adapter_name,
                target=target,
                documents=documents_by_role.get(role, []),
                vectors=vectors_by_role.get(role, []),
                embedding_model=embedding_model,
                kg_version=kg_version,
            )
        return total

    def upsert_documents_by_role(
        self,
        *,
        adapter_name: str,
        target: str,
        documents_by_role: dict[str, list[MilvusHybridDocument]],
        vectors_by_role: dict[str, list[list[float]]],
        embedding_model: str,
        kg_version: str = "",
    ) -> int:
        total = 0
        for role, documents in documents_by_role.items():
            if not documents:
                continue
            total += self.store_for(role).upsert_documents(
                adapter_name=adapter_name,
                target=target,
                documents=documents,
                vectors=vectors_by_role.get(role, []),
                embedding_model=embedding_model,
                kg_version=kg_version,
            )
        return total

    def delete_evidence(self, *, adapter_name: str, target: str, evidence_ids: list[str]) -> None:
        for store in self._stores.values():
            store.delete_evidence(adapter_name=adapter_name, target=target, evidence_ids=evidence_ids)

    def delete_documents(self, *, adapter_name: str, target: str, chunk_ids: list[str]) -> None:
        for store in self._stores.values():
            store.delete_documents(adapter_name=adapter_name, target=target, chunk_ids=chunk_ids)

    def delete_documents_by_role(
        self,
        *,
        collection_role: str,
        adapter_name: str,
        target: str,
        target_ids: list[str],
    ) -> None:
        self.store_for(collection_role).delete_documents(
            adapter_name=adapter_name,
            target=target,
            chunk_ids=target_ids,
        )

    def list_target_ids(
        self,
        *,
        collection_role: str,
        adapter_name: str,
        target: str,
        source_type: str | None = None,
        limit: int = 1_000_000,
    ) -> list[str]:
        return self.store_for(collection_role).list_target_ids(
            adapter_name=adapter_name,
            target=target,
            source_type=source_type,
            limit=limit,
        )

    def delete_scope(self, *, adapter_name: str, target: str) -> None:
        for store in self._stores.values():
            store.delete_scope(adapter_name=adapter_name, target=target)

def _content_hash(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _document_rows(
    *,
    adapter_name: str,
    target: str,
    documents: list[MilvusHybridDocument],
    vectors: list[list[float]],
    embedding_model: str,
    kg_version: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for document, vector in zip(documents, vectors, strict=False):
        payload = dict(document.metadata or {})
        source_type = str(payload.get("source_type") or payload.get("source_table") or "")
        source_id = str(payload.get("source_id") or payload.get("source_pk") or "")
        published_at_ts = _timestamp_value(
            payload.get("published_at")
            or payload.get("source_published_at")
            or payload.get("observed_at")
            or payload.get("created_at")
        )
        event_time_start_ts = _timestamp_value(
            payload.get("event_time_start")
            or payload.get("event_time")
            or payload.get("earliest_evidence_at")
            or payload.get("earliest_source_published_at")
        )
        event_time_end_ts = _timestamp_value(
            payload.get("event_time_end")
            or payload.get("event_time")
            or payload.get("latest_evidence_at")
            or payload.get("latest_source_published_at")
        )
        if event_time_start_ts == 0 and published_at_ts:
            event_time_start_ts = published_at_ts
        if event_time_end_ts == 0 and published_at_ts:
            event_time_end_ts = published_at_ts
        rows.append(
            {
                "target_id": document.target_id,
                "chunk_id": document.chunk_id,
                "evidence_id": document.evidence_id,
                "adapter_name": adapter_name,
                "target": target,
                "target_type": str(payload.get("target_type") or payload.get("document_type") or target)[:64],
                "source_type": source_type[:80],
                "source_id": source_id[:160],
                # Milvus VARCHAR max_length 按 UTF-8 字节校验，而 Python 切片按
                # Unicode 字符计数。中文正文必须按字节边界裁剪，否则全量重建
                # 会在单行超过 8192 bytes 时拒绝整个 upsert batch。
                "text": _truncate_utf8(document.text, MILVUS_TEXT_MAX_BYTES),
                "dense_vector": vector,
                "content_hash": _content_hash(document.text),
                "embedding_model": embedding_model[:80],
                "kg_version": kg_version[:80],
                "published_at_ts": published_at_ts,
                "event_time_start_ts": event_time_start_ts,
                "event_time_end_ts": event_time_end_ts,
                "metadata_json": _json_safe_metadata(payload),
            }
        )
    return rows


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Return a valid UTF-8 string whose encoded length does not exceed max_bytes."""
    if max_bytes <= 0:
        return ""
    encoded = str(value or "").encode("utf-8")
    if len(encoded) <= max_bytes:
        return str(value or "")
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _hit_from_entity(
    entity: dict[str, Any],
    *,
    score: float,
    fallback_chunk_id: str = "",
) -> MilvusHybridHit:
    metadata = {key: value for key, value in entity.items() if key not in {"text", "metadata_json"}}
    raw_metadata = entity.get("metadata_json")
    if isinstance(raw_metadata, dict):
        metadata.update(raw_metadata)
    for key in ("published_at_ts", "event_time_start_ts", "event_time_end_ts"):
        if key in entity:
            metadata[key] = entity.get(key)
    return MilvusHybridHit(
        chunk_id=str(entity.get("chunk_id") or entity.get("target_id") or fallback_chunk_id or ""),
        evidence_id=str(entity.get("evidence_id") or metadata.get("evidence_id") or ""),
        text=str(entity.get("text") or ""),
        score=score,
        metadata=metadata,
    )


def _json_safe_metadata(value: dict[str, Any]) -> dict[str, Any]:
    return {
        str(key): _json_safe_value(item)
        for key, item in value.items()
        if key not in {"text", "dense_vector", "sparse_vector"}
    }


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value if not isinstance(value, float) or math.isfinite(value) else str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(item) for item in value]
    return str(value)


def _validate_document_vectors(
    documents: list[MilvusHybridDocument],
    vectors: list[list[float]],
    *,
    dim: int,
) -> None:
    if not documents:
        if vectors:
            raise ValueError(
                f"Milvus index received {len(vectors)} vector(s) for 0 document(s); refusing inconsistent index write"
            )
        return
    if len(vectors) != len(documents):
        raise ValueError(
            f"Milvus index vector/document count mismatch: documents={len(documents)} vectors={len(vectors)}. "
            "Refusing to delete or replace the existing vector index."
        )
    invalid: list[str] = []
    for document, vector in zip(documents, vectors, strict=True):
        if not vector or len(vector) != dim or not _finite_vector(vector):
            invalid.append(document.chunk_id)
        if len(invalid) >= 5:
            break
    if invalid:
        raise ValueError(
            f"Milvus index received invalid dense vector(s): count>={len(invalid)} dim={dim} "
            f"sample_chunk_ids={invalid}. Refusing to delete or replace the existing vector index."
        )


def _document_from_chunk(chunk: EvidenceChunk) -> MilvusHybridDocument:
    payload = dict(chunk.payload or {})
    payload.setdefault("source_type", "kg_evidence")
    payload.setdefault("source_id", chunk.evidence_id)
    return MilvusHybridDocument(
        chunk_id=chunk.chunk_id,
        evidence_id=chunk.evidence_id,
        text=chunk.content,
        metadata=payload,
    )


def _timestamp_value(value: Any) -> int:
    parsed = _parse_datetime_value(value)
    if parsed is None:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _parse_datetime_value(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        number = float(value)
        if number <= 0:
            return None
        if number > 10_000_000_000:
            number = number / 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_range_filter(*, time_start: datetime | None, time_end: datetime | None) -> str:
    start_ts = _timestamp_value(time_start)
    end_ts = _timestamp_value(time_end)
    if start_ts <= 0 and end_ts <= 0:
        return ""
    filters: list[str] = []
    if start_ts > 0:
        filters.append(f"event_time_end_ts >= {start_ts}")
    if end_ts > 0:
        filters.append(f"event_time_start_ts <= {end_ts}")
    filters.append("event_time_start_ts > 0")
    filters.append("event_time_end_ts > 0")
    return " and ".join(filters)


def _collection_dense_dim(client, collection_name: str) -> int | None:
    fields = _collection_fields(client, collection_name)
    if not isinstance(fields, list):
        return None
    for field in fields:
        name = field.get("name") if isinstance(field, dict) else getattr(field, "name", None)
        if name != "dense_vector":
            continue
        params = field.get("params") if isinstance(field, dict) else getattr(field, "params", None)
        dim = None
        if isinstance(params, dict):
            dim = params.get("dim")
        if dim is None and isinstance(field, dict):
            dim = field.get("dim")
        try:
            return int(dim) if dim is not None else None
        except (TypeError, ValueError):
            return None
    return None


def _collection_has_field(client, collection_name: str, field_name: str) -> bool:
    fields = _collection_fields(client, collection_name)
    if not isinstance(fields, list):
        return False
    for field in fields:
        name = field.get("name") if isinstance(field, dict) else getattr(field, "name", None)
        if name == field_name:
            return True
    return False


def _collection_fields(client, collection_name: str):
    describe = getattr(client, "describe_collection", None)
    if describe is None:
        return None
    try:
        info = describe(collection_name=collection_name)
    except TypeError:
        info = describe(collection_name)
    except Exception:
        logger.warning("Failed to inspect Milvus collection schema for %s", collection_name, exc_info=True)
        return None
    fields = info.get("fields") if isinstance(info, dict) else getattr(info, "fields", None)
    return fields if isinstance(fields, list) else None


def _is_sparse_row_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "Invalid sparse row" in message
        or "std::isfinite(element.val)" in message
        or "NaN or Inf" in message
    )


def _is_transient_milvus_connection_error(exc: Exception) -> bool:
    message = str(exc).lower()
    indicators = (
        "connection reset by peer",
        "statuscode.unavailable",
        "grpc_status:14",
        "server unavailable",
        "fail connecting to server",
        "channel distribution is not serviceable",
        "channel not available",
        "failed to search/query delegator",
        "channel_ready",
        "futuretimeouterror",
        "too_many_pings",
        "enhance_your_calm",
        "goaway",
    )
    return any(indicator in message for indicator in indicators)


def _finite_vector(vector: list[float]) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in vector)
    except (TypeError, ValueError):
        return False


def _log_empty_scope_once(collection_name: str, adapter_name: str, target: str) -> None:
    key = (collection_name, adapter_name, target)
    message = (
        "Milvus hybrid search skipped because scoped vector index is empty: "
        "collection=%s adapter=%s target=%s. Rebuild the semantic hybrid index "
        "before expecting semantic_hybrid results."
    )
    if key in _EMPTY_SCOPE_WARNED:
        logger.debug(message, collection_name, adapter_name, target)
        return
    _EMPTY_SCOPE_WARNED.add(key)
    logger.warning(message, collection_name, adapter_name, target)


def _hit_value(hit: Any, key: str) -> Any:
    if isinstance(hit, dict):
        return hit.get(key)
    try:
        return hit[key]
    except Exception:
        return getattr(hit, key, None)


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _scoped_in_filter(*, adapter_name: str, target: str, field: str, values: list[str]) -> str:
    scope = (
        f'adapter_name == "{_escape_filter_value(adapter_name)}" '
        f'and target == "{_escape_filter_value(target)}"'
    )
    unique_values = [value for value in dict.fromkeys(values) if value]
    if len(unique_values) == 1:
        return f'{scope} and {field} == "{_escape_filter_value(unique_values[0])}"'
    quoted_values = ", ".join(f'"{_escape_filter_value(value)}"' for value in unique_values)
    return f"{scope} and {field} in [{quoted_values}]"
