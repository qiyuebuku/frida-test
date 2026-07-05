-- Remove legacy node/edge fact graph tables.
-- Current KG architecture keeps source evidence and chunk manifests in PG,
-- stores readable/searchable text in Milvus, and builds high-level indexes from
-- Cognitive Cards and Community Assignments.

DROP TABLE IF EXISTS public.kg_edge_evidence_chunks;
DROP TABLE IF EXISTS public.kg_edge_evidence;
DROP TABLE IF EXISTS public.kg_graph_adjacency;
DROP TABLE IF EXISTS public.kg_edges;
DROP TABLE IF EXISTS public.kg_nodes;

-- Legacy PG retrieval/wiki materialization tables are also not part of the
-- active retrieval path. schema/07 already drops them; keep these statements
-- here so a single cleanup migration documents the full removal boundary.
DROP TABLE IF EXISTS public.kg_retrieval_document_versions;
DROP TABLE IF EXISTS public.kg_retrieval_documents;
DROP TABLE IF EXISTS public.kg_wiki_pages;
