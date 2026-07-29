# AGENTS.md - smart-fund-agent

This project is the Codex-facing agent layer for `../smart-fund-server`.

- Keep database, Milvus, retrieval, and graph logic in `smart-fund-server`.
- MCP tools are hosted by `smart-fund-server`; this project only registers the
  remote Streamable HTTP endpoint and maintains the Agent Skill.
- Do not recreate a standalone LLM tool-calling loop in this project.
- Codex owns tool selection and iteration; the Skill only defines retrieval policy.
- Relationship claims require an opened Edge. Factual claims require an opened Card
  or Edge carrying readable evidence.
