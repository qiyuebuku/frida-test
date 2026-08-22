#!/usr/bin/env bash
set -euo pipefail

REGISTRY_NAME=smart-fund-registry
REGISTRY_IMAGE=registry@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373
REGISTRY_VOLUME=smart-fund-registry-data

[[ "${GITHUB_ACTIONS:-}" == true && "${GITHUB_EVENT_NAME:-}" == push \
    && "${GITHUB_REF:-}" == refs/heads/main \
    && "${RUNNER_ENVIRONMENT:-}" == self-hosted ]] || {
    echo "the production registry may only be managed by the main self-hosted Actions job" >&2
    exit 1
}

docker image inspect "$REGISTRY_IMAGE" >/dev/null 2>&1 || docker pull "$REGISTRY_IMAGE" >/dev/null
expected_image_id=$(docker image inspect "$REGISTRY_IMAGE" --format '{{.Id}}')
if docker inspect "$REGISTRY_NAME" >/dev/null 2>&1; then
    actual_image_id=$(docker inspect "$REGISTRY_NAME" --format '{{.Image}}')
    actual_binding=$(docker inspect "$REGISTRY_NAME" --format '{{(index (index .HostConfig.PortBindings "5000/tcp") 0).HostIp}}:{{(index (index .HostConfig.PortBindings "5000/tcp") 0).HostPort}}')
    actual_volume=$(docker inspect "$REGISTRY_NAME" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/registry"}}{{.Name}}{{end}}{{end}}')
    [[ "$actual_image_id" == "$expected_image_id" \
        && "$actual_binding" == 127.0.0.1:5000 \
        && "$actual_volume" == "$REGISTRY_VOLUME" ]] || {
        echo "existing production registry does not match the locked image/binding" >&2
        exit 65
    }
    docker start "$REGISTRY_NAME" >/dev/null
else
    docker run -d --name "$REGISTRY_NAME" --restart unless-stopped \
        -p 127.0.0.1:5000:5000 \
        -v "$REGISTRY_VOLUME:/var/lib/registry" \
        "$REGISTRY_IMAGE" >/dev/null
fi

for attempt in 1 2 3 4 5 6 7 8 9 10; do
    curl -fsS http://127.0.0.1:5000/v2/ >/dev/null && exit 0
    sleep 1
done
echo "private loopback registry did not become ready" >&2
exit 69
