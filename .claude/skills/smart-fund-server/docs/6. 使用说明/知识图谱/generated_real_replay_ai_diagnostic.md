# KG Real Replay AI Diagnostic

- status: success
- started_at: 2026-05-17T15:45:19+0800
- finished_at: 2026-05-17T15:46:15+0800
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
    "duration_s": 0.001
  },
  {
    "name": "print LLM routing config",
    "status": "done",
    "duration_s": 0.001
  },
  {
    "name": "check Milvus",
    "status": "done",
    "duration_s": 1.067
  },
  {
    "name": "check KG service health",
    "status": "done",
    "duration_s": 0.07
  },
  {
    "name": "Step 0 reset generated KG data before write",
    "status": "done",
    "duration_s": 0.656
  },
  {
    "name": "Step 0.5 ensure system baseline normalization rules",
    "status": "done",
    "duration_s": 0.034
  },
  {
    "name": "Step 1 compile real ft_news projection records",
    "status": "done",
    "duration_s": 4.497
  },
  {
    "name": "write bad case file",
    "status": "done",
    "duration_s": 0.001
  },
  {
    "name": "Step 1.6 inspect retrieval documents",
    "status": "done",
    "duration_s": 0.317
  },
  {
    "name": "Step 1.7 retrieval document quality report",
    "status": "done",
    "duration_s": 0.304
  },
  {
    "name": "Step 1.8 check database persistence",
    "status": "done",
    "duration_s": 0.022
  },
  {
    "name": "Step 4 replay auto-routed quality baseline",
    "status": "done",
    "duration_s": 48.612
  },
  {
    "name": "Step 4.5 persist retrieval quality evaluation",
    "status": "done",
    "duration_s": 0.159
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
        "run_id": "kg_run:financial:7974e4f4-b860-431a-84bd-d0172edac4e7",
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
          "retrieval_document_version_id": "kg_rt_doc_version:9c1a5907-ed63-43e8-aaaf-910e575046a9",
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
        "version_id": "kg_rt_doc_version:9c1a5907-ed63-43e8-aaaf-910e575046a9",
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
  "summary": {
    "total": 12,
    "passed": 12,
    "failed": 0,
    "metrics": {
      "pass_rate": 1.0,
      "channel_coverage": {
        "search": 12,
        "open": 12
      },
      "route_coverage": {
        "agentic_arag": 12
      },
      "upgraded": 0,
      "avg_hits": 9.666666666666666,
      "avg_evidence_refs": 3.4166666666666665,
      "avg_matched_nodes": 87.91666666666667,
      "avg_matched_edges": 28.5,
      "avg_forbidden_hits": 0.0,
      "avg_context_precision": 0.5201831927714006
    }
  },
  "failed_cases": []
}
```

## Quality Eval

```json
{
  "run_id": "kg_rt_eval:f2b89407-d777-4dd8-92ec-81ca65eac463",
  "strategy": "real_replay_quality_baseline:v1",
  "labels": 12,
  "snapshots": 12,
  "metrics": 36,
  "upserted": 36,
  "aggregate": {
    "case_count": 12,
    "metric_count": 36,
    "failure_count": 36,
    "avg_preselect_recall_at_k": 0.3117063492063492,
    "avg_preselect_precision_at_k": 0.21582383665716998,
    "avg_wasted_slots_at_k": 7.194444444444445,
    "avg_expected_candidates": 4.5,
    "avg_selected_candidates": 8.5,
    "avg_hit_candidates": 1.3055555555555556,
    "avg_missed_candidates": 3.1944444444444446,
    "avg_snapshot_found": 1.0,
    "avg_k": 11.666666666666666,
    "labels": 12,
    "snapshots_available": 12,
    "metrics_upserted": 36
  },
  "metric_sample": [
    {
      "metric_id": "kg_rt_metric:3b127999-4de5-4fe2-bfd2-972522bfc20b",
      "run_id": "kg_rt_eval:f2b89407-d777-4dd8-92ec-81ca65eac463",
      "case_id": "real_ft_news_ft_news_83904@8",
      "query": "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
      "metrics": {
        "preselect_recall_at_k": 0.3333333333333333,
        "preselect_precision_at_k": 0.16666666666666666,
        "wasted_slots_at_k": 5,
        "expected_candidates": 3,
        "selected_candidates": 6,
        "hit_candidates": 1,
        "missed_candidates": 2,
        "snapshot_found": 1,
        "k": 8
      },
      "failure_stage": "preselect",
      "failure_details": {
        "missed_candidate_ids": [
          "kg_edge:financial:mentions:2c98471255cf44a7",
          "A股并购重组市场呈现三方面新变化"
        ],
        "wasted_candidate_ids": [
          "kg_ev:financial:news_articles:ft_news:83904:a8934858d26f15da",
          "kg:financial:concept:5223b7f613456aa9",
          "kg:financial:event:e8a50bb3aca0545b",
          "kg:financial:event:a0f8f9de7750b5d3",
          "kg_wiki:financial:relation_page:ed961fe88461d1fd"
        ],
        "base_case_id": "real_ft_news_ft_news_83904",
        "snapshot_id": "kg_rt_snapshot:860b320c-06a9-432f-ba53-fdcab130e812"
      },
      "created_at": null
    },
    {
      "metric_id": "kg_rt_metric:16a327bd-1388-49c1-a9fa-7e11abe92d8e",
      "run_id": "kg_rt_eval:f2b89407-d777-4dd8-92ec-81ca65eac463",
      "case_id": "real_ft_news_ft_news_83904@12",
      "query": "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
      "metrics": {
        "preselect_recall_at_k": 0.3333333333333333,
        "preselect_precision_at_k": 0.16666666666666666,
        "wasted_slots_at_k": 5,
        "expected_candidates": 3,
        "selected_candidates": 6,
        "hit_candidates": 1,
        "missed_candidates": 2,
        "snapshot_found": 1,
        "k": 12
      },
      "failure_stage": "preselect",
      "failure_details": {
        "missed_candidate_ids": [
          "kg_edge:financial:mentions:2c98471255cf44a7",
          "A股并购重组市场呈现三方面新变化"
        ],
        "wasted_candidate_ids": [
          "kg_ev:financial:news_articles:ft_news:83904:a8934858d26f15da",
          "kg:financial:concept:5223b7f613456aa9",
          "kg:financial:event:e8a50bb3aca0545b",
          "kg:financial:event:a0f8f9de7750b5d3",
          "kg_wiki:financial:relation_page:ed961fe88461d1fd"
        ],
        "base_case_id": "real_ft_news_ft_news_83904",
        "snapshot_id": "kg_rt_snapshot:860b320c-06a9-432f-ba53-fdcab130e812"
      },
      "created_at": null
    },
    {
      "metric_id": "kg_rt_metric:4369ada3-2c7a-4d28-ae34-1ff2d6e12182",
      "run_id": "kg_rt_eval:f2b89407-d777-4dd8-92ec-81ca65eac463",
      "case_id": "real_ft_news_ft_news_83904@15",
      "query": "A股并购重组市场呈现三方面新变化 这条新闻涉及哪些主体、行业或资产影响",
      "metrics": {
        "preselect_recall_at_k": 0.3333333333333333,
        "preselect_precision_at_k": 0.16666666666666666,
        "wasted_slots_at_k": 5,
        "expected_candidates": 3,
        "selected_candidates": 6,
        "hit_candidates": 1,
        "missed_candidates": 2,
        "snapshot_found": 1,
        "k": 15
      },
      "failure_stage": "preselect",
      "failure_details": {
        "missed_candidate_ids": [
          "kg_edge:financial:mentions:2c98471255cf44a7",
          "A股并购重组市场呈现三方面新变化"
        ],
        "wasted_candidate_ids": [
          "kg_ev:financial:news_articles:ft_news:83904:a8934858d26f15da",
          "kg:financial:concept:5223b7f613456aa9",
          "kg:financial:event:e8a50bb3aca0545b",
          "kg:financial:event:a0f8f9de7750b5d3",
          "kg_wiki:financial:relation_page:ed961fe88461d1fd"
        ],
        "base_case_id": "real_ft_news_ft_news_83904",
        "snapshot_id": "kg_rt_snapshot:860b320c-06a9-432f-ba53-fdcab130e812"
      },
      "created_at": null
    },
    {
      "metric_id": "kg_rt_metric:2461df3a-f95d-421b-ba31-e3b28f203c5d",
      "run_id": "kg_rt_eval:f2b89407-d777-4dd8-92ec-81ca65eac463",
      "case_id": "real_ft_news_ft_news_78253@8",
      "query": "2025年年报点评：25年储能业务实现量利齐升，静待海外产能&新兴业务兑现业绩 这条新闻涉及哪些主体、行业或资产影响",
      "metrics": {
        "preselect_recall_at_k": 0.5,
        "preselect_precision_at_k": 0.125,
        "wasted_slots_at_k": 7,
        "expected_candidates": 2,
        "selected_candidates": 8,
        "hit_candidates": 1,
        "missed_candidates": 1,
        "snapshot_found": 1,
        "k": 8
      },
      "failure_stage": "preselect",
      "failure_details": {
        "missed_candidate_ids": [
          "2025年年报点评：25年储能业务实现量利齐升，静待海外产能&新兴业务兑现业绩"
        ],
        "wasted_candidate_ids": [
          "kg_ev:financial:news_articles:ft_news:78253:9826dbf7affb1a80",
          "kg:financial:concept:2784eb007f3e659e",
          "kg:financial:concept:834952ec423a6807",
          "kg:financial:concept:e3c814f87b09626c",
          "kg_ev:financial:news_articles:ft_news:74422:74ef719388442da8",
          "kg_wiki:financial:timeline_page:5068713baedb7308",
          "kg:financial:event:9db80dafad282cbe"
        ],
        "base_case_id": "real_ft_news_ft_news_78253",
        "snapshot_id": "kg_rt_snapshot:205774f3-fddb-48e4-a44f-389e97113458"
      },
      "created_at": null
    },
    {
      "metric_id": "kg_rt_metric:837b51b8-460f-4362-b24d-b7720e748493",
      "run_id": "kg_rt_eval:f2b89407-d777-4dd8-92ec-81ca65eac463",
      "case_id": "real_ft_news_ft_news_78253@12",
      "query": "2025年年报点评：25年储能业务实现量利齐升，静待海外产能&新兴业务兑现业绩 这条新闻涉及哪些主体、行业或资产影响",
      "metrics": {
        "preselect_recall_at_k": 0.5,
        "preselect_precision_at_k": 0.08333333333333333,
        "wasted_slots_at_k": 11,
        "expected_candidates": 2,
        "selected_candidates": 12,
        "hit_candidates": 1,
        "missed_candidates": 1,
        "snapshot_found": 1,
        "k": 12
      },
      "failure_stage": "preselect",
      "failure_details": {
        "missed_candidate_ids": [
          "2025年年报点评：25年储能业务实现量利齐升，静待海外产能&新兴业务兑现业绩"
        ],
        "wasted_candidate_ids": [
          "kg_ev:financial:news_articles:ft_news:78253:9826dbf7affb1a80",
          "kg:financial:concept:2784eb007f3e659e",
          "kg:financial:concept:834952ec423a6807",
          "kg:financial:concept:e3c814f87b09626c",
          "kg_ev:financial:news_articles:ft_news:74422:74ef719388442da8",
          "kg_wiki:financial:timeline_page:5068713baedb7308",
          "kg:financial:event:9db80dafad282cbe",
          "kg_wiki:financial:relation_page:93447f0bc2efd9e4",
          "kg_ev:financial:news_articles:ft_news:74409:62064351d47f57ac",
          "kg_ev:financial:news_articles:ft_news:74414:5d901cf4621d1ad5",
          "kg_ev:financial:news_articles:ft_news:74416:077d9aedd8fdfa40"
        ],
        "base_case_id": "real_ft_news_ft_news_78253",
        "snapshot_id": "kg_rt_snapshot:205774f3-fddb-48e4-a44f-389e97113458"
      },
      "created_at": null
    }
  ]
}
```

## Debug Pointers

- 优先看 full_log 中 `[llm_call] START/DONE/FAILED` 判断是否卡在真实 LLM 请求。
- 若 replay failed，优先看 `generated_retrieval_llm_trace.log` 的 `agentic_case_summary`、`ranker_preselect`、`candidate_judge`。
- 若写入慢，优先看 full_log 中 `financial_news_extraction` 的 source_id 和耗时。
