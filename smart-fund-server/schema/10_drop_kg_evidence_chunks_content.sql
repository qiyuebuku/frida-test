-- Remove duplicated readable chunk text and redundant JSON metadata from PG chunk manifests.
-- Milvus stores readable chunk/card text; kg_evidence stores source metadata.
-- kg_evidence_chunks keeps only chunk manifest columns and graph refs point to chunk_id.

UPDATE public.kg_evidence
SET payload = payload
    - 'text'
    - 'content'
    - 'raw_text'
    - 'full_text'
    - 'document_text'
    - 'body'
    - 'html'
WHERE payload ?| ARRAY['text', 'content', 'raw_text', 'full_text', 'document_text', 'body', 'html'];

ALTER TABLE IF EXISTS public.kg_evidence_chunks
    DROP COLUMN IF EXISTS content;

ALTER TABLE IF EXISTS public.kg_evidence_chunks
    DROP COLUMN IF EXISTS payload;
