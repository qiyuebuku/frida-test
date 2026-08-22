#!/system/bin/sh
set -eu

# Android 动态链接器要在 /init 挂载 APEX 后才可用。入口阶段使用镜像内的静态
# Magisk busybox，并临时安装 applet，最后 exec /init 保持 Android init 为 PID 1。
BUSYBOX=/system/etc/init/magisk/busybox
$BUSYBOX mkdir -p /dev/ths-busybox
$BUSYBOX --install -s /dev/ths-busybox
PATH=/dev/ths-busybox
export PATH

# 旧诊断版本曾把登录报文写入此文件；镜像启动时确定性清理，后续 Hook
# 仅记录报文长度，避免任何交易凭据残留在持久化 /data 中。此时 Android
# 动态链接器尚未启动，必须显式调用静态 busybox，不能使用系统 rm。
$BUSYBOX rm -f /data/data/com.hexin.plat.android/files/thshook_login_probe.log \
    2>/dev/null || true

MODULE_BOOTSTRAP=/opt/ths/riru-lsposed-redroid-bootstrap.tar.gz
STATE_DIR=/data/local/tmp/ths-runtime
CONFIG=${STATE_DIR}/bootstrap.env
NEW_INSTANCE=false

# One-time adoption of volumes created by the former golden-/data image. New
# images never create this legacy marker or consume the golden template.
if [ -f /data/.ths-template-initialized ] && [ ! -f /data/.ths-bootstrap-initialized ]; then
    touch /data/.ths-bootstrap-initialized
fi

case "${THS_MODE:-}" in
    collector|trade) ;;
    *) echo "THS_MODE must be collector or trade" >&2; exit 64 ;;
esac

if [ ! -f /data/.ths-bootstrap-initialized ]; then
    if [ "$(find /data -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]; then
        # 一次性接管旧 redroid bind 数据目录。只有显式 existing 模式且两个
        # 必需包的数据都存在时才登记为已初始化，普通新实例仍拒绝未知非空卷。
        if [ "${THS_MODE:-}" = trade ] \
            && [ "${THS_TRADE_INIT:-secrets}" = existing ] \
            && [ -d /data/data/com.hexin.plat.android ] \
            && [ -d /data/data/com.yuyang.thshook ]; then
            touch /data/.ths-bootstrap-initialized
        else
            echo "/data is not empty and is not a THS initialized volume" >&2
            exit 65
        fi
    else
        [ -r "$MODULE_BOOTSTRAP" ] || {
            echo "missing immutable Riru/LSPosed bootstrap" >&2
            exit 66
        }
        tar -xzf "$MODULE_BOOTSTRAP" -C /data
        touch /data/.ths-bootstrap-initialized
        NEW_INSTANCE=true
    fi
fi

if [ "$NEW_INSTANCE" = true ]; then
    # A new volume contains only static Riru/LSPosed modules. Android and THS
    # create all application state from scratch, so no cloned device identity,
    # cookie, account state or market cache can enter a new instance.
    touch /data/.ths-empty-volume-created
fi

mkdir -p "$STATE_DIR" /data/local/tmp/ths-secrets
chmod 0700 "$STATE_DIR" /data/local/tmp/ths-secrets

case "${THS_INSTANCE_ID:-${HOSTNAME:-ths-instance}}" in
    *[!a-zA-Z0-9._-]*|'') echo "invalid THS_INSTANCE_ID" >&2; exit 64 ;;
esac
case "${THS_READY_MAX_ATTEMPTS:-30}:${THS_READY_RETRY_SECONDS:-10}" in
    *[!0-9:]*) echo "warmup limits must be integers" >&2; exit 64 ;;
esac
case "${THS_TRADE_INIT:-secrets}" in
    secrets|existing) ;;
    *) echo "THS_TRADE_INIT must be secrets or existing" >&2; exit 64 ;;
esac

copy_secret() {
    name="$1"
    source_path="$2"
    [ -n "$source_path" ] || return 0
    [ -f "$source_path" ] || { echo "secret not found: $source_path" >&2; exit 66; }
    cp "$source_path" "/data/local/tmp/ths-secrets/$name"
    chmod 0600 "/data/local/tmp/ths-secrets/$name"
}

copy_secret trade_account_seed "${THS_TRADE_ACCOUNT_SEED_FILE:-}"
copy_secret trade_token "${THS_TRADE_TOKEN_FILE:-}"
copy_secret trade_password "${THS_TRADE_PASSWORD_FILE:-}"

umask 077
{
    printf "THS_MODE='%s'\n" "$THS_MODE"
    printf "THS_INSTANCE_ID='%s'\n" "${THS_INSTANCE_ID:-${HOSTNAME:-ths-instance}}"
    printf "THS_ANDROID_ID='%s'\n" "${THS_ANDROID_ID:-}"
    printf "THS_READY_MAX_ATTEMPTS='%s'\n" "${THS_READY_MAX_ATTEMPTS:-30}"
    printf "THS_READY_RETRY_SECONDS='%s'\n" "${THS_READY_RETRY_SECONDS:-10}"
    printf "THS_TRADE_INIT='%s'\n" "${THS_TRADE_INIT:-secrets}"
    printf "THS_TRADE_PASSWORD_LEVELS='%s'\n" "${THS_TRADE_PASSWORD_LEVELS:-1}"
    printf "THS_TRADE_CBAS_HOST='%s'\n" "${THS_TRADE_CBAS_HOST:-8.134.137.28}"
    printf "THS_TRADE_CBAS_PORT='%s'\n" "${THS_TRADE_CBAS_PORT:-9528}"
} > "$CONFIG"
rm -f "$STATE_DIR/ready" "$STATE_DIR/status.json"

exec /init qemu=1 androidboot.hardware=redroid "$@"
