---
name: financial-graph-research
description: Research financial news, market movements, corroboration, conflicts, causal chains, event progress, and cross-market relationships, then continuously track selected stocks, funds, ETFs, or indices when requested. Use when a financial question requires current public information, cross-document synthesis, relationship verification, graph evidence, or persistent instrument monitoring.
---

# Financial Graph Research

Use Smart Fund's relation graph as the primary financial research system and its
external research tools as a freshness and coverage supplement. Semantic
similarity finds possible graph entry points; it does not prove that two events
are related.

## Capability Routing

Apply these rules before any tool call:

1. Identify the capabilities required by the request. Do not reduce every task
   to either graph retrieval or web search.
2. For financial market, stock, fund, industry, policy, macro, or company
   questions, start with the relation graph. It is the primary knowledge and
   relationship system for this project.
3. If a financial request also says `搜索`, `联网`, `查一下`, `最新`,
   `当前`, `今天`, or `最近`, supplement the graph with
   `external_web_search` and opened public sources. Do not omit either layer.
4. For current public information, news, products, entertainment, sports, or
   general web research, use `external_web_search` and `external_web_read`.
5. For a public code repository, use `external_repo_search`,
   `external_repo_structure`, and `external_repo_read`. Do not substitute a
   generic web search when repository tools directly fit the request.
6. For local workspace code, use Codex's native filesystem and code tools. Do
   not send local source code through the external repository provider.
7. For images or visual documents, use a dedicated vision capability when one
   is available. Do not pretend that web search or the financial graph performs
   image understanding.
8. Multi-part requests may combine graph, web, repository, vision, and native
   code tools. Financial priority means the graph is the primary financial
   knowledge source, not that other capabilities are disabled.
9. A broad but executable search request does not require clarification. Run a
   broad search, summarize the useful results, then ask what the user wants to
   narrow down.
10. Continuous tracking is a deliberate state change. Add an instrument only
    when the user requests ongoing monitoring, it is a current holding, or the
    research produces a specific hypothesis that requires later market-data
    validation. Never add every search hit automatically.

## External Research Workflow

1. Search with `external_web_search`.
2. Select authoritative and directly relevant results. For a substantive
   answer, open at least one primary or market-data source and normally two
   independent sources with `external_web_read`. Search snippets are never
   sufficient evidence for market numbers or investment conclusions.
3. Large pages return a preview and `content_handle`. Read only relevant ranges
   with `external_content_read`.
4. Use `external_repo_search`, `external_repo_structure`, and
   `external_repo_read` only for public repository research.
5. Stop when enough opened sources support the answer.

## Current Financial Hybrid Workflow

For any current/latest/today financial question:

1. Run one focused `kg_relation_graph_search` for the market event, sector,
   instrument, or likely drivers. Open up to three relevant Cards.
2. Run the external workflow to supplement current prices, index moves,
   announcements, and developments newer than the graph.
3. Expand or open Edges only when they materially explain a driver,
   transmission path, confirmation, contradiction, or event progression.
4. Synthesize two explicitly labelled layers:
   - `图谱认知主线`: opened Cards and Edges;
   - `最新市场补充`: opened external sources.
5. If the graph has no useful current context, say so. Do not manufacture a
   connection merely to satisfy the hybrid workflow.

## Graph Retrieval Workflow

Use this workflow only after the routing rules select graph research.

1. Call `kg_relation_graph_search` with a concise description of the financial
   fact, event, market move, or transmission mechanism being investigated.
   Start with one query. Issue another query only when the first result does not
   contain both sides of the question.
2. Open at most four of the strongest candidate Cards with `kg_card_open` before
   treating their summaries or source text as facts.
3. Put the relevant Cards from both sides of the question into one
   `kg_card_expand` call. For a normal question, make exactly one expansion with
   one hop, at most 8 nodes and 8 edges. Do not expand each seed separately.
4. Open only the Edges that materially support the answer, normally no more than
   three. Do not infer a relationship merely because two Cards were returned
   together.
5. Use `kg_community_open` and `kg_community_expand` only when the question asks
   for a broader event cluster, related communities, or the surrounding market
   context. Return to Cards and Edges for evidence.
6. Do not make another expansion or use a second hop unless the user explicitly
   asks for deep research,
   a complete transmission chain, or broader exploration.
7. Stop as soon as the opened Cards and Edges answer the question. Tool output is
   evidence to select and synthesize, not a corpus that must all be repeated.

## Continuous Tracking Workflow

Use this workflow after research identifies a concrete instrument that should be
observed beyond the current answer.

1. Call `market_watchlist_list` first when you need to know whether the
   instrument is already tracked or whether its data is stale.
2. Call `market_watchlist_add` with the concrete code, explicit type, useful
   name, and a short reason tied to the user's objective or research hypothesis.
   New and reactivated instruments trigger immediate collection.
3. Use `market_instrument_open` for the latest compact snapshot. A newly added
   instrument may still be collecting; inspect its tracking status instead of
   inventing missing data.
4. Use `market_instrument_history` only for the dimension and date range needed
   to evaluate a trend or event window.
5. Call `market_watchlist_update` with `enabled=false` when the user no longer
   wants tracking or the original hypothesis is no longer relevant. Disabling
   preserves historical data.
6. Re-adding or enabling the same instrument is idempotent and triggers a new
   first collection only when it was disabled.

Do not treat price co-movement in tracked data as proof of causality. Return to
opened graph Edges or external primary sources for relationship claims.

## External Research Contract

- External tools are provider-neutral. Never depend on a provider's private MCP
  tool name or response schema.
- Search results are discovery leads. Cite or rely on a public source only after
  opening it.
- Keep external evidence separate from graph evidence. An external article does
  not create a verified graph Edge.
- Prefer primary and authoritative sources when multiple results describe the
  same fact.
- If external sources add information that is absent from the graph, state that
  it is external research rather than existing graph knowledge.
- Content handles are temporary. Read the needed range during the current task.
- Never answer a time-sensitive financial search using only graph Cards.
- Never answer a substantive financial market query using only search snippets.

## Evidence Contract

- Treat search results and Community summaries as discovery material, not final
  evidence.
- Ground each factual claim in an opened Card or Edge with readable evidence.
- Ground each relationship claim in an opened, active Edge.
- Distinguish `observed` relations from `inferred` relations in the answer.
- Treat `same_fact` as duplicate corroboration or event heat, not as an
  independent causal signal.
- If the graph has no verified Edge for a proposed relationship, state
  `现有图谱证据不足以确认该关系`. Do not convert similarity into causality.
- Preserve complete Card, Edge, and Community IDs. Never shorten IDs in
  citations.
- When sources conflict, retain the conflict and identify the corresponding
  Cards or Edges instead of forcing a single narrative.

## Answer Shape

Answer in Chinese unless the user requests another language. Prefer:

1. `结论`: the direct answer and its confidence boundary.
2. `证据链`: ordered facts and verified relations showing how the conclusion was
   reached.
3. `不确定性`: missing links, inferred relations, conflicts, or freshness limits.

Use inline graph citations:

- `[Card:kg_cognitive_card:...]`
- `[Edge:kg_card_relation:...]`
- `[Community:kgc:financial:relation:...]`

Do not cite a Community as proof of a card-to-card relationship.

For external material, cite the page title and URL returned by
`external_web_read`. Do not expose `content_handle` as a citation.

## Tool Reference

Read [references/tool-contract.md](references/tool-contract.md) when selecting
tools, interpreting relation kinds, or diagnosing a weak retrieval result.
