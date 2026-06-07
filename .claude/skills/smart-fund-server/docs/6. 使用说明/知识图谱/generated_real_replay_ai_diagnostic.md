# KG Real Replay AI Diagnostic

- status: failed
- started_at: 2026-05-17T16:40:22+0800
- finished_at: 2026-05-17T16:46:17+0800
- script: docs/6. 使用说明/知识图谱/3_kg_real_replay_quality_baseline.py
- full_log: /home/yuyang/frida-test/.claude/skills/smart-fund-server/docs/6. 使用说明/知识图谱/generated_real_replay_full.log
- llm_full_trace: /home/yuyang/frida-test/.claude/skills/smart-fund-server/docs/6. 使用说明/知识图谱/generated_real_replay_llm_full_trace.log
- retrieval_llm_trace: /home/yuyang/frida-test/.claude/skills/smart-fund-server/docs/6. 使用说明/知识图谱/generated_retrieval_llm_trace.log
- case_file: /home/yuyang/frida-test/.claude/skills/smart-fund-server/docs/6. 使用说明/知识图谱/generated_real_replay_bad_cases.json

## Config

```json
{
  "target": "prod",
  "adapter": "financial",
  "write_only": false,
  "reset_before_replay": true,
  "projection_news_limit": 100,
  "projection_order_by_created_at": true,
  "dynamic_case_limit": 12,
  "include_seed_baseline": false,
  "fail_on_compile_failure": false,
  "concurrency": 2,
  "trace_retrieval_llm": true,
  "profile_retrieval": true
}
```

## Step Timeline

```json
[
  {
    "name": "configure logging",
    "status": "done",
    "duration_s": 0.0
  },
  {
    "name": "print run mode",
    "status": "done",
    "duration_s": 0.0
  },
  {
    "name": "configure retrieval profile",
    "status": "done",
    "duration_s": 0.0
  },
  {
    "name": "configure retrieval LLM trace",
    "status": "done",
    "duration_s": 0.0
  },
  {
    "name": "print LLM routing config",
    "status": "done",
    "duration_s": 0.001
  },
  {
    "name": "check Milvus",
    "status": "done",
    "duration_s": 1.135
  },
  {
    "name": "check KG service health",
    "status": "done",
    "duration_s": 0.072
  },
  {
    "name": "Step 0 reset generated KG data before write",
    "status": "done",
    "duration_s": 0.639
  },
  {
    "name": "Step 0.5 ensure system baseline normalization rules",
    "status": "done",
    "duration_s": 0.04
  },
  {
    "name": "Step 1 compile real ft_news projection records",
    "status": "done",
    "duration_s": 4.608
  },
  {
    "name": "write bad case file",
    "status": "done",
    "duration_s": 0.0
  },
  {
    "name": "Step 1.6 inspect retrieval documents",
    "status": "done",
    "duration_s": 0.336
  },
  {
    "name": "Step 1.7 retrieval document quality report",
    "status": "done",
    "duration_s": 0.341
  },
  {
    "name": "Step 1.8 check database persistence",
    "status": "done",
    "duration_s": 0.026
  }
]
```

## Data Summary

```json
{
  "real_news_records": 100,
  "case_count": 12,
  "database_counts": [
    {
      "table_name": "kg_nodes",
      "count": 546
    },
    {
      "table_name": "kg_edges",
      "count": 651
    },
    {
      "table_name": "kg_evidence",
      "count": 100
    },
    {
      "table_name": "kg_wiki_pages",
      "count": 555
    },
    {
      "table_name": "kg_evidence_chunks",
      "count": 100
    },
    {
      "table_name": "kg_retrieval_documents",
      "count": 1852
    },
    {
      "table_name": "kg_retrieval_trace_snapshots",
      "count": 0
    }
  ],
  "compile_results": [
    {
      "label": "ft_news",
      "summary": {
        "adapter_name": "financial",
        "run_id": "kg_run:financial:1997c385-6127-4be4-a8ed-66b13359e3f5",
        "nodes": 546,
        "edges": 651,
        "evidence": 100,
        "failed_records": 20,
        "ids": {
          "node_ids": {
            "count": 546,
            "sample": [
              "kg:financial:event:5b8cdfdfc972155e",
              "kg:financial:concept:5223b7f613456aa9",
              "kg:financial:concept:9ebc089f8aeb7a3d",
              "kg:financial:industry:0382d4d5a5ccc248",
              "kg:financial:industry:aa47b41443f6da9f"
            ]
          },
          "edge_ids": {
            "count": 651,
            "sample": [
              "kg_edge:financial:mentions:2c98471255cf44a7",
              "kg_edge:financial:mentions:c69760a5256e6090",
              "kg_edge:financial:mentions:5cfed905f6baaffa",
              "kg_edge:financial:mentions:4d5214974ba227c1",
              "kg_edge:financial:mentions:c0c763c69a0d3741"
            ]
          },
          "evidence_ids": {
            "count": 100,
            "sample": [
              "kg_ev:financial:news_articles:ft_news:83904:a8934858d26f15da",
              "kg_ev:financial:news_articles:ft_news:78253:9826dbf7affb1a80",
              "kg_ev:financial:news_articles:ft_news:78252:9fb443f5b56557e6",
              "kg_ev:financial:news_articles:ft_news:77551:1fb63ab345ed9195",
              "kg_ev:financial:news_articles:ft_news:77550:8a878b6c47ecf6c8"
            ]
          }
        },
        "index_refresh": {
          "mode": "incremental",
          "graph_adjacency": 453,
          "evidence_chunks": 100,
          "wiki_pages": 555,
          "hybrid_chunks": 1852,
          "retrieval_documents": 1852,
          "retrieval_document_version_id": "kg_rt_doc_version:6567be49-1b2a-4198-b5eb-a0dc46f68369",
          "stale_evidence_cleanup": {
            "evidence": 0,
            "edges": 0,
            "evidence_ids": [],
            "edge_ids": []
          },
          "stale_hybrid_vectors_deleted": 0,
          "node_ids": {
            "count": 546,
            "sample": [
              "kg:financial:event:5b8cdfdfc972155e",
              "kg:financial:concept:5223b7f613456aa9",
              "kg:financial:concept:9ebc089f8aeb7a3d",
              "kg:financial:industry:0382d4d5a5ccc248",
              "kg:financial:industry:aa47b41443f6da9f"
            ]
          },
          "edge_ids": {
            "count": 651,
            "sample": [
              "kg_edge:financial:mentions:2c98471255cf44a7",
              "kg_edge:financial:mentions:c69760a5256e6090",
              "kg_edge:financial:mentions:5cfed905f6baaffa",
              "kg_edge:financial:mentions:4d5214974ba227c1",
              "kg_edge:financial:mentions:c0c763c69a0d3741"
            ]
          },
          "evidence_ids": {
            "count": 100,
            "sample": [
              "kg_ev:financial:news_articles:ft_news:83904:a8934858d26f15da",
              "kg_ev:financial:news_articles:ft_news:78253:9826dbf7affb1a80",
              "kg_ev:financial:news_articles:ft_news:78252:9fb443f5b56557e6",
              "kg_ev:financial:news_articles:ft_news:77551:1fb63ab345ed9195",
              "kg_ev:financial:news_articles:ft_news:77550:8a878b6c47ecf6c8"
            ]
          }
        },
        "failure_reason_counts": {
          "edge endpoint cannot be resolved": 20
        },
        "failure_endpoints": {
          "count": 25,
          "items": [
            {
              "ref": "concept:business:服务贸易标准化工作行动计划(2026—2030年)",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 5,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "concept:business:《\"骑心协力共护食安\"网络餐饮食品安全共治备忘录》",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 4,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "institution:商务部",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:institution:570dc80b37eacfd9",
              "node_type": "institution",
              "resolved": true,
              "failure_count": 3,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "institution:市场监管总局",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:institution:bb647e09cbe5f996",
              "node_type": "institution",
              "resolved": true,
              "failure_count": 3,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "concept:business:《服务贸易标准化工作行动计划(2026—2030年)》",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 2,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "concept:business:牧野铣床",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 2,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "event:ft_news:76833:candidate_event:0",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:event:24cef1c4d0ae468d",
              "node_type": "event",
              "resolved": true,
              "failure_count": 2,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "affects"
              ]
            },
            {
              "ref": "institution:日本政府",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:institution:445247f475c02284",
              "node_type": "institution",
              "resolved": true,
              "failure_count": 2,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "concept:business:MBKPartners",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "concept:business:三星电子",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "concept:business:中芯国际",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "affects"
              ]
            },
            {
              "ref": "concept:business:农业",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "concept:business:华绿生物",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "affects"
              ]
            },
            {
              "ref": "concept:business:华虹半导体",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "affects"
              ]
            },
            {
              "ref": "concept:business:恒勃股份",
              "side": [
                "target"
              ],
              "node_id": null,
              "node_type": "concept",
              "resolved": false,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "affects"
              ]
            },
            {
              "ref": "event:ft_news:75456",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:event:c2ac9e4d99db0a12",
              "node_type": "event",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "event:ft_news:76842:candidate_event:0",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:event:9d35fb1c146c5fab",
              "node_type": "event",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "industry:default:食用菌",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:industry:fbf2e09dbdd0d031",
              "node_type": "industry",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "institution:京东外卖",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:institution:e7c2e1a8412d261c",
              "node_type": "institution",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "institution:海南省市场监管局",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:institution:75cefb2a68504c60",
              "node_type": "institution",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "institution:淘宝闪购",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:institution:9d0a142172bd0768",
              "node_type": "institution",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "institution:美团",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:institution:66a2fc081d10ab2d",
              "node_type": "institution",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "policy:ft_news:74432:candidate_event:0",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:policy:2503fcb6432ea649",
              "node_type": "policy",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "affects"
              ]
            },
            {
              "ref": "policy:ft_news:75049:candidate_event:0",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:policy:564f71708fcd9c5f",
              "node_type": "policy",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "related_to"
              ]
            },
            {
              "ref": "policy:ft_news:75457:candidate_event:0",
              "side": [
                "source"
              ],
              "node_id": "kg:financial:policy:86ee3ab004e4cb2a",
              "node_type": "policy",
              "resolved": true,
              "failure_count": 1,
              "reasons": [
                "edge endpoint cannot be resolved"
              ],
              "source_ids": [
                "affects"
              ]
            }
          ]
        },
        "failure_sample": [
          {
            "source_type": "event",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "event:ft_news:76842:candidate_event:0",
            "target_ref": "concept:business:三星电子",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:event:9d35fb1c146c5fab",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:76842"
            ]
          },
          {
            "source_type": "event",
            "source_id": "affects",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "event:ft_news:76833:candidate_event:0",
            "target_ref": "concept:business:华虹半导体",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:event:24cef1c4d0ae468d",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:76833"
            ]
          },
          {
            "source_type": "event",
            "source_id": "affects",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "event:ft_news:76833:candidate_event:0",
            "target_ref": "concept:business:中芯国际",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:event:24cef1c4d0ae468d",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:76833"
            ]
          },
          {
            "source_type": "institution",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "institution:海南省市场监管局",
            "target_ref": "concept:business:《\"骑心协力共护食安\"网络餐饮食品安全共治备忘录》",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:institution:75cefb2a68504c60",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:75815"
            ]
          },
          {
            "source_type": "institution",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "institution:淘宝闪购",
            "target_ref": "concept:business:《\"骑心协力共护食安\"网络餐饮食品安全共治备忘录》",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:institution:9d0a142172bd0768",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:75815"
            ]
          },
          {
            "source_type": "institution",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "institution:美团",
            "target_ref": "concept:business:《\"骑心协力共护食安\"网络餐饮食品安全共治备忘录》",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:institution:66a2fc081d10ab2d",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:75815"
            ]
          },
          {
            "source_type": "institution",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "institution:京东外卖",
            "target_ref": "concept:business:《\"骑心协力共护食安\"网络餐饮食品安全共治备忘录》",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:institution:e7c2e1a8412d261c",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:75815"
            ]
          },
          {
            "source_type": "policy",
            "source_id": "affects",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "policy:ft_news:75457:candidate_event:0",
            "target_ref": "concept:business:恒勃股份",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:policy:86ee3ab004e4cb2a",
            "target_node_id": null,
            "evidence_refs": [
              "policy_news:ft_news:75457"
            ]
          },
          {
            "source_type": "institution",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "institution:日本政府",
            "target_ref": "concept:business:牧野铣床",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:institution:445247f475c02284",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:75456"
            ]
          },
          {
            "source_type": "institution",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "institution:日本政府",
            "target_ref": "concept:business:MBKPartners",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:institution:445247f475c02284",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:75456"
            ]
          },
          {
            "source_type": "event",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "event:ft_news:75456",
            "target_ref": "concept:business:牧野铣床",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:event:c2ac9e4d99db0a12",
            "target_node_id": null,
            "evidence_refs": [
              "news_articles:ft_news:75456"
            ]
          },
          {
            "source_type": "institution",
            "source_id": "related_to",
            "reason": "edge endpoint cannot be resolved",
            "source_ref": "institution:商务部",
            "target_ref": "concept:business:服务贸易标准化工作行动计划(2026—2030年)",
            "target_type": "concept",
            "source_resolved": true,
            "target_resolved": false,
            "source_node_id": "kg:financial:institution:570dc80b37eacfd9",
            "target_node_id": null,
            "evidence_refs": [
              "policy_news:ft_news:75049"
            ]
          }
        ],
        "warnings_count": 0,
        "dry_run": false
      }
    }
  ],
  "retrieval_documents": {
    "total": 1852,
    "by_fact_type": {
      "edge": 651,
      "evidence": 100,
      "node": 546,
      "wiki": 555
    },
    "by_answer_type": {
      "support": 1305,
      "answer": 482,
      "unknown": 64,
      "background": 1
    },
    "latest_versions": [
      {
        "version_id": "kg_rt_doc_version:6567be49-1b2a-4198-b5eb-a0dc46f68369",
        "generation_version": "retrieval_doc_v2",
        "field_coverage": {
          "filled_counts": {
            "aliases": 372,
            "edge_refs": 1163,
            "node_refs": 1752,
            "time_tags": 502,
            "event_type": 646,
            "key_phrases": 1852,
            "search_text": 1852,
            "asset_classes": 445,
            "evidence_refs": 1766,
            "evidence_summary": 1839,
            "impact_direction": 543,
            "relation_intents": 963,
            "source_type_tags": 655,
            "readable_relations": 1154,
            "answer_candidate_type": 1788
          },
          "filled_ratios": {
            "aliases": 0.2009,
            "edge_refs": 0.628,
            "node_refs": 0.946,
            "time_tags": 0.2711,
            "event_type": 0.3488,
            "key_phrases": 1.0,
            "search_text": 1.0,
            "asset_classes": 0.2403,
            "evidence_refs": 0.9536,
            "evidence_summary": 0.993,
            "impact_direction": 0.2932,
            "relation_intents": 0.52,
            "source_type_tags": 0.3537,
            "readable_relations": 0.6231,
            "answer_candidate_type": 0.9654
          },
          "total_documents": 1852
        },
        "changed_fact_set": {
          "edge_ids": 651,
          "node_ids": 546,
          "document_ids": 1852,
          "evidence_ids": 100,
          "wiki_page_ids": 555
        }
      }
    ]
  },
  "retrieval_document_quality": {
    "total": 1852,
    "by_fact_type": {
      "edge": 651,
      "evidence": 100,
      "node": 546,
      "wiki": 555
    },
    "by_answer_type": {
      "support": 1305,
      "answer": 482,
      "unknown": 64,
      "background": 1
    },
    "version_counts": {
      "retrieval_doc_v2": 1852
    },
    "expected_generation_version": "retrieval_doc_v2",
    "expected_generation_version_count": 1852,
    "expected_generation_version_ratio": 1.0,
    "field_counts": {
      "search_text": 1852,
      "key_phrases": 1852,
      "aliases": 372,
      "readable_relations": 1154,
      "evidence_summary": 1839,
      "answer_candidate_type": 1788,
      "relation_intents": 963,
      "source_type_tags": 655,
      "time_tags": 502
    },
    "field_ratios": {
      "search_text": 1.0,
      "key_phrases": 1.0,
      "aliases": 0.2009,
      "readable_relations": 0.6231,
      "evidence_summary": 0.993,
      "answer_candidate_type": 0.9654,
      "relation_intents": 0.52,
      "source_type_tags": 0.3537,
      "time_tags": 0.2711
    },
    "empty_summary_by_fact_type": {
      "node": 13
    },
    "json_noise_count": 0,
    "json_noise_samples": [],
    "warnings": [
      "node_evidence_summary_empty=13"
    ]
  }
}
```

## Replay Summary

```json
{
  "summary": {},
  "failed_cases": []
}
```

## Quality Eval

```json
{}
```

## Error

```json
{
  "type": "KeyboardInterrupt",
  "message": "",
  "traceback": [
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpx/_client.py\", line 176, in __aiter__\n    async for chunk in self._stream:\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpx/_transports/default.py\", line 271, in __aiter__\n    async for part in self._httpcore_stream:\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpcore/_async/connection_pool.py\", line 407, in __aiter__\n    raise exc from None\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpcore/_async/connection_pool.py\", line 403, in __aiter__\n    async for part in self._stream:\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpcore/_async/http11.py\", line 342, in __aiter__\n    raise exc\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpcore/_async/http11.py\", line 334, in __aiter__\n    async for chunk in self._connection._receive_response_body(**kwargs):\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpcore/_async/http11.py\", line 203, in _receive_response_body\n    event = await self._receive_event(timeout=timeout)\n            ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpcore/_async/http11.py\", line 217, in _receive_event\n    data = await self._network_stream.read(\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/httpcore/_backends/anyio.py\", line 35, in read\n    return await self._stream.receive(max_bytes=max_bytes)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/anyio/streams/tls.py\", line 244, in receive\n    data = await self._call_sslobject_method(self._ssl_object.read, max_bytes)\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/anyio/streams/tls.py\", line 187, in _call_sslobject_method\n    data = await self.transport_stream.receive()\n           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n",
    "  File \"/home/yuyang/.local/lib/python3.12/site-packages/anyio/_backends/_asyncio.py\", line 1269, in receive\n    await self._protocol.read_event.wait()\n",
    "  File \"/home/yuyang/anaconda3/envs/frida-test/lib/python3.12/asyncio/locks.py\", line 212, in wait\n    await fut\n",
    "asyncio.exceptions.CancelledError\n",
    "\nDuring handling of the above exception, another exception occurred:\n\n",
    "Traceback (most recent call last):\n",
    "  File \"/home/yuyang/frida-test/.claude/skills/smart-fund-server/docs/6. 使用说明/知识图谱/3_kg_real_replay_quality_baseline.py\", line 2000, in <module>\n    asyncio.run(main())\n",
    "  File \"/home/yuyang/anaconda3/envs/frida-test/lib/python3.12/asyncio/runners.py\", line 195, in run\n    return runner.run(main)\n           ^^^^^^^^^^^^^^^^\n",
    "  File \"/home/yuyang/anaconda3/envs/frida-test/lib/python3.12/asyncio/runners.py\", line 123, in run\n    raise KeyboardInterrupt()\n",
    "KeyboardInterrupt\n"
  ]
}
```

## Debug Pointers

- 优先看 full_log 中 `[llm_call] START/DONE/FAILED` 判断是否卡在真实 LLM 请求。
- 若 replay failed，优先看 `generated_retrieval_llm_trace.log` 的 `agentic_case_summary`、`ranker_preselect`、`candidate_judge`。
- 若写入慢，优先看 full_log 中 `financial_news_extraction` 的 source_id 和耗时。
