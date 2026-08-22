#!/bin/sh
set -eu

UPSTREAM_DNS=${THS_REDROID_UPSTREAM_DNS:-10.168.1.3}
for protocol in udp tcp; do
    if ! /usr/sbin/iptables -t nat -C PREROUTING -i docker0 -d 8.8.8.8 \
        -p "$protocol" --dport 53 -j DNAT --to-destination "$UPSTREAM_DNS" \
        2>/dev/null; then
        /usr/sbin/iptables -t nat -I PREROUTING -i docker0 -d 8.8.8.8 \
            -p "$protocol" --dport 53 -j DNAT --to-destination "$UPSTREAM_DNS"
    fi
done
