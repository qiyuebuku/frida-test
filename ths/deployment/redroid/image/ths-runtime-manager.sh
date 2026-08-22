#!/system/bin/sh
set -u

STATE_DIR=/data/local/tmp/ths-runtime
CONFIG=$STATE_DIR/bootstrap.env
SECRETS=/data/local/tmp/ths-secrets
LOG=$STATE_DIR/runtime.log
READY=$STATE_DIR/ready
STATUS=$STATE_DIR/status.json
HOOK_APK=/opt/ths/ths-hook.apk
THS_APK=/opt/ths/ths.apk

exec >>"$LOG" 2>&1
echo "$(date '+%F %T') runtime manager starting"
# The data volume survives container replacement.  Never let a ready marker
# from the previous Android boot satisfy Docker's health check for this boot.
rm -f "$READY"

[ -r "$CONFIG" ] || { echo "missing $CONFIG"; exit 1; }
# 文件由 PID 1 entrypoint 生成，只允许预定义的单引号字符串。
. "$CONFIG"

json_status() {
    phase="$1"
    detail="$2"
    printf '{"ready":false,"mode":"%s","phase":"%s","detail":"%s"}\n' \
        "$THS_MODE" "$phase" "$detail" > "$STATUS"
}

http() {
    method="$1"
    path="$2"
    body="${3:-}"
    length=${#body}
    {
        printf '%s %s HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' \
            "$method" "$path" "$length"
        printf '%s' "$body"
    } | toybox nc -w 55 127.0.0.1 18900 | sed '1,/^\r$/d'
}

wait_http() {
    n=0
    while [ "$n" -lt 120 ]; do
        if http GET /health 2>/dev/null | grep -q '"ok":true'; then return 0; fi
        n=$((n + 1))
        sleep 2
    done
    return 1
}

complete_app_onboarding() {
    BOOTSTRAP_STRATEGY=direct
    BOOTSTRAP_DETAIL='{}'
    # Preferred path: invoke THS's official privacy callback and final
    # NewUserTagsSelectContainer.goToHexin() business entry point. Repeating
    # this endpoint is safe and lets asynchronously-created startup objects
    # become available without guessing a fixed delay.
    attempt=1
    while [ "$attempt" -le 12 ]; do
        BOOTSTRAP_DETAIL=$(http POST /admin/bootstrap '{}' 2>/dev/null || true)
        sleep 1
        verify_hurricane_runtime && return 0
        attempt=$((attempt + 1))
    done

    # Compatibility fallback for a future App build whose obfuscated direct
    # entry points changed. This still invokes the App's real listeners by
    # resource ID; it never uses coordinates or shell-injected gestures.
    BOOTSTRAP_STRATEGY=view_fallback
    attempt=1
    while [ "$attempt" -le 13 ]; do
        http POST /admin/view-click '{"view_id":2131309195}' >/dev/null 2>&1 || true
        http POST /admin/view-click '{"view_id":2131306063}' >/dev/null 2>&1 || true
        http POST /admin/view-click '{"view_id":2131306169}' >/dev/null 2>&1 || true
        sleep 1
        verify_hurricane_runtime && return 0
        attempt=$((attempt + 1))
    done
    return 1
}

verify_hurricane_runtime() {
    result=$(http POST /native/hurricane '{"frame_id":2312,"start":0,"count":2,"hurricane_type":"TAG","hurricane_ids":["cn_concept"],"hurricane_indicator_ids":["ths-hot-data-minute-attention-rate"],"mobile_indicator_ids":["34818"],"sort_indicator_id":"ths-hot-data-minute-attention-rate","order":"DESCENDING","http_source_id":"sif-quoter-dataapi-sector-statistics","source_header_id":"sif-quoter-dataapi-sector-statistics","timeout_ms":10000}' 2>/dev/null || true)
    printf '%s' "$result" | grep -q '"success":true'
}

ensure_riru_injected() {
    # 镜像在 post-fs-data 阶段同步准备 Riru/LSPosed 文件，再启动 rirud。
    # 此处只做最终门禁；禁止在运行期重启 zygote，以免多个 redroid 实例
    # 同时重启 framework 给宿主机造成负载尖峰。
    attempt=1
    while [ "$attempt" -le 15 ]; do
        zygote_pid=$(pidof zygote64 2>/dev/null | awk '{print $1}')
        if [ -n "$zygote_pid" ] \
            && grep -q '/libriru\.so' "/proc/$zygote_pid/maps" 2>/dev/null \
            && grep -q '/liblspd\.so' "/proc/$zygote_pid/maps" 2>/dev/null; then
            return 0
        fi
        json_status riru_waiting "waiting for Riru/LSPosed zygote injection"
        sleep 1
        attempt=$((attempt + 1))
    done
    json_status riru_unavailable "Riru/LSPosed did not enter zygote"
    echo "Riru/LSPosed injection missing; requesting automatic container reboot"
    # Android init exits on poweroff; Docker's unless-stopped policy starts the
    # same immutable instance again. This replaces the former manual docker
    # restart workaround and never force-stops the App.
    reboot -p >/dev/null 2>&1 || setprop sys.powerctl shutdown
    return 1
}

install_apk_artifact() {
    package="$1"
    artifact="$2"
    label="$3"
    [ -r "$artifact" ] || { json_status artifact_missing "missing embedded $label APK"; return 1; }
    installed=$(pm path "$package" 2>/dev/null | head -n 1 | sed 's/^package://')
    expected_sha=$(sha256sum "$artifact" | cut -d' ' -f1)
    installed_sha=$(sha256sum "$installed" 2>/dev/null | cut -d' ' -f1 || true)
    if [ "$installed_sha" != "$expected_sha" ]; then
        json_status artifact_installing "installing embedded $label artifact"
        install_result=$(pm install -r "$artifact" 2>&1 || true)
        printf '%s' "$install_result" | grep -q '^Success' \
            || { echo "$install_result"; json_status artifact_install_failed "embedded $label install failed"; return 1; }
    fi
    installed=$(pm path "$package" 2>/dev/null | head -n 1 | sed 's/^package://')
    installed_sha=$(sha256sum "$installed" 2>/dev/null | cut -d' ' -f1 || true)
    [ "$installed_sha" = "$expected_sha" ] \
        || { json_status artifact_verify_failed "installed $label digest mismatch"; return 1; }
}

configure_lsposed_scope() {
    db=/data/adb/lspd/config/modules_config.db
    hook_path=$(pm path com.yuyang.thshook 2>/dev/null | head -n 1 | sed 's/^package://')
    case "$hook_path" in /data/app/*/base.apk) ;; *) json_status scope_path_invalid "invalid Hook APK path"; return 1 ;; esac
    attempt=1
    while [ "$attempt" -le 30 ] && [ ! -s "$db" ]; do sleep 1; attempt=$((attempt + 1)); done
    [ -s "$db" ] || { json_status scope_db_missing "LSPosed module database missing"; return 1; }
    expected_sha=$(sha256sum "$HOOK_APK" | cut -d' ' -f1)
    marker=$STATE_DIR/lsposed-scope.sha256
    [ "$(cat "$marker" 2>/dev/null || true)" = "$expected_sha" ] && return 0
    pkill -STOP lspd >/dev/null 2>&1 || true
    /system/bin/sqlite3 "$db" <<SQL
BEGIN IMMEDIATE;
INSERT INTO modules(module_pkg_name, apk_path, enabled)
VALUES('com.yuyang.thshook', '$hook_path', 1)
ON CONFLICT(module_pkg_name) DO UPDATE SET apk_path=excluded.apk_path, enabled=1;
DELETE FROM scope WHERE mid=(SELECT mid FROM modules WHERE module_pkg_name='com.yuyang.thshook');
INSERT INTO scope(mid, app_pkg_name, user_id)
SELECT mid, 'com.hexin.plat.android', 0 FROM modules WHERE module_pkg_name='com.yuyang.thshook';
COMMIT;
SQL
    pkill -CONT lspd >/dev/null 2>&1 || true
    enabled=$(/system/bin/sqlite3 "$db" "SELECT enabled FROM modules WHERE module_pkg_name='com.yuyang.thshook';")
    scoped=$(/system/bin/sqlite3 "$db" "SELECT count(*) FROM scope s JOIN modules m ON m.mid=s.mid WHERE m.module_pkg_name='com.yuyang.thshook' AND s.app_pkg_name='com.hexin.plat.android' AND s.user_id=0;")
    [ "$enabled:$scoped" = 1:1 ] || { json_status scope_verify_failed "LSPosed scope verification failed"; return 1; }
    printf '%s\n' "$expected_sha" > "$marker"
    json_status scope_rebooting "rebooting once to activate deterministic LSPosed scope"
    sync
    reboot -p >/dev/null 2>&1 || setprop sys.powerctl shutdown
    return 2
}

start_ths_app() {
    attempt=1
    while [ "$attempt" -le 3 ]; do
        am start -W -n com.hexin.plat.android/.LogoEmptyActivity >/dev/null 2>&1 || true
        n=0
        while [ "$n" -lt 15 ]; do
            pidof com.hexin.plat.android >/dev/null && return 0
            n=$((n + 1))
            sleep 1
        done
        attempt=$((attempt + 1))
    done
    return 1
}

initialize_trade_channel() {
    # CommunicationService 只有收到这个官方 extra 才初始化 socket pool。
    am start-service \
        -n com.hexin.plat.android/com.hexin.plat.android.CommunicationService \
        --es hexin_connect_hangqing_flag_key hexin_connect_hangqing_flag >/dev/null 2>&1 \
        || return 1
    n=0
    while [ "$n" -lt 15 ]; do
        result=$(http POST /stock/trade/cbas \
            "{\"host\":\"$THS_TRADE_CBAS_HOST\",\"port\":$THS_TRADE_CBAS_PORT}" 2>/dev/null || true)
        printf '%s' "$result" | grep -q '"ok":true' && return 0
        n=$((n + 1))
        sleep 1
    done
    return 1
}

enter_headless_runtime() {
    # redroid does not transition its virtual display to OFF in response to
    # KEYCODE_SLEEP.  Leaving Hexin as the resumed activity keeps every page's
    # RenderThread drawing into that always-on display and costs roughly half a
    # CPU core per instance.  Once all native runtimes are ready, move the real
    # App to the background by opening Android's static launcher.  The Hook HTTP
    # server and market/trade socket services remain alive in the App process.
    settings put global bluetooth_on 0
    settings put global ble_scan_always_enabled 0
    settings put global wifi_scan_always_enabled 0
    cmd bluetooth_manager disable >/dev/null 2>&1 || true
    cmd location set-location-enabled false --user 0 >/dev/null 2>&1 || true
    cmd deviceidle whitelist +com.hexin.plat.android >/dev/null 2>&1 || true
    am set-standby-bucket com.hexin.plat.android active >/dev/null 2>&1 || true
    appops set com.hexin.plat.android RUN_IN_BACKGROUND allow >/dev/null 2>&1 || true
    appops set com.hexin.plat.android RUN_ANY_IN_BACKGROUND allow >/dev/null 2>&1 || true

    # These packages are unrelated to market collection and trading.  Disable
    # them deterministically for every new volume instead of relying on a host
    # ADB maintenance script.
    for package in \
        com.android.bluetooth \
        com.android.bluetoothmidiservice \
        com.android.nfc \
        com.android.printspooler \
        com.android.printservice.recommendation \
        com.android.camera2 \
        com.android.music \
        com.android.musicfx \
        com.android.wallpaper.livepicker \
        com.android.wallpapercropper \
        com.android.wallpaperbackup; do
        pm disable-user --user 0 "$package" >/dev/null 2>&1 || true
    done

    # Hexin closes its trading session from the Activity background lifecycle.
    # Collectors are entirely native-HTTP driven and can be backgrounded; the
    # trade instance must stay resumed until that App behaviour is hooked out.
    if [ "$THS_MODE" = collector ]; then
        am start -a android.intent.action.MAIN \
            -c android.intent.category.HOME >/dev/null 2>&1 || return 1
    fi
}

if [ -n "$THS_ANDROID_ID" ]; then
    android_id="$THS_ANDROID_ID"
else
    android_id=$(printf '%s' "$THS_INSTANCE_ID" | sha256sum | cut -c1-16)
fi
case "$android_id" in
    *[!0-9a-fA-F]*|'') echo "invalid THS_ANDROID_ID"; exit 1 ;;
esac
[ "${#android_id}" -eq 16 ] || { echo "THS_ANDROID_ID must contain 16 hex chars"; exit 1; }

ensure_riru_injected || exit 1

settings put global captive_portal_mode 0
settings put global private_dns_mode off
settings put secure android_id "$android_id"
settings put global window_animation_scale 0
settings put global transition_animation_scale 0
settings put global animator_duration_scale 0

json_status app_artifacts "installing and verifying immutable APK artifacts"
install_apk_artifact com.hexin.plat.android "$THS_APK" THS || exit 1
install_apk_artifact com.yuyang.thshook "$HOOK_APK" Hook || exit 1
scope_result=0
configure_lsposed_scope || scope_result=$?
[ "$scope_result" -eq 0 ] || { [ "$scope_result" -eq 2 ] && exit 0; exit 1; }
if [ "$THS_MODE" = trade ]; then
    # 密码登录责任链首个 q9m 会请求 READ_PHONE_STATE。无头 redroid 没有
    # 权限弹窗交互，未预授予时回调永远不执行，表现为登录固定超时。
    # 这是 App 清单中声明的运行时权限，容器启动时确定性初始化。
    pm grant com.hexin.plat.android android.permission.READ_PHONE_STATE \
        || { json_status trade_permission_failed "READ_PHONE_STATE grant failed"; exit 1; }
    appops set com.hexin.plat.android READ_PHONE_STATE allow \
        || { json_status trade_permission_failed "READ_PHONE_STATE app-op failed"; exit 1; }
fi
json_status app_starting "starting THS explicitly"
start_ths_app || { json_status app_start_failed "THS process did not start"; exit 1; }
wait_http || { json_status hook_unavailable "HTTP hook did not start"; exit 1; }

json_status app_onboarding "completing deterministic THS onboarding"
complete_app_onboarding \
    || { json_status app_onboarding_failed "THS onboarding did not reach Hurricane-ready home"; exit 1; }

# 模式门禁先配置，确保 collector 永远不能调用交易接口。
if [ "$THS_MODE" = trade ]; then role=true; else role=false; fi
http POST /stock/trade/role "{\"enabled\":$role}" >/dev/null || exit 1

json_status market_initializing "triggering native runtime ensure"
http POST /native/runtime/ensure '{}' >/dev/null || true
runtime_attempt=1
runtime_ready=false
runtime_status=''
while [ "$runtime_attempt" -le "$THS_READY_MAX_ATTEMPTS" ]; do
    runtime_status=$(http GET /native/runtime/status 2>/dev/null || true)
    if printf '%s' "$runtime_status" | grep -q '"runtime_ready":true'; then
        runtime_ready=true
        break
    fi
    if printf '%s' "$runtime_status" | grep -q '"state":"failed"'; then
        http POST /native/runtime/ensure '{}' >/dev/null || true
    fi
    runtime_attempt=$((runtime_attempt + 1))
    sleep 1
done
[ "$runtime_ready" = true ] \
    || { json_status market_runtime_failed "active native initialization failed"; exit 1; }

json_status hurricane_verifying "verifying page-backed Hurricane runtime"
hurricane_attempt=1
hurricane_ready=false
while [ "$hurricane_attempt" -le 5 ]; do
    if verify_hurricane_runtime; then
        hurricane_ready=true
        break
    fi
    hurricane_attempt=$((hurricane_attempt + 1))
    sleep 2
done
[ "$hurricane_ready" = true ] \
    || { json_status hurricane_runtime_failed "THS onboarding/Hurricane initialization failed"; exit 1; }

if [ "$THS_MODE" = trade ]; then
    case "$THS_TRADE_PASSWORD_LEVELS" in
        1|2) ;;
        *) json_status trade_config_invalid "password levels must be 1 or 2"; exit 1 ;;
    esac
    if [ "$THS_TRADE_INIT" != existing ]; then
        [ -s "$SECRETS/trade_account_seed" ] || { json_status trade_secret_missing "account seed is required"; exit 1; }
        http POST /stock/trade/account/seed "$(cat "$SECRETS/trade_account_seed")" | grep -q '"ok":true' \
            || { json_status trade_seed_failed "account seed rejected"; exit 1; }
        if [ -s "$SECRETS/trade_token" ]; then
            http POST /stock/trade/token/import "$(cat "$SECRETS/trade_token")" >/dev/null || true
        fi
    fi
    # existing 只表示复用 App 数据卷，不表示 token 永远有效。交易密码是每次
    # 冷启动的恢复凭据，必须始终从只读 Docker secret 重新注入。
    if [ -s "$SECRETS/trade_password" ]; then
        password=$(sed 's/\\/\\\\/g; s/"/\\"/g' "$SECRETS/trade_password" | tr -d '\r\n')
        http POST /stock/trade/pwd "{\"password\":\"$password\"}" | grep -q '"ok":true' \
            || { json_status trade_password_failed "password rejected"; exit 1; }
    fi
    json_status trade_channel_starting "starting CommunicationService and CBAS socket pool"
    initialize_trade_channel \
        || { json_status trade_channel_failed "CommunicationService/CBAS initialization failed"; exit 1; }
    sleep 2
    json_status trade_warming "ensuring read-only trading runtime"
    http POST /stock/trade/runtime/ensure '{}' >/dev/null || true
    trade_attempt=1
    trade_ready=false
    while [ "$trade_attempt" -le "$THS_READY_MAX_ATTEMPTS" ]; do
        trade_status=$(http GET /stock/trade/runtime/status 2>/dev/null || true)
        if printf '%s' "$trade_status" | grep -q '"write_ready":true'; then
            trade_ready=true
            break
        fi
        if printf '%s' "$trade_status" | grep -Eq '"ensure_state":"(FAILED|NOT_RUN)"'; then
            http POST /stock/trade/runtime/ensure '{}' >/dev/null || true
        fi
        trade_attempt=$((trade_attempt + 1))
        sleep "$THS_READY_RETRY_SECONDS"
    done
    [ "$trade_ready" = true ] \
        || { json_status trade_not_ready "trade runtime is not write_ready"; exit 1; }
fi

printf '{"ready":true,"mode":"%s","android_id":"%s","bootstrap_strategy":"%s","runtime_poll_attempts":%s,"native_runtime":%s}\n' \
    "$THS_MODE" "$android_id" "$BOOTSTRAP_STRATEGY" "$runtime_attempt" "$runtime_status" > "$STATUS"
touch "$READY"
echo "$(date '+%F %T') THS instance ready mode=$THS_MODE"

enter_headless_runtime \
    && echo "$(date '+%F %T') headless runtime enabled" \
    || echo "$(date '+%F %T') failed to enter headless runtime"

# 常驻守护。只做无破坏性的 am start + 重预热，禁止 force-stop 和 swipe。
while sleep 30; do
    healthy=true
    pidof com.hexin.plat.android >/dev/null || healthy=false
    runtime_status=$(http GET /native/runtime/status 2>/dev/null || true)
    printf '%s' "$runtime_status" | grep -q '"runtime_ready":true' || healthy=false
    if [ "$THS_MODE" = trade ]; then
        trade_status=$(http GET /stock/trade/runtime/status 2>/dev/null || true)
        printf '%s' "$trade_status" | grep -q '"write_ready":true' || healthy=false
    fi
    if [ "$healthy" != true ]; then
        echo "$(date '+%F %T') readiness lost; starting recovery"
        rm -f "$READY"
        start_ths_app || true
        exec "$0"
    fi
done
