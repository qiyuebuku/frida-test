#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT="${1:-}"
[[ "${ENVIRONMENT}" == "production" ]] || {
    echo "usage: $0 production [--component COMPONENT[,COMPONENT...]] [--dry-run] [--revision COMMIT]" >&2
    exit 2
}
shift

COMPONENTS="auto"
DRY_RUN=0
REVISION="HEAD"
while (($#)); do
    case "$1" in
        --component) COMPONENTS="${2:?missing component list}"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        --revision) REVISION="${2:?missing revision}"; shift 2 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

cd "${WORKSPACE}"
REVISION="$(git rev-parse --verify "${REVISION}^{commit}")"
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "tracked files have uncommitted changes; commit them before deployment" >&2
    exit 1
fi
git fetch --quiet origin
git merge-base --is-ancestor "${REVISION}" origin/main || {
    echo "revision ${REVISION} has not been pushed to origin/main" >&2
    exit 1
}

DEPLOY_ENV="${DEPLOY_ENV:-${WORKSPACE}/deployment/production.env}"
[[ -f "${DEPLOY_ENV}" ]] || {
    echo "missing local deployment connection config: ${DEPLOY_ENV}" >&2
    exit 1
}
# shellcheck disable=SC1090
source "${DEPLOY_ENV}"
: "${REMOTE_HOST:?required in production.env}"
: "${REMOTE_PORT:?required in production.env}"
: "${REMOTE_USER:?required in production.env}"
SSH_KEY="${SSH_KEY:-/mnt/c/Users/阮雨阳/.ssh/id_rsa}"
: "${SSH_KEY:?required in production.env}"
SSH=(ssh -i "${SSH_KEY}" -p "${REMOTE_PORT}" -o StrictHostKeyChecking=no "${REMOTE_USER}@${REMOTE_HOST}")
STATE_FILE="${REMOTE_STATE_FILE:-/home/${REMOTE_USER}/smart-fund/deployment-state.env}"

state="$("${SSH[@]}" "cat '${STATE_FILE}' 2>/dev/null || true")"
state_value() {
    local key="$1"
    sed -n "s/^${key}=//p" <<<"${state}" | tail -n 1
}
last_hook="$(state_value DEPLOYED_THS_HOOK)"
last_runtime="$(state_value DEPLOYED_THS_RUNTIME)"
last_server_api="$(state_value DEPLOYED_SERVER_API)"
last_server_persist="$(state_value DEPLOYED_SERVER_PERSIST)"
last_server_scheduler="$(state_value DEPLOYED_SERVER_SCHEDULER)"
last_server_workers="$(state_value DEPLOYED_SERVER_WORKERS)"
last_server_stream="$(state_value DEPLOYED_SERVER_THS_STREAM)"
last_server_kg="$(state_value DEPLOYED_SERVER_KG)"
if [[ "${COMPONENTS}" == "auto" ]]; then
    selected=()
    component_changed() {
        local base="$1" pattern="$2"
        [[ -n "${base}" ]] && git cat-file -e "${base}^{commit}" 2>/dev/null \
            && git diff --name-only "${base}" "${REVISION}" | grep -Eq "${pattern}"
    }
    if [[ -z "${last_hook}" ]] || component_changed "${last_hook}" '^(ths/app/|deploy\.sh$|deployment/)'; then
        selected+=(ths-hook)
    fi
    if [[ -z "${last_runtime}" ]] || component_changed "${last_runtime}" '^(ths/deployment/|deploy\.sh$|deployment/)'; then
        selected+=(ths-runtime)
    fi
    server_component_changed() {
        local base="$1" kind="$2" changes common
        [[ -n "${base}" ]] && git cat-file -e "${base}^{commit}" 2>/dev/null || return 0
        changes="$(git diff --name-only "${base}" "${REVISION}")"
        grep -Eq '^(deploy\.sh$|deployment/)' <<<"${changes}" && return 0
        common="$(grep '^smart-fund-server/' <<<"${changes}" \
            | grep -Ev '^smart-fund-server/(docs/|tests/|.*\.md$|src/interfaces/(api/|mcp/|tasks/)|src/interfaces/cli/schedules\.py$|src/application/services/ths_realtime_stream_service\.py$|src/(domain/knowledge/|interfaces/cli/knowledge\.py$|application/services/[^/]*knowledge[^/]*\.py$))' || true)"
        [[ -n "${common}" ]] && return 0
        case "${kind}" in
            api) grep -Eq '^smart-fund-server/src/interfaces/(api|mcp)/' <<<"${changes}" ;;
            persist) return 1 ;;
            scheduler) grep -q '^smart-fund-server/src/interfaces/cli/schedules.py$' <<<"${changes}" ;;
            workers) grep -q '^smart-fund-server/src/interfaces/tasks/' <<<"${changes}" ;;
            ths-stream) grep -q '^smart-fund-server/src/application/services/ths_realtime_stream_service.py$' <<<"${changes}" ;;
            kg) grep -Eq '^smart-fund-server/src/(domain/knowledge/|interfaces/cli/knowledge\.py$|application/services/[^/]*knowledge[^/]*\.py$)' <<<"${changes}" ;;
        esac
    }
    server_component_changed "${last_server_api}" api && selected+=(server-api)
    server_component_changed "${last_server_persist}" persist && selected+=(server-persist)
    server_component_changed "${last_server_scheduler}" scheduler && selected+=(server-scheduler)
    server_component_changed "${last_server_workers}" workers && selected+=(server-workers)
    server_component_changed "${last_server_stream}" ths-stream && selected+=(server-ths-stream)
    server_component_changed "${last_server_kg}" kg && selected+=(server-kg)
    COMPONENTS="$(IFS=,; echo "${selected[*]}")"
fi

expanded_components=()
for component in ${COMPONENTS//,/ }; do
    if [[ "${component}" == "server" ]]; then
        expanded_components+=(server-api server-persist server-scheduler server-workers server-ths-stream server-kg)
    else
        expanded_components+=("${component}")
    fi
done
COMPONENTS="$(IFS=,; echo "${expanded_components[*]}")"
for component in ${COMPONENTS//,/ }; do
    case "${component}" in ths-hook|ths-runtime|server-api|server-persist|server-scheduler|server-workers|server-ths-stream|server-kg) ;; *) echo "invalid component: ${component}" >&2; exit 2 ;; esac
done
if [[ -z "${COMPONENTS}" ]]; then
    echo "No deployable changes detected."
    exit 0
fi

echo "revision:   ${REVISION}"
echo "components: ${COMPONENTS}"
(( DRY_RUN == 0 )) || exit 0

if [[ ",${COMPONENTS}," == *,ths-hook,* || ",${COMPONENTS}," == *,ths-runtime,* ]]; then
    SSH_KEY="${SSH_KEY}" DEPLOY_REVISION="${REVISION}" "${WORKSPACE}/ths/deployment/deploy.sh" \
        --component "${COMPONENTS}" --env-file "${DEPLOY_ENV}"
fi
server_components=()
for component in ${COMPONENTS//,/ }; do
    [[ "${component}" == server-* ]] && server_components+=("${component#server-}")
done
if ((${#server_components[@]} > 0)); then
    server_component_csv="$(IFS=,; echo "${server_components[*]}")"
    SSH_KEY="${SSH_KEY}" DEPLOY_REVISION="${REVISION}" LOCAL_DEPLOY_ENV="${DEPLOY_ENV}" \
        "${WORKSPACE}/smart-fund-server/deployment/deploy_113.sh" --components "${server_component_csv}"
fi

[[ ",${COMPONENTS}," == *,ths-hook,* ]] && last_hook="${REVISION}"
[[ ",${COMPONENTS}," == *,ths-runtime,* ]] && last_runtime="${REVISION}"
[[ ",${COMPONENTS}," == *,server-api,* ]] && last_server_api="${REVISION}"
[[ ",${COMPONENTS}," == *,server-persist,* ]] && last_server_persist="${REVISION}"
[[ ",${COMPONENTS}," == *,server-scheduler,* ]] && last_server_scheduler="${REVISION}"
[[ ",${COMPONENTS}," == *,server-workers,* ]] && last_server_workers="${REVISION}"
[[ ",${COMPONENTS}," == *,server-ths-stream,* ]] && last_server_stream="${REVISION}"
[[ ",${COMPONENTS}," == *,server-kg,* ]] && last_server_kg="${REVISION}"
"${SSH[@]}" "install -d '/home/${REMOTE_USER}/smart-fund'; { \
printf '%s\n' 'DEPLOYED_THS_HOOK=${last_hook}'; \
printf '%s\n' 'DEPLOYED_THS_RUNTIME=${last_runtime}'; \
printf '%s\n' 'DEPLOYED_SERVER_API=${last_server_api}'; \
printf '%s\n' 'DEPLOYED_SERVER_PERSIST=${last_server_persist}'; \
printf '%s\n' 'DEPLOYED_SERVER_SCHEDULER=${last_server_scheduler}'; \
printf '%s\n' 'DEPLOYED_SERVER_WORKERS=${last_server_workers}'; \
printf '%s\n' 'DEPLOYED_SERVER_THS_STREAM=${last_server_stream}'; \
printf '%s\n' 'DEPLOYED_SERVER_KG=${last_server_kg}'; \
} > '${STATE_FILE}.tmp'; mv '${STATE_FILE}.tmp' '${STATE_FILE}'"
echo "deployment completed: ${REVISION}"
