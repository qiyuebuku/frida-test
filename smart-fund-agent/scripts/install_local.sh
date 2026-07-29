#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CODEX_HOME="${CODEX_HOME:-${HOME}/.codex}"
SKILL_TARGET="${CODEX_HOME}/skills/financial-graph-research"

if ! command -v codex >/dev/null 2>&1; then
    echo "codex CLI is required" >&2
    exit 1
fi

mkdir -p "${CODEX_HOME}/skills"
if [[ -L "${SKILL_TARGET}" ]]; then
    rm "${SKILL_TARGET}"
elif [[ -e "${SKILL_TARGET}" ]]; then
    echo "Refusing to replace existing non-symlink Skill: ${SKILL_TARGET}" >&2
    exit 1
fi
ln -s \
    "${PROJECT_ROOT}/skills/financial-graph-research" \
    "${SKILL_TARGET}"

echo "Installed financial-graph-research Skill."
echo "Codex home: ${CODEX_HOME}"
echo "Project-scoped smart-fund-graph MCP is configured in ${PROJECT_ROOT}/.codex/config.toml."
