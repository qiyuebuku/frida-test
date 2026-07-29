#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME="${SMART_FUND_CODEX_HOME:-${HOME}/.codex-smart-fund}"
CONFIG_TEMPLATE="${PROJECT_ROOT}/config/codex-home.config.toml"
AICLIENT_ENV_FILE="${AICLIENT2API_ENV_FILE:-${PROJECT_ROOT}/../AIClient2API/.deployment.local.env}"
AGENT_ENV_FILE="${SMART_FUND_AGENT_ENV_FILE:-${PROJECT_ROOT}/.deployment.local.env}"
BIN_DIR="${SMART_FUND_CODEX_BIN_DIR:-${HOME}/.local/bin}"
BIN_TARGET="${BIN_DIR}/smart-fund-codex"
DEFAULT_SKILL="${HOME}/.codex/skills/financial-graph-research"

if ! command -v codex >/dev/null 2>&1; then
    echo "codex CLI is required" >&2
    exit 1
fi

if [[ ! -r "${AICLIENT_ENV_FILE}" ]]; then
    echo "AIClient2API environment file is not readable: ${AICLIENT_ENV_FILE}" >&2
    exit 1
fi

if ! grep -q '^AICLIENT2API_API_KEY=.' "${AICLIENT_ENV_FILE}"; then
    echo "AICLIENT2API_API_KEY is missing from ${AICLIENT_ENV_FILE}" >&2
    exit 1
fi
if [[ ! -r "${AGENT_ENV_FILE}" ]]; then
    echo "Smart Fund Agent environment file is not readable: ${AGENT_ENV_FILE}" >&2
    exit 1
fi
if ! grep -q '^SMART_FUND_MCP_BEARER_TOKEN=.' "${AGENT_ENV_FILE}"; then
    echo "SMART_FUND_MCP_BEARER_TOKEN is missing from ${AGENT_ENV_FILE}" >&2
    exit 1
fi

mkdir -p "${CODEX_HOME}" "${BIN_DIR}"

python3 - \
    "${CONFIG_TEMPLATE}" \
    "${CODEX_HOME}/config.toml" \
    "${CODEX_HOME}/model-catalog.json" \
    "${PROJECT_ROOT}" \
    "${CODEX_HOME}" <<'PY'
import copy
import json
import pathlib
import subprocess
import sys

template_path = pathlib.Path(sys.argv[1])
target_path = pathlib.Path(sys.argv[2])
catalog_path = pathlib.Path(sys.argv[3])
project_root = sys.argv[4]
codex_home = sys.argv[5]

rendered = (
    template_path.read_text(encoding="utf-8")
    .replace("__PROJECT_ROOT__", project_root)
    .replace("__CODEX_HOME__", codex_home)
)
temporary_path = target_path.with_suffix(".toml.tmp")
temporary_path.write_text(rendered, encoding="utf-8")
temporary_path.replace(target_path)

bundled_result = subprocess.run(
    ["codex", "debug", "models", "--bundled"],
    check=True,
    capture_output=True,
    text=True,
)
bundled_catalog = json.loads(bundled_result.stdout)
base_model = next(
    (
        model
        for model in bundled_catalog.get("models", [])
        if model.get("slug") == "gpt-5.2"
    ),
    None,
)
if base_model is None:
    raise RuntimeError(
        "The installed Codex CLI does not provide the gpt-5.2 metadata template"
    )

glm_model = copy.deepcopy(base_model)
glm_model.update(
    {
        "slug": "glm-5.2",
        "display_name": "GLM-5.2",
        "description": (
            "GLM-5.2 through AIClient2API with a 1M-token context window"
        ),
        "default_reasoning_level": None,
        "supported_reasoning_levels": [],
        "visibility": "list",
        "supported_in_api": True,
        "priority": 0,
        "additional_speed_tiers": [],
        "service_tiers": [],
        "default_service_tier": None,
        "availability_nux": None,
        "upgrade": None,
        "supports_reasoning_summary_parameter": False,
        "default_reasoning_summary": "none",
        "support_verbosity": False,
        "default_verbosity": None,
        "supports_parallel_tool_calls": True,
        "supports_image_detail_original": False,
        "context_window": 1_000_000,
        "max_context_window": 1_000_000,
        "auto_compact_token_limit": 900_000,
        "effective_context_window_percent": 95,
        "comp_hash": "glm-5.2-aiclient2api-v1",
        "input_modalities": ["text"],
        "supports_search_tool": False,
        "use_responses_lite": False,
    }
)
glm_model.pop("tool_mode", None)
glm_model.pop("multi_agent_version", None)

catalog_temporary_path = catalog_path.with_suffix(".json.tmp")
catalog_temporary_path.write_text(
    json.dumps({"models": [glm_model]}, ensure_ascii=True, indent=2) + "\n",
    encoding="utf-8",
)
catalog_temporary_path.replace(catalog_path)
PY

CODEX_HOME="${CODEX_HOME}" bash "${PROJECT_ROOT}/scripts/install_local.sh"

if [[ -L "${BIN_TARGET}" ]]; then
    rm "${BIN_TARGET}"
elif [[ -e "${BIN_TARGET}" ]]; then
    echo "Refusing to replace existing non-symlink launcher: ${BIN_TARGET}" >&2
    exit 1
fi
ln -s "${PROJECT_ROOT}/scripts/smart-fund-codex" "${BIN_TARGET}"

if [[ -L "${DEFAULT_SKILL}" ]] \
    && [[ "$(readlink -f "${DEFAULT_SKILL}")" == "${PROJECT_ROOT}/skills/financial-graph-research" ]]; then
    rm "${DEFAULT_SKILL}"
fi

echo "Smart Fund Codex runtime initialized:"
echo "  CODEX_HOME=${CODEX_HOME}"
echo "  launcher=${BIN_TARGET}"
echo "  project=${PROJECT_ROOT}"
echo
echo "Run: smart-fund-codex"
