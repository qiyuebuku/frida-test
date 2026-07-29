# Smart Fund Graph Tool Contract

## Tool Selection

| Need | Tool | Result role |
| --- | --- | --- |
| Find graph entry points from natural language | `kg_relation_graph_search` | Candidate discovery |
| Read atomic facts and source evidence | `kg_card_open` | Fact evidence |
| Traverse relations from known facts | `kg_card_expand` | Relation discovery |
| Verify relation type, direction, and evidence | `kg_edge_open` | Relation evidence |
| Inspect an event cluster | `kg_community_open` | Cluster navigation |
| Traverse related event clusters | `kg_community_expand` | Broad context navigation |
| Add or reactivate continuous tracking | `market_watchlist_add` | Stateful tracking control |
| Inspect tracking state and freshness | `market_watchlist_list` | Tracking diagnostics |
| Change interval or stop/restart tracking | `market_watchlist_update` | Stateful tracking control |
| Read latest tracked market data | `market_instrument_open` | Latest market snapshot |
| Read one tracked historical dimension | `market_instrument_history` | Bounded time series |
| Discover current public sources | `external_web_search` | External lead discovery |
| Open one public page | `external_web_read` | External source evidence |
| Search a public repository | `external_repo_search` | Repository discovery |
| Inspect a repository directory | `external_repo_structure` | Repository navigation |
| Read a repository file | `external_repo_read` | Repository source evidence |
| Continue reading large external content | `external_content_read` | Bounded content paging |

Search and expand responses may contain convenient summaries. These summaries
help decide what to open next; they are not substitutes for opening the relevant
Card or Edge.

### Routing precedence

1. Financial knowledge or relationship question -> `kg_*` tools are primary.
2. Current/latest financial question -> graph plus external opened sources.
3. General current public information -> web search and reader.
4. Public repository question -> repository search, structure, and file tools.
5. Local code question -> Codex native workspace tools.
6. Image understanding -> a dedicated vision tool when available.
7. Multi-capability request -> compose the relevant routes.
8. Persistent monitoring request -> research first, then use `market_*` tools
   only for concrete instruments.

`kg_relation_graph_search` is not a fallback implementation of web search.
Conversely, web search snippets are not a replacement for verified graph
relationships.

## Relation Semantics

- `same_fact`: two Cards report the same atomic fact. Use as corroboration,
  source diversity, or heat. Do not count both as independent causal events.
- `same_event`: distinct facts belong to the same real-world event.
- `confirmation`: one Card independently supports another Card's claim.
- `contradiction`: Cards provide incompatible claims or observations.
- `temporal_progression`: the target is a later state or development of the
  source event.
- `causal_influence`: the graph records a supported influence or transmission
  direction. Check whether it is `observed` or `inferred`.
- `market_co_movement`: markets moved together, but co-movement alone does not
  establish causality.

Other relation kinds must be interpreted from the opened Edge rather than from
their names alone.

## Retrieval Discipline

1. Begin with a narrow query describing the event or mechanism, not a broad
   industry label.
2. Open no more than four of the strongest Cards first.
3. Expand the relevant Cards from both sides together in one call, using one hop
   and a normal budget of 8 Cards and 8 Edges.
4. Open only the Edges that materially support the answer, normally no more than
   three in one call.
5. Use a second search with a changed angle when no useful seed is found:
   actor, instrument, policy action, upstream driver, downstream consequence, or
   contradiction.
6. Do not make a second expansion or use a second hop for an ordinary question.
7. Stop when additional graph expansion only returns duplicate facts or generic
   topical neighbors.

An empty result means the current graph does not contain a matching verified
path. It does not prove that the relationship is false.

## External Tool Semantics

- Provider selection is owned by Smart Fund Server. Agent prompts and Skills use
  only the stable `external_*` tools.
- `external_web_search` snippets are not complete source evidence.
- `external_web_read` and repository tools return a bounded preview plus a
  temporary `content_handle`.
- `external_content_read` reads a character range from that handle. Keep each
  page bounded and stop when the relevant section has been obtained.
- External evidence can corroborate or challenge a graph conclusion, but it is
  not a verified Card or Edge until the ingestion pipeline processes it.
- For current financial questions, label the graph knowledge mainline and the
  external freshness supplement separately.

## Market Tracking Semantics

- `market_watchlist_add` and `market_watchlist_update` change persistent server
  state. Use them only when continuous tracking is intended.
- Every Agent-created item needs a specific reason.
- Newly created and reactivated items are collected immediately; an unchanged
  repeated add does not create duplicate state or another initial task.
- `market_watchlist_list` reports the per-instrument checkpoint,
  `last_success_at`, and `last_error`.
- `market_instrument_open` returns latest rows by data type plus tracking
  freshness. It is not a live exchange quote guarantee.
- `market_instrument_history` reads one data type at a time to keep context
  bounded.
- Disable tracking rather than deleting history when an observation ends.
