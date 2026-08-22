#!/bin/sh
set -eu

UPSTREAM_DNS=${THS_REDROID_UPSTREAM_DNS:-10.168.1.3}
DOCKER_BRIDGE=${THS_DOCKER_BRIDGE:-docker0}

# redroid resolves through Android's fixed 8.8.8.8 endpoint. Maintain the
# host-network DNAT rule idempotently from the same Docker-managed component
# that owns the collector gateway.
for protocol in udp tcp; do
    if ! iptables -t nat -C PREROUTING -i "$DOCKER_BRIDGE" -d 8.8.8.8 \
        -p "$protocol" --dport 53 -j DNAT --to-destination "$UPSTREAM_DNS" \
        2>/dev/null; then
        iptables -t nat -I PREROUTING -i "$DOCKER_BRIDGE" -d 8.8.8.8 \
            -p "$protocol" --dport 53 -j DNAT --to-destination "$UPSTREAM_DNS"
    fi
done

set -- python3 /opt/ths-gateway/app-load-balancer.py \
    --listen-port "${THS_GATEWAY_PORT:-49350}" \
    --stream-listen-port "${THS_GATEWAY_STREAM_PORT:-49352}" \
    --passive-recovery \
    --hook-health-only \
    --backend collector1=127.0.0.1:49610 \
    --backend collector2=127.0.0.1:49611 \
    --backend collector3=127.0.0.1:49612 \
    --backend collector4=127.0.0.1:49613 \
    --backend collector5=127.0.0.1:49614 \
    --backend collector6=127.0.0.1:49615 \
    --backend collector7=127.0.0.1:49616 \
    --backend collector8=127.0.0.1:49617 \
    --stream-backend collector1=49610 \
    --stream-backend collector2=49611 \
    --stream-backend collector3=49612 \
    --stream-backend collector4=49613 \
    --stream-backend collector5=49614 \
    --stream-backend collector6=49615 \
    --stream-backend collector7=49616 \
    --stream-backend collector8=49617

exec "$@"
