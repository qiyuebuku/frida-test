# Smart Fund Agent

`smart-fund-agent` is the Codex-facing financial research layer for
`../smart-fund-server`.

The server owns PostgreSQL, Milvus, semantic retrieval, relation verification,
graph APIs, provider-neutral external research adapters, and the Streamable HTTP
MCP endpoint. This project contributes:

- project-scoped registration of the server-hosted MCP endpoint;
- a Codex Skill that defines retrieval and evidence policy;
- no local Python MCP process or duplicate HTTP adapter.

The plugin packages the Skill only. MCP registration deliberately lives in the
project's `.codex/config.toml`, so the graph tools are unavailable outside this
project.

It does not implement another LLM tool loop. Codex is the Agent runtime.

## Isolated Codex Runtime

```bash
bash scripts/setup_codex_runtime.sh
```

The setup command creates an isolated `~/.codex-smart-fund` runtime and a
`~/.local/bin/smart-fund-codex` launcher. The isolated runtime:

- uses `glm-5.2` through the AIClient2API Responses endpoint;
- generates a Codex model catalog for GLM-5.2 with its 1M context window and
  a 900K automatic compaction threshold;
- reads `AICLIENT2API_API_KEY` at launch time from
  `../AIClient2API/.deployment.local.env`;
- installs `financial-graph-research` only under the isolated Codex home;
- trusts this project so its project-scoped MCP configuration is loaded;
- keeps model config, sessions, cache, logs, and Skills separate from the
  normal `~/.codex` runtime.

The regular `codex` command continues to use the official provider and the
normal `~/.codex` state.

The model catalog is generated from the installed Codex metadata template at
setup time. This preserves Codex's current Agent instructions while replacing
the model identity, context limits, input modalities, and unsupported
OpenAI-specific request capabilities with GLM-5.2 values.

## Usage

Start the dedicated interactive runtime:

```bash
smart-fund-codex
```

Then invoke the Skill:

```text
$financial-graph-research 分析存储芯片涨价是否已经影响手机厂商产品策略，并给出图谱证据。
```

For an isolated batch run:

```bash
smart-fund-codex exec --ephemeral --sandbox read-only \
  '$financial-graph-research 分析存储芯片涨价是否已经影响手机厂商产品策略，并给出图谱证据。'
```

The project-scoped MCP URL is configured in `.codex/config.toml` as
`http://119.23.227.187:8900/mcp`.
Its Bearer Token is read from the Git-ignored `.deployment.local.env` through
`SMART_FUND_MCP_BEARER_TOKEN`; `smart-fund-codex` loads it automatically.

The same endpoint exposes verified graph reads, persistent `market_*` tracking
tools, and provider-neutral `external_*` research tools. Provider credentials,
state, and routing remain in `smart-fund-server`; this Agent project does not
register vendor MCP servers or store vendor keys.

To use a different Codex home or AIClient2API environment file:

```bash
SMART_FUND_CODEX_HOME=/path/to/codex-home \
AICLIENT2API_ENV_FILE=/path/to/aiclient.env \
SMART_FUND_AGENT_ENV_FILE=/path/to/agent.env \
bash scripts/setup_codex_runtime.sh
```

The same variables are supported by `smart-fund-codex`.

## Verification

```bash
python3 /home/yuyang/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/financial-graph-research
python3 /home/yuyang/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```
