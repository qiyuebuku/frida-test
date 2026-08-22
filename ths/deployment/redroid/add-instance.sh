#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
usage: add-instance.sh --name NAME --mode collector|trade --adb-port PORT --http-port PORT [options]
  --image IMAGE                    default: yuyangruan/ths-redroid:1.2.50-headless
  --account-seed-secret FILE       required for trade secrets initialization
  --token-secret FILE              optional exported token JSON
  --password-secret FILE           optional plain trading password
  --account-secret FILE            optional plain broker account identifier
  --broker-secret FILE             optional broker display name
  --qsid-secret FILE               optional broker qsid
  --password-levels 1|2            explicit trade password policy metadata
  --trade-init secrets|existing    default: secrets
  --ready-timeout SECONDS          default: 300; command succeeds only when healthy
  --data-dir DIR                   adopt an existing trade bind directory
EOF
    exit 64
}

NAME= MODE= ADB_PORT= HTTP_PORT=
IMAGE=yuyangruan/ths-redroid:1.2.50-headless
ACCOUNT_SEED= TOKEN= PASSWORD= ACCOUNT= BROKER= QSID= PASSWORD_LEVELS=1 TRADE_INIT=secrets READY_TIMEOUT=300 DATA_DIR=
while (($#)); do
    case "$1" in
        --name) NAME=${2:-}; shift 2 ;;
        --mode) MODE=${2:-}; shift 2 ;;
        --adb-port) ADB_PORT=${2:-}; shift 2 ;;
        --http-port) HTTP_PORT=${2:-}; shift 2 ;;
        --image) IMAGE=${2:-}; shift 2 ;;
        --account-seed-secret) ACCOUNT_SEED=${2:-}; shift 2 ;;
        --token-secret) TOKEN=${2:-}; shift 2 ;;
        --password-secret) PASSWORD=${2:-}; shift 2 ;;
        --account-secret) ACCOUNT=${2:-}; shift 2 ;;
        --broker-secret) BROKER=${2:-}; shift 2 ;;
        --qsid-secret) QSID=${2:-}; shift 2 ;;
        --password-levels) PASSWORD_LEVELS=${2:-}; shift 2 ;;
        --trade-init) TRADE_INIT=${2:-}; shift 2 ;;
        --ready-timeout) READY_TIMEOUT=${2:-}; shift 2 ;;
        --data-dir) DATA_DIR=${2:-}; shift 2 ;;
        *) usage ;;
    esac
done

[[ "$NAME" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || usage
[[ "$MODE" == collector || "$MODE" == trade ]] || usage
[[ "$ADB_PORT" =~ ^[0-9]+$ && "$HTTP_PORT" =~ ^[0-9]+$ ]] || usage
[[ "$PASSWORD_LEVELS" == 1 || "$PASSWORD_LEVELS" == 2 ]] || usage
[[ "$TRADE_INIT" == secrets || "$TRADE_INIT" == existing ]] || usage
[[ "$READY_TIMEOUT" =~ ^[1-9][0-9]*$ ]] || usage
if [[ "$MODE" == trade && "$TRADE_INIT" == secrets && ! -f "$ACCOUNT_SEED" ]]; then
    echo "trade secrets mode requires --account-seed-secret" >&2
    exit 66
fi
if [[ -n "$DATA_DIR" ]]; then
    [[ "$MODE" == trade && "$TRADE_INIT" == existing ]] || {
        echo "--data-dir is only allowed for trade --trade-init existing migration" >&2
        exit 66
    }
    [[ -d "$DATA_DIR/data/com.hexin.plat.android" \
        && -d "$DATA_DIR/data/com.yuyang.thshook" ]] || {
        echo "existing data directory does not contain required THS packages" >&2
        exit 66
    }
    DATA_DIR=$(realpath "$DATA_DIR")
fi

for port in "$ADB_PORT" "$HTTP_PORT"; do
    if ss -H -ltn "sport = :$port" | grep -q .; then
        echo "host port $port is already in use" >&2
        exit 67
    fi
done
docker container inspect "$NAME" >/dev/null 2>&1 && {
    echo "container already exists: $NAME" >&2
    exit 68
}

args=(
    run -d --privileged --name "$NAME" --hostname "$NAME"
    # A healthy THS/redroid instance currently settles around 1.6-1.9 GiB.
    # Keep enough headroom for native response bursts while preventing one
    # leaking App process from consuming unbounded host memory.
    --restart unless-stopped --memory 4g --memory-swap 4g --cpus 2
    -p "127.0.0.1:${ADB_PORT}:5555"
    -p "127.0.0.1:${HTTP_PORT}:18900"
    --dns 10.168.1.3 --dns 223.5.5.5
    -e "THS_MODE=$MODE" -e "THS_INSTANCE_ID=$NAME"
    -e "THS_TRADE_INIT=$TRADE_INIT"
    -e "THS_TRADE_PASSWORD_LEVELS=$PASSWORD_LEVELS"
)
if [[ -n "$DATA_DIR" ]]; then
    args+=(-v "$DATA_DIR:/data")
else
    args+=(-v "${NAME}-data:/data")
fi
mount_secret() {
    local env_name=$1 source_file=$2 target_name=$3
    [[ -n "$source_file" ]] || return 0
    [[ -f "$source_file" ]] || { echo "secret not found: $source_file" >&2; exit 66; }
    source_file=$(realpath "$source_file")
    args+=(--mount "type=bind,src=$source_file,dst=/run/secrets/$target_name,readonly")
    args+=(-e "$env_name=/run/secrets/$target_name")
}
mount_secret THS_TRADE_ACCOUNT_SEED_FILE "$ACCOUNT_SEED" trade_account_seed
mount_secret THS_TRADE_TOKEN_FILE "$TOKEN" trade_token
mount_secret THS_TRADE_PASSWORD_FILE "$PASSWORD" trade_password
mount_secret THS_TRADE_ACCOUNT_FILE "$ACCOUNT" trade_account
mount_secret THS_TRADE_BROKER_FILE "$BROKER" trade_broker
mount_secret THS_TRADE_QSID_FILE "$QSID" trade_qsid

args+=("$IMAGE"
    ro.product.cpu.abilist=x86_64,arm64-v8a,x86,armeabi-v7a,armeabi
    ro.product.cpu.abilist64=x86_64,arm64-v8a
    ro.product.cpu.abilist32=x86,armeabi-v7a
    ro.dalvik.vm.isa.arm=x86 ro.dalvik.vm.isa.arm64=x86_64
    ro.enable.native.bridge.exec=1 ro.vendor.enable.native.bridge.exec=1
    ro.vendor.enable.native.bridge.exec64=1
    ro.dalvik.vm.native.bridge=libriruloader.so
    androidboot.redroid_gpu_mode=guest androidboot.use_memfd=1)

container_id=$(docker "${args[@]}")
echo "created $NAME ($container_id); waiting for deterministic readiness"
deadline=$((SECONDS + READY_TIMEOUT))
while ((SECONDS < deadline)); do
    state=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME")
    if [[ "$state" == healthy ]]; then
        echo "$NAME is healthy and ready for traffic"
        exit 0
    fi
    if [[ $(docker inspect --format '{{.State.Status}}' "$NAME") == exited ]]; then
        break
    fi
    sleep 2
done
echo "$NAME failed to become healthy within ${READY_TIMEOUT}s" >&2
docker inspect --format 'container={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$NAME" >&2 || true
docker exec "$NAME" sh -c 'cat /data/local/tmp/ths-runtime/status.json 2>/dev/null; tail -n 80 /data/local/tmp/ths-runtime/runtime.log 2>/dev/null' >&2 || true
exit 69
