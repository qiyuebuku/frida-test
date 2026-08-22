#!/usr/bin/env bash
set -euo pipefail

REGISTRY_NAME=ths-redroid-registry
REGISTRY_IMAGE=registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373
REGISTRY_DATA=${THS_REGISTRY_DATA:-/home/yuyangruan/.smart-fund-registry}

[[ "${GITHUB_ACTIONS:-}" == true && "${GITHUB_EVENT_NAME:-}" == push \
    && "${GITHUB_REF:-}" == refs/heads/main \
    && "${RUNNER_ENVIRONMENT:-}" == self-hosted ]] || {
    echo "the production registry may only be managed by the main self-hosted Actions job" >&2
    exit 1
}

install -d -m 0700 "$REGISTRY_DATA"
if docker inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
    actual_image=$(docker inspect "$REGISTRY_NAME" --format '{{.Config.Image}}')
    actual_binding=$(docker inspect "$REGISTRY_NAME" --format '{{(index (index .HostConfig.PortBindings "5000/tcp") 0).HostIp}}:{{(index (index .HostConfig.PortBindings "5000/tcp") 0).HostPort}}')
    [[ "$actual_image" == "$REGISTRY_IMAGE" && "$actual_binding" == 127.0.0.1:5000 ]] || {
        echo "existing production registry does not match the locked image/binding" >&2
        exit 65
    }
    docker start "$REGISTRY_NAME" >/dev/null
else
    docker run -d --name "$REGISTRY_NAME" --restart unless-stopped \
        -p 127.0.0.1:5000:5000 \
        -v "$REGISTRY_DATA:/var/lib/registry" \
        "$REGISTRY_IMAGE" >/dev/null
fi

for attempt in 1 2 3 4 5 6 7 8 9 10; do
    curl -fsS http://127.0.0.1:5000/v2/ >/dev/null && exit 0
    sleep 1
done
echo "private loopback registry did not become ready" >&2
exit 69
