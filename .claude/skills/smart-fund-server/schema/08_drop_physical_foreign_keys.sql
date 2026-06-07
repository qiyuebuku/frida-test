-- Remove legacy physical foreign keys.
-- Tables keep logical id references, but cleanup/rebuild flows must not be
-- blocked by database-level FK delete ordering. Some local/test databases may
-- contain old tables owned by another role, so this migration skips those
-- constraints with a NOTICE instead of failing the whole schema refresh.

DO $$
DECLARE
    ddl text;
    ddl_statements text[] := ARRAY[
        'ALTER TABLE IF EXISTS public.kg_edges DROP CONSTRAINT IF EXISTS kg_edges_source_node_id_fkey',
        'ALTER TABLE IF EXISTS public.kg_edges DROP CONSTRAINT IF EXISTS kg_edges_target_node_id_fkey',
        'ALTER TABLE IF EXISTS public.kg_edge_evidence DROP CONSTRAINT IF EXISTS kg_edge_evidence_edge_id_fkey',
        'ALTER TABLE IF EXISTS public.kg_edge_evidence DROP CONSTRAINT IF EXISTS kg_edge_evidence_evidence_id_fkey',
        'ALTER TABLE IF EXISTS public.kg_edge_evidence_chunks DROP CONSTRAINT IF EXISTS kg_edge_evidence_chunks_edge_id_fkey',
        'ALTER TABLE IF EXISTS public.kg_edge_evidence_chunks DROP CONSTRAINT IF EXISTS kg_edge_evidence_chunks_evidence_id_fkey',
        'ALTER TABLE IF EXISTS public.kg_edge_evidence_chunks DROP CONSTRAINT IF EXISTS kg_edge_evidence_chunks_chunk_id_fkey',
        'ALTER TABLE IF EXISTS public.kg_evidence_chunks DROP CONSTRAINT IF EXISTS kg_evidence_chunks_evidence_id_fkey',
        'ALTER TABLE IF EXISTS public.ft_reviews DROP CONSTRAINT IF EXISTS ft_reviews_decision_id_fkey'
    ];
BEGIN
    FOREACH ddl IN ARRAY ddl_statements LOOP
        BEGIN
            EXECUTE ddl;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE NOTICE 'skip dropping physical foreign key because current role is not table owner: %', ddl;
        END;
    END LOOP;
END $$;
