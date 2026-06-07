-- Drop obsolete Graph Index version archive tables.
-- Current Graph Index state is stored in kg_graph_communities, kg_graph_findings and kg_graph_deltas.
-- Historical archive tables are intentionally removed to avoid maintaining duplicate derived state.

DROP TABLE IF EXISTS public.kg_graph_delta_versions;
DROP TABLE IF EXISTS public.kg_graph_finding_versions;
DROP TABLE IF EXISTS public.kg_graph_community_versions;
