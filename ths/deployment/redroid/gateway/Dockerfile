FROM python:3.12-slim-bookworm

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iptables ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/ths-gateway
COPY internal/runtime/app-load-balancer.py /opt/ths-gateway/app-load-balancer.py
COPY redroid/gateway/gateway-entrypoint.sh /usr/local/bin/ths-gateway-entrypoint
RUN chmod 0755 /usr/local/bin/ths-gateway-entrypoint

ENTRYPOINT ["/usr/local/bin/ths-gateway-entrypoint"]
