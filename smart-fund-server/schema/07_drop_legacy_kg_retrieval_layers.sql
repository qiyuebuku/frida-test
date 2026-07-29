-- Remove legacy KG retrieval materialization tables.
-- The active retrieval architecture uses canonical KG facts plus evidence chunks
-- and semantic vector documents.

DROP TABLE IF EXISTS public.kg_retrieval_document_versions;
DROP TABLE IF EXISTS public.kg_retrieval_documents;
DROP TABLE IF EXISTS public.kg_wiki_pages;
