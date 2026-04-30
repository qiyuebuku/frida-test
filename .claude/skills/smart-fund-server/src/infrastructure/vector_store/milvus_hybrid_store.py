"""Milvus dense+sparse hybrid index for KG evidence chunks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.domain.knowledge.schemas import EvidenceChunk
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
        self._load_imports()
        self._get_client()

    def ensure_collection(self) -> None:
        imports = self._load_imports()
        client = self._get_client()
        if client.has_collection(self.collection_name):
            return

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
        self.ensure_collection()
        self.delete_scope(adapter_name=adapter_name, target=target)
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
        client = self._get_client()
        for start in range(0, len(rows), settings.MILVUS_BATCH_SIZE):
            client.insert(
                collection_name=self.collection_name,
                data=rows[start : start + settings.MILVUS_BATCH_SIZE],
            )
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
        self.ensure_collection()
        self.delete_documents(
            adapter_name=adapter_name,
            target=target,
            chunk_ids=[document.chunk_id for document in documents],
        )
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
        client = self._get_client()
        for start in range(0, len(rows), settings.MILVUS_BATCH_SIZE):
            client.insert(
                collection_name=self.collection_name,
                data=rows[start : start + settings.MILVUS_BATCH_SIZE],
            )
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
        for chunk_id in chunk_ids:
            client.delete(
                collection_name=self.collection_name,
                filter=(
                    f'adapter_name == "{_escape_filter_value(adapter_name)}" '
                    f'and target == "{_escape_filter_value(target)}" '
                    f'and chunk_id == "{_escape_filter_value(chunk_id)}"'
                ),
            )

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
        results = self._get_client().hybrid_search(
            collection_name=self.collection_name,
            reqs=[dense_req, sparse_req],
            ranker=imports["RRFRanker"](k=self.rrf_k),
            filter=f'adapter_name == "{adapter_name}" and target == "{target}"',
            limit=limit,
            output_fields=[
                "chunk_id",
                "evidence_id",
                "text",
                "adapter_name",
                "target",
                "source_type",
                "source_id",
            ],
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
        return hits

    def _get_client(self):
        if self._client is not None:
            return self._client
        imports = self._load_imports()
        if self.uri.endswith(".db") or self.uri.startswith("./") or self.uri.startswith("/"):
            Path(self.uri).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        self._client = imports["MilvusClient"](**kwargs)
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
        except Exception as exc:  # pragma: no cover - depends on optional local package
            raise RuntimeError("pymilvus is required for Milvus hybrid retrieval") from exc
        self._imports = {
            "AnnSearchRequest": AnnSearchRequest,
            "DataType": DataType,
            "Function": Function,
            "FunctionType": FunctionType,
            "MilvusClient": MilvusClient,
            "RRFRanker": RRFRanker,
        }
        return self._imports


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
        if not vector:
            continue
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


def _hit_value(hit: Any, key: str) -> Any:
    if isinstance(hit, dict):
        return hit.get(key)
    try:
        return hit[key]
    except Exception:
        return getattr(hit, key, None)


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
