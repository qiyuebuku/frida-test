import logging

import pytest

from src.infrastructure.vector_store.milvus_hybrid_store import (
    MILVUS_COLLECTION_CHUNK,
    MILVUS_COLLECTION_ENTITY,
    MILVUS_COLLECTION_RELATION,
    MilvusCollectionRegistry,
    MilvusHybridDocument,
    MilvusHybridStore,
    MilvusTypedHybridStore,
)


def test_replace_documents_refuses_vector_count_mismatch_before_delete() -> None:
    store = MilvusHybridStore(dim=3)

    with pytest.raises(ValueError, match="vector/document count mismatch"):
        store.replace_documents(
            adapter_name="financial",
            target="prod",
            documents=[MilvusHybridDocument(chunk_id="doc-1", text="hello")],
            vectors=[],
            embedding_model="test-embedding",
        )


def test_replace_documents_refuses_invalid_vector_before_delete() -> None:
    store = MilvusHybridStore(dim=3)

    with pytest.raises(ValueError, match="invalid dense vector"):
        store.replace_documents(
            adapter_name="financial",
            target="prod",
            documents=[MilvusHybridDocument(chunk_id="doc-1", text="hello")],
            vectors=[[1.0, float("nan"), 3.0]],
            embedding_model="test-embedding",
        )


def test_hybrid_search_returns_empty_before_bm25_when_scope_has_no_rows() -> None:
    class FakeClient:
        def has_collection(self, collection_name: str) -> bool:
            return True

        def query(self, **kwargs):
            return []

    class Store(MilvusHybridStore):
        def ensure_collection(self, *, recreate_on_dim_mismatch: bool = False) -> None:
            return None

        def _get_client(self):
            return FakeClient()

    store = Store(dim=3)

    assert (
        store.hybrid_search(
            query_text="A股并购重组",
            query_vector=[0.1, 0.2, 0.3],
            adapter_name="financial",
            target="prod",
            limit=5,
        )
        == []
    )


def test_hybrid_search_empty_scope_warning_is_rate_limited(caplog) -> None:
    class FakeClient:
        def has_collection(self, collection_name: str) -> bool:
            return True

        def query(self, **kwargs):
            return []

    class Store(MilvusHybridStore):
        def ensure_collection(self, *, recreate_on_dim_mismatch: bool = False) -> None:
            return None

        def _get_client(self):
            return FakeClient()

    store = Store(dim=3, collection_name="unit_empty_scope")

    with caplog.at_level(logging.WARNING, logger="src.infrastructure.vector_store.milvus_hybrid_store"):
        for _ in range(3):
            assert (
                store.hybrid_search(
                    query_text="A股并购重组",
                    query_vector=[0.1, 0.2, 0.3],
                    adapter_name="financial",
                    target="prod",
                    limit=5,
                )
                == []
            )

    messages = [
        record.getMessage()
        for record in caplog.records
        if "scoped vector index is empty" in record.getMessage()
    ]
    assert len(messages) == 1


def test_hybrid_search_retries_once_after_transient_connection_reset() -> None:
    class FakeAnnSearchRequest:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeRRFRanker:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeClient:
        def __init__(self, *, fail: bool):
            self.fail = fail
            self.closed = False
            self.hybrid_search_calls = 0

        def has_collection(self, collection_name: str) -> bool:
            return True

        def query(self, **kwargs):
            return [{"target_id": "doc-1"}]

        def hybrid_search(self, **kwargs):
            self.hybrid_search_calls += 1
            if self.fail:
                raise RuntimeError("StatusCode.UNAVAILABLE recvmsg:Connection reset by peer")
            return [
                [
                    {
                        "entity": {
                            "target_id": "doc-1",
                            "chunk_id": "doc-1",
                            "evidence_id": "ev-1",
                            "text": "AI 算力链",
                            "adapter_name": "financial",
                            "target": "prod",
                            "target_type": "community",
                            "source_type": "kg_graph_community",
                            "source_id": "community-1",
                            "metadata_json": {},
                        },
                        "score": 0.9,
                    }
                ]
            ]

        def close(self):
            self.closed = True

    first_client = FakeClient(fail=True)
    second_client = FakeClient(fail=False)

    class Store(MilvusHybridStore):
        def __init__(self):
            super().__init__(dim=3)
            self.clients = [first_client, second_client]
            self.connection_manager_closed = 0

        def _load_imports(self):
            return {
                "AnnSearchRequest": FakeAnnSearchRequest,
                "RRFRanker": FakeRRFRanker,
            }

        def ensure_collection(self, *, recreate_on_dim_mismatch: bool = False) -> None:
            return None

        def _get_client(self):
            if self._client is not None:
                return self._client
            self._client = self.clients.pop(0)
            return self._client

        def _close_connection_manager(self, imports):
            self.connection_manager_closed += 1

    store = Store()

    hits = store.hybrid_search(
        query_text="AI算力链",
        query_vector=[0.1, 0.2, 0.3],
        adapter_name="financial",
        target="prod",
        limit=5,
    )

    assert len(hits) == 1
    assert hits[0].target_id == "doc-1"
    assert first_client.hybrid_search_calls == 1
    assert first_client.closed is True
    assert second_client.hybrid_search_calls == 1
    assert store.connection_manager_closed == 1


def test_upsert_documents_uses_target_id_primary_key_without_delete() -> None:
    class FakeClient:
        def __init__(self):
            self.upsert_calls = []
            self.delete_calls = []

        def has_collection(self, collection_name: str) -> bool:
            return True

        def upsert(self, **kwargs):
            self.upsert_calls.append(kwargs)

        def delete(self, **kwargs):
            self.delete_calls.append(kwargs)

    fake_client = FakeClient()

    class Store(MilvusHybridStore):
        def ensure_collection(self, *, recreate_on_dim_mismatch: bool = False) -> None:
            return None

        def _get_client(self):
            return fake_client

    store = Store(dim=3)

    count = store.upsert_documents(
        adapter_name="financial",
        target="prod",
        documents=[
            MilvusHybridDocument(
                chunk_id="legacy-chunk-id",
                text="宁德时代 海外产能",
                evidence_id="ev:1",
                metadata={
                    "target_id": "kg_chunk:ev:1:0",
                    "target_type": "evidence_chunk",
                    "source_type": "kg_evidence_chunk",
                    "source_id": "ev:1",
                },
            )
        ],
        vectors=[[0.1, 0.2, 0.3]],
        embedding_model="test-embedding",
    )

    assert count == 1
    assert fake_client.delete_calls == []
    assert fake_client.upsert_calls[0]["data"][0]["target_id"] == "kg_chunk:ev:1:0"
    assert fake_client.upsert_calls[0]["data"][0]["chunk_id"] == "legacy-chunk-id"
    assert fake_client.upsert_calls[0]["data"][0]["metadata_json"]["target_id"] == "kg_chunk:ev:1:0"
    assert fake_client.upsert_calls[0]["data"][0]["metadata_json"]["source_type"] == "kg_evidence_chunk"


def test_get_documents_queries_by_target_id_and_returns_hits() -> None:
    class FakeClient:
        def __init__(self):
            self.query_calls = []

        def has_collection(self, collection_name: str) -> bool:
            return True

        def query(self, **kwargs):
            self.query_calls.append(kwargs)
            return [
                {
                    "target_id": "kg_chunk:ev:1:0",
                    "chunk_id": "kg_chunk:ev:1:0",
                    "evidence_id": "ev:1",
                    "text": "宁德时代 海外产能",
                    "adapter_name": "financial",
                    "target": "prod",
                    "target_type": "evidence_chunk",
                    "source_type": "kg_evidence_chunk",
                    "source_id": "ev:1",
                    "metadata_json": {
                        "target_id": "kg_chunk:ev:1:0",
                        "node_ids": ["kg:financial:stock:300750"],
                    },
                }
            ]

    fake_client = FakeClient()

    class Store(MilvusHybridStore):
        def ensure_collection(self, *, recreate_on_dim_mismatch: bool = False) -> None:
            return None

        def _get_client(self):
            return fake_client

    store = Store(dim=3)

    hits = store.get_documents(
        adapter_name="financial",
        target="prod",
        target_ids=["kg_chunk:ev:1:0"],
    )

    assert len(hits) == 1
    assert hits[0].target_id == "kg_chunk:ev:1:0"
    assert hits[0].text == "宁德时代 海外产能"
    assert hits[0].metadata["node_ids"] == ["kg:financial:stock:300750"]
    assert 'target_id == "kg_chunk:ev:1:0"' in fake_client.query_calls[0]["filter"]
    assert "chunk_id ==" not in fake_client.query_calls[0]["filter"]


def test_collection_registry_names_are_explicit() -> None:
    registry = MilvusCollectionRegistry(
        chunk="kg_evidence_chunks",
        entity="kg_entity_cards",
        relation="kg_relation_cards",
        community="kg_community_reports",
    )

    assert registry.name_for(MILVUS_COLLECTION_CHUNK) == "kg_evidence_chunks"
    assert registry.name_for(MILVUS_COLLECTION_ENTITY) == "kg_entity_cards"
    assert registry.name_for(MILVUS_COLLECTION_RELATION) == "kg_relation_cards"


def test_typed_store_writes_documents_to_role_specific_collections() -> None:
    class FakeStore:
        def __init__(self, collection_name: str):
            self.collection_name = collection_name
            self.replace_calls = []

        def replace_documents(self, **kwargs):
            self.replace_calls.append(kwargs)
            return len(kwargs["documents"])

    registry = MilvusCollectionRegistry(
        chunk="kg_evidence_chunks",
        entity="kg_entity_cards",
        relation="kg_relation_cards",
        community="kg_community_reports",
    )
    typed = MilvusTypedHybridStore(registry=registry, dim=3)
    typed._stores = {
        MILVUS_COLLECTION_CHUNK: FakeStore("kg_evidence_chunks"),
        MILVUS_COLLECTION_ENTITY: FakeStore("kg_entity_cards"),
        MILVUS_COLLECTION_RELATION: FakeStore("kg_relation_cards"),
        "community": FakeStore("kg_community_reports"),
    }

    count = typed.replace_documents_by_role(
        adapter_name="financial",
        target="prod",
        documents_by_role={
            MILVUS_COLLECTION_CHUNK: [MilvusHybridDocument(chunk_id="chunk:1", text="chunk")],
            MILVUS_COLLECTION_ENTITY: [MilvusHybridDocument(chunk_id="entity:1", text="entity")],
        },
        vectors_by_role={
            MILVUS_COLLECTION_CHUNK: [[0.1, 0.2, 0.3]],
            MILVUS_COLLECTION_ENTITY: [[0.2, 0.3, 0.4]],
        },
        embedding_model="test-embedding",
    )

    assert count == 2
    assert typed._stores[MILVUS_COLLECTION_CHUNK].replace_calls[0]["documents"][0].chunk_id == "chunk:1"
    assert typed._stores[MILVUS_COLLECTION_ENTITY].replace_calls[0]["documents"][0].chunk_id == "entity:1"


def test_typed_store_deletes_adapter_target_scope_from_all_role_collections() -> None:
    class FakeStore:
        def __init__(self, collection_name: str):
            self.collection_name = collection_name
            self.delete_scope_calls = []

        def delete_scope(self, **kwargs):
            self.delete_scope_calls.append(kwargs)

    registry = MilvusCollectionRegistry(
        chunk="kg_evidence_chunks",
        entity="kg_entity_cards",
        relation="kg_relation_cards",
        community="kg_community_reports",
    )
    typed = MilvusTypedHybridStore(registry=registry, dim=3)
    typed._stores = {
        MILVUS_COLLECTION_CHUNK: FakeStore("kg_evidence_chunks"),
        MILVUS_COLLECTION_ENTITY: FakeStore("kg_entity_cards"),
        MILVUS_COLLECTION_RELATION: FakeStore("kg_relation_cards"),
        "community": FakeStore("kg_community_reports"),
    }

    typed.delete_scope(adapter_name="financial", target="prod")

    for store in typed._stores.values():
        assert store.delete_scope_calls == [{"adapter_name": "financial", "target": "prod"}]
