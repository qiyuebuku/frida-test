"""Milvus dense+sparse hybrid index for KG evidence chunks."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.domain.knowledge.schemas import EvidenceChunk
from src.domain.knowledge.retrieval_profile import profile_event, profile_span
from src.infrastructure.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MilvusHybridHit:
    chunk_id: str
    evidence_id: str
    text: str
    score: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MilvusHybridDocument:
    chunk_id: str
    text: str
    evidence_id: str = ""
    metadata: dict[str, Any] | None = None


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
            if existing_dim in {None, self.dim}:
                return
            if not recreate_on_dim_mismatch:
                raise RuntimeError(
                    f"Milvus collection {self.collection_name} dense_vector dim mismatch: "
                    f"existing={existing_dim} configured={self.dim}. "
                    "Rebuild the semantic hybrid index before retrieval."
                )
            logger.warning(
                "Milvus collection %s dense_vector dim mismatch: existing=%s configured=%s; recreating collection",
                self.collection_name,
                existing_dim,
                self.dim,
            )
            client.drop_collection(self.collection_name)

        schema = client.create_schema()
        data_type = imports["DataType"]
        schema.add_field("chunk_id", data_type.VARCHAR, is_primary=True, max_length=220)
        schema.add_field("evidence_id", data_type.VARCHAR, max_length=180)
        schema.add_field("adapter_name", data_type.VARCHAR, max_length=64)
        schema.add_field("target", data_type.VARCHAR, max_length=16)
        schema.add_field("source_type", data_type.VARCHAR, max_length=80)
        schema.add_field("source_id", data_type.VARCHAR, max_length=160)
        schema.add_field("text", data_type.VARCHAR, max_length=8192, enable_analyzer=True)
        schema.add_field("dense_vector", data_type.FLOAT_VECTOR, dim=self.dim)
        schema.add_field("sparse_vector", data_type.SPARSE_FLOAT_VECTOR)
        schema.add_field("content_hash", data_type.VARCHAR, max_length=64)
        schema.add_field("embedding_model", data_type.VARCHAR, max_length=80)
        schema.add_field("kg_version", data_type.VARCHAR, max_length=80)

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
            self._insert_rows(rows)
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
        with profile_span("milvus_store.upsert_documents.delete_existing", documents=len(documents)):
            self.delete_documents(
                adapter_name=adapter_name,
                target=target,
                chunk_ids=[document.chunk_id for document in documents],
            )
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
            self._insert_rows(rows)
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
                    field="chunk_id",
                    values=batch,
                ),
            )

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

    def _insert_rows(self, rows: list[dict[str, Any]]) -> None:
        client = self._get_client()
        for start in range(0, len(rows), settings.MILVUS_BATCH_SIZE):
            batch = rows[start : start + settings.MILVUS_BATCH_SIZE]
            with profile_span(
                "milvus_store.insert_batch",
                batch_start=start,
                batch_size=len(batch),
                total=len(rows),
            ):
                client.insert(collection_name=self.collection_name, data=batch)

    def hybrid_search(
        self,
        *,
        query_text: str,
        query_vector: list[float],
        adapter_name: str,
        target: str,
        limit: int,
    ) -> list[MilvusHybridHit]:
        if not query_text.strip() or not query_vector:
            return []
        if not _finite_vector(query_vector):
            logger.warning("Milvus hybrid search skipped because query vector contains NaN or Inf values")
            return []
        with profile_span("milvus_store.ensure_collection", collection=self.collection_name):
            self.ensure_collection()
        with profile_span("milvus_store.hybrid_search_scope_check", adapter=adapter_name, target=target):
            if not self._scope_has_rows(adapter_name=adapter_name, target=target):
                logger.warning(
                    "Milvus hybrid search skipped because scoped vector index is empty: collection=%s adapter=%s target=%s. "
                    "Rebuild the semantic hybrid index before expecting semantic_hybrid results.",
                    self.collection_name,
                    adapter_name,
                    target,
                )
                profile_event(
                    "milvus_store.hybrid_search_empty_scope",
                    collection=self.collection_name,
                    adapter=adapter_name,
                    target=target,
                )
                return []
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
            "chunk_id",
            "evidence_id",
            "text",
            "adapter_name",
            "target",
            "source_type",
            "source_id",
        ]
        scope_filter = f'adapter_name == "{adapter_name}" and target == "{target}"'
        with profile_span(
            "milvus_store.hybrid_search_rpc",
            collection=self.collection_name,
            adapter=adapter_name,
            target=target,
            limit=limit,
            query_text_len=len(query_text),
            vector_dim=len(query_vector),
        ):
            try:
                results = self._get_client().hybrid_search(
                    collection_name=self.collection_name,
                    reqs=[dense_req, sparse_req],
                    ranker=imports["RRFRanker"](k=self.rrf_k),
                    filter=scope_filter,
                    limit=limit,
                    output_fields=output_fields,
                )
            except Exception as exc:
                if not _is_sparse_row_error(exc):
                    raise
                logger.warning(
                    "Milvus sparse BM25 hybrid search failed; falling back to dense-only search: %s",
                    exc,
                )
                profile_event(
                    "milvus_store.hybrid_search_dense_fallback",
                    error=type(exc).__name__,
                    reason="sparse_vector_nan_or_inf",
                )
                results = self._get_client().search(
                    collection_name=self.collection_name,
                    data=[query_vector],
                    anns_field="dense_vector",
                    search_params={"metric_type": self.metric_type},
                    filter=scope_filter,
                    limit=limit,
                    output_fields=output_fields,
                )
        hits: list[MilvusHybridHit] = []
        for hit in results[0] if results else []:
            entity = _hit_value(hit, "entity") or {}
            hits.append(
                MilvusHybridHit(
                    chunk_id=str(entity.get("chunk_id") or _hit_value(hit, "id") or ""),
                    evidence_id=str(entity.get("evidence_id") or ""),
                    text=str(entity.get("text") or ""),
                    score=float(_hit_value(hit, "distance") or _hit_value(hit, "score") or 0.0),
                    metadata={key: value for key, value in entity.items() if key != "text"},
                )
            )
        profile_event("milvus_store.hybrid_search_result", hits=len(hits))
        return hits

    def _scope_has_rows(self, *, adapter_name: str, target: str) -> bool:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return False
        try:
            rows = client.query(
                collection_name=self.collection_name,
                filter=f'adapter_name == "{_escape_filter_value(adapter_name)}" and target == "{_escape_filter_value(target)}"',
                output_fields=["chunk_id"],
                limit=1,
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
        kwargs = {"uri": self.uri}
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
        rows.append(
            {
                "chunk_id": document.chunk_id,
                "evidence_id": document.evidence_id,
                "adapter_name": adapter_name,
                "target": target,
                "source_type": source_type[:80],
                "source_id": source_id[:160],
                "text": document.text[:8192],
                "dense_vector": vector,
                "content_hash": _content_hash(document.text),
                "embedding_model": embedding_model[:80],
                "kg_version": kg_version[:80],
            }
        )
    return rows


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


def _collection_dense_dim(client, collection_name: str) -> int | None:
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


def _is_sparse_row_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "Invalid sparse row" in message
        or "std::isfinite(element.val)" in message
        or "NaN or Inf" in message
    )


def _finite_vector(vector: list[float]) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in vector)
    except (TypeError, ValueError):
        return False


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
