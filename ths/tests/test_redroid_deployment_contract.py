from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REDROID = ROOT / "deployment" / "redroid"
WORKSPACE = ROOT.parent


def test_image_is_rebuilt_from_locked_artifacts_without_golden_data() -> None:
    dockerfile = (REDROID / "image" / "Dockerfile").read_text(encoding="utf-8")
    builder = (REDROID / "build-image.sh").read_text(encoding="utf-8")

    assert "redroid/redroid@sha256:" in dockerfile
    assert "ths.apk /opt/ths/ths.apk" in dockerfile
    assert "COPY --chmod=0644 ths-hook.apk /opt/ths/ths-hook.apk" in dockerfile
    assert "data-template" not in dockerfile
    assert "data-template" not in builder
    assert "--hook-apk" in builder
    assert '"$SCRIPT_DIR/verify-artifacts.sh"' in builder
    assert "BUILD_REVISION=$REVISION" in builder
    assert (
        "libriruloader-android11-x86_64.so\" \"$context/libriruloader.so"
        in builder
    )
    assert "libriruloader.so /system/lib64/libriruloader.so" in dockerfile


def test_runtime_manager_owns_app_start_and_active_readiness() -> None:
    manager = (REDROID / "image" / "ths-runtime-manager.sh").read_text(
        encoding="utf-8"
    )

    assert "install_apk_artifact" in manager
    assert "configure_lsposed_scope" in manager
    assert "am start -W -n com.hexin.plat.android/.LogoEmptyActivity" in manager
    assert "POST /native/runtime/ensure" in manager
    assert "POST /stock/trade/runtime/ensure" in manager
    assert '"write_ready":true' in manager
    assert "pm grant com.hexin.plat.android android.permission.READ_PHONE_STATE" in manager
    assert "appops set com.hexin.plat.android READ_PHONE_STATE allow" in manager
    assert "am force-stop" not in manager
    assert "input swipe" not in manager


def test_legacy_hook_signature_migration_never_uninstalls_ths() -> None:
    manager = (REDROID / "image" / "ths-runtime-manager.sh").read_text(
        encoding="utf-8"
    )

    migration = manager[manager.index("INSTALL_FAILED_UPDATE_INCOMPATIBLE") :]
    assert '"$package" = com.yuyang.thshook' in manager
    assert 'pm uninstall "$package"' in migration
    assert "Never apply this migration" in migration
    assert "pm uninstall com.hexin.plat.android" not in manager


def test_first_run_prefers_direct_business_bootstrap_with_observable_fallback() -> None:
    manager = (REDROID / "image" / "ths-runtime-manager.sh").read_text(
        encoding="utf-8"
    )
    source = (
        ROOT / "app/src/main/java/com/yuyang/thshook/MainHook.java"
    ).read_text(encoding="utf-8")

    direct_pos = manager.index("POST /admin/bootstrap")
    fallback_pos = manager.index("POST /admin/view-click")
    assert direct_pos < fallback_pos
    assert 'BOOTSTRAP_STRATEGY=direct' in manager
    assert 'BOOTSTRAP_STRATEGY=view_fallback' in manager
    assert '"bootstrap_strategy":"%s"' in manager
    assert 'requestLine.startsWith("POST /admin/bootstrap")' in source
    assert 'getDeclaredMethod("h")' in source
    assert 'getDeclaredMethod("goToHexin")' in source


def test_unified_absorbs_single_transport_timeout() -> None:
    source = (
        ROOT / "app/src/main/java/com/yuyang/thshook/MainHook.java"
    ).read_text(encoding="utf-8")

    assert "attempt <= 3" in source
    assert 'result.contains("\\\"success\\\":true")' in source
    assert "Thread.sleep(300L)" in source


def test_healthcheck_rejects_stale_ready_marker() -> None:
    healthcheck = (REDROID / "image" / "ths-healthcheck.sh").read_text(
        encoding="utf-8"
    )

    assert "/native/runtime/status" in healthcheck
    assert '"runtime_ready":true' in healthcheck
    assert "/stock/trade/runtime/status" in healthcheck
    assert '"write_ready":true' in healthcheck


def test_add_instance_waits_until_healthy() -> None:
    add_instance = (REDROID / "add-instance.sh").read_text(encoding="utf-8")

    assert "--ready-timeout" in add_instance
    assert "waiting for deterministic readiness" in add_instance
    assert '[[ "$state" == healthy ]]' in add_instance
    assert "failed to become healthy" in add_instance
    assert "--data-dir" in add_instance
    assert "only allowed for trade --trade-init existing migration" in add_instance
    assert "ro.dalvik.vm.native.bridge=libriruloader.so" in add_instance
    assert "androidboot.use_memfd=1" in add_instance


def test_compose_example_keeps_required_native_bridge_boot_arguments() -> None:
    compose = (REDROID / "compose.example.yml").read_text(encoding="utf-8")

    assert "ro.dalvik.vm.native.bridge=libriruloader.so" in compose
    assert "ro.enable.native.bridge.exec=1" in compose
    assert "androidboot.use_memfd=1" in compose


def test_gateway_is_docker_managed_with_host_backend_access() -> None:
    compose = (REDROID / "compose.gateway.yml").read_text(encoding="utf-8")
    entrypoint = (
        REDROID / "gateway" / "gateway-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "container_name: ths-gateway" in compose
    assert "network_mode: host" in compose
    assert "NET_ADMIN" in compose
    assert "restart: unless-stopped" in compose
    assert "/lb/status" in compose
    assert "iptables -t nat -C PREROUTING" in entrypoint
    assert "--backend collector1=127.0.0.1:49610" in entrypoint
    assert "--backend collector8=127.0.0.1:49617" in entrypoint

    installer = (
        ROOT / "deployment" / "internal" / "remote" / "install-services.sh"
    ).read_text(encoding="utf-8")
    assert 'compose.gateway.yml"' in installer
    assert "systemctl enable --now ths-app-load-balancer.service" not in installer


def test_legacy_data_adoption_is_explicit_and_trade_only() -> None:
    entrypoint = (REDROID / "image" / "docker-entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert '[ "${THS_MODE:-}" = trade ]' in entrypoint
    assert '[ "${THS_TRADE_INIT:-secrets}" = existing ]' in entrypoint
    assert "/data/data/com.hexin.plat.android" in entrypoint
    assert "/data/data/com.yuyang.thshook" in entrypoint


def test_new_instance_starts_without_cloned_application_data() -> None:
    entrypoint = (REDROID / "image" / "docker-entrypoint.sh").read_text(
        encoding="utf-8"
    )

    assert "NEW_INSTANCE=false" in entrypoint
    assert "NEW_INSTANCE=true" in entrypoint
    assert 'if [ "$NEW_INSTANCE" = true ]; then' in entrypoint
    assert "riru-lsposed-redroid-bootstrap.tar.gz" in entrypoint
    assert "data-template.tar.gz" not in entrypoint
    assert "hardwareinfo.dat.xml" not in entrypoint
    assert "/data/data/com.hexin.plat.android" in entrypoint  # legacy trade adoption only


def test_artifact_verifier_checks_size_and_digest() -> None:
    verifier = (REDROID / "verify-artifacts.sh").read_text(encoding="utf-8")
    lock = (REDROID / "artifacts.lock").read_text(encoding="utf-8")

    assert "stat -c '%s'" in verifier
    assert "sha256sum" in verifier
    assert "ths-11.58.03.apk|183278446|03f8d870" in lock
    assert "riru-lsposed-redroid-bootstrap.tar.gz" in lock
    assert (
        "libriruloader-android11-x86_64.so|11416|b803126d57148134"
        in lock
    )


def test_existing_trade_volume_still_reinjects_password_secret() -> None:
    manager = (REDROID / "image" / "ths-runtime-manager.sh").read_text(
        encoding="utf-8"
    )

    existing_end = manager.index(
        '# existing 只表示复用 App 数据卷，不表示 token 永远有效。'
    )
    password_injection = manager.index(
        'if [ -s "$SECRETS/trade_password" ]; then', existing_end
    )
    assert password_injection > existing_end


def test_trade_startup_initializes_official_channel_before_runtime_ensure() -> None:
    manager = (REDROID / "image" / "ths-runtime-manager.sh").read_text(
        encoding="utf-8"
    )

    service_pos = manager.index("hexin_connect_hangqing_flag_key")
    cbas_pos = manager.index("/stock/trade/cbas")
    password_pos = manager.index("POST /stock/trade/pwd")
    login_pos = manager.index("POST /stock/trade/login")
    ensure_pos = manager.index("/stock/trade/runtime/ensure")
    assert service_pos < ensure_pos
    assert cbas_pos < ensure_pos
    assert password_pos < login_pos < ensure_pos
    assert "'{\"method\":\"pwd\"}'" in manager


def test_production_workflow_builds_pushes_and_deploys_digest_only() -> None:
    workflow = (
        WORKSPACE / ".github/workflows/ths-redroid-production.yml"
    ).read_text(encoding="utf-8")
    deploy = (REDROID / "deploy-production.sh").read_text(encoding="utf-8")
    rollout = (REDROID / "rollout-production.sh").read_text(encoding="utf-8")

    assert "workflow_dispatch:" not in workflow
    assert "redroid-validate:" in workflow
    assert "needs: redroid-validate" in workflow
    assert "timeout-minutes: 180" in workflow
    assert "'smart-fund-production'" in workflow
    assert "github.workflow, github.ref" in workflow
    assert "github.event_name == 'push'" in workflow
    assert "refs/heads/main" in workflow
    assert "runs-on: [self-hosted, Linux, X64, production-redroid]" in workflow
    assert "DEPLOY_ARTIFACTS_SSH_KEY" in workflow
    assert "ARTIFACT_KNOWN_HOSTS=/tmp/ths-github-known-hosts" in workflow
    assert "> ~/.ssh/known_hosts" not in workflow
    assert "0608fd9b25c75f9bf1d18f36fc3ce87f002b087a" in (
        REDROID / "fetch-private-artifacts.sh"
    ).read_text(encoding="utf-8")
    assert "docker push" in workflow
    assert "RepoDigests" in workflow
    assert "127.0.0.1:5000/ths-redroid:git-$GITHUB_SHA" in workflow
    assert '"$IMAGE_DIGEST" "$GITHUB_SHA" "$THS_REDROID_TARGETS"' in workflow
    assert "Synchronize protected trade credentials" in workflow
    for secret in (
        "THS_TRADE_ACCOUNT",
        "THS_TRADE_BROKER",
        "THS_TRADE_QSID",
        "THS_TRADE_PASSWORD",
    ):
        assert f"secrets.{secret}" in workflow
    assert 'temporary=$(mktemp "$secret_dir/.${file_name}.XXXXXX")' in workflow
    assert 'mv -f "$temporary" "$secret_dir/$file_name"' in workflow
    assert "THS_PRODUCTION_SSH_PRIVATE_KEY" not in workflow
    assert '"${GITHUB_ACTIONS:-}" == true' in deploy
    assert '"${GITHUB_SHA:-}" == "$REVISION"' in deploy
    assert 'tar -C "$WORKSPACE" -czf - ths/deployment/redroid' in deploy
    assert "git -C" not in deploy
    assert "docker save" not in deploy
    assert "'$IMAGE' '$REVISION'" in deploy
    assert "@sha256:[0-9a-f]{64}" in rollout
    assert "127\\.0\\.0\\.1:5000/ths-redroid" in rollout
    assert '"${RUNNER_ENVIRONMENT:-}" == self-hosted' in rollout
    assert "org.opencontainers.image.revision" in rollout
    assert "for attempt in 1 2 3 4 5" in rollout
    assert "unable to pull immutable image after 5 attempts" in rollout


def test_private_registry_is_pinned_and_loopback_only() -> None:
    registry = (REDROID / "ensure-local-registry.sh").read_text(
        encoding="utf-8"
    )

    assert "registry@sha256:" in registry
    assert "REGISTRY_NAME=smart-fund-registry" in registry
    assert "REGISTRY_VOLUME=smart-fund-registry-data" in registry
    assert "expected_image_id" in registry
    assert "-p 127.0.0.1:5000:5000" in registry
    assert '"${RUNNER_ENVIRONMENT:-}" == self-hosted' in registry
    assert "http://127.0.0.1:5000/v2/" in registry


def test_empty_volume_declares_native_bridge_before_android_init() -> None:
    entrypoint = (REDROID / "image" / "docker-entrypoint.sh").read_text(
        encoding="utf-8"
    )

    marker = entrypoint.index("/data/adb/riru/native_bridge")
    android_init = entrypoint.rindex("exec /init")
    assert "libndk_translation.so" in entrypoint
    assert "chmod 0666 /data/adb/riru/native_bridge" in entrypoint
    assert marker < android_init


def test_empty_volume_allows_lsposed_first_boot_restart_to_settle() -> None:
    manager = (REDROID / "image" / "ths-runtime-manager.sh").read_text(
        encoding="utf-8"
    )

    gate = manager[manager.index("ensure_riru_injected()") :]
    assert "/system/lib64/libriruloader.so" in gate
    assert "b803126d57148134faff2d0e9fb9268fbeabf8efd770ed5d" in gate
    assert "riru_loader_invalid" in gate
    assert 'while [ "$attempt" -le 90 ]' in gate
    assert "did not enter zygote within 90 seconds" in gate


def test_custom_riru_compatibility_service_starts_before_zygote_can_load_bridge() -> None:
    boot = (REDROID / "image" / "bootanim.riru.rc").read_text(encoding="utf-8")
    start = (REDROID / "image" / "start-rirud.sh").read_text(
        encoding="utf-8"
    )

    setup = boot.index("--setup-sbin")
    post_fs_data = boot.index("--post-fs-data")
    start_service = boot.index("start ths-rirud")
    assert setup < start_service < post_fs_data
    assert boot.count("start ths-rirud") == 1
    assert "service ths-rirud" in boot
    assert "riru.Daemon" in start
    assert "--from-service" in start


def test_rollout_proves_empty_volume_before_touching_production() -> None:
    rollout = (REDROID / "rollout-production.sh").read_text(encoding="utf-8")

    canary = rollout.index("--name ths-rebuild-canary")
    collectors = rollout.index("for number in 1 2 3 4 5 6 7 8")
    trade = rollout.index("docker rm -f ths-trade")
    assert canary < collectors < trade
    assert "docker volume rm ths-rebuild-canary-data" in rollout
    assert 'old_image=$(docker inspect' in rollout
    assert 'old_trade_image=$(docker inspect' in rollout


def test_rollout_rebuilds_trade_data_and_reinjects_credentials() -> None:
    rollout = (REDROID / "rollout-production.sh").read_text(encoding="utf-8")

    assert "THS_TRADE_DATA_DIR" not in rollout
    assert "THS_TRADE_SECRET_DIR" in rollout
    assert "--trade-init existing" in rollout
    assert "--data-dir" not in rollout
    assert "docker volume rm ths-trade-data" in rollout
    assert '--account-secret "$secret_dir/trade_account"' in rollout
    assert '--broker-secret "$secret_dir/trade_broker"' in rollout
    assert '--qsid-secret "$secret_dir/trade_qsid"' in rollout
    assert '--password-secret "$secret_dir/trade_password"' in rollout
    assert "2>/dev/null || true" in rollout[rollout.index("old_trade_image=") :]
    assert 'if [[ -n "$old_trade_image" ]]' in rollout
    assert "no previous container image exists for rollback" in rollout
    assert "required trade disaster-recovery secret is missing" not in rollout


def test_trade_can_be_rebuilt_from_minimal_protected_credentials() -> None:
    source = (
        ROOT / "app/src/main/java/com/yuyang/thshook/MainHook.java"
    ).read_text(encoding="utf-8")
    manager = (REDROID / "image/ths-runtime-manager.sh").read_text(encoding="utf-8")
    add_instance = (REDROID / "add-instance.sh").read_text(encoding="utf-8")

    assert 'POST /stock/trade/account/configure' in source
    assert 'private static String handleTradeAccountConfigure' in source
    assert 'getMethod("W", String.class)' in source
    assert 'getMethod("K", String.class)' in source
    assert 'getMethod("X", String.class)' in source
    assert 'getMethod("V", cl.loadClass("a1s"))' in source
    assert 'getMethod("b", cl.loadClass("pzr"))' in source
    assert 'getMethod("x", cl.loadClass("pzr"))' in source
    assert "configured broker does not match qsid" in source
    assert "trade_account" in manager
    assert "trade_broker" in manager
    assert "trade_qsid" in manager
    configure_pos = manager.index("POST /stock/trade/account/configure")
    password_pos = manager.index("POST /stock/trade/pwd")
    login_pos = manager.index("POST /stock/trade/login")
    assert configure_pos < password_pos < login_pos
    assert manager.count("POST /stock/trade/login") == 1
    assert "--account-secret" in add_instance
    assert "--broker-secret" in add_instance
    assert "--qsid-secret" in add_instance


def test_redroid_rollout_can_target_trade_without_replacing_collectors() -> None:
    rollout = (REDROID / "rollout-production.sh").read_text(encoding="utf-8")

    assert '^(none|all|collectors|trade)$' in rollout
    assert '[[ "$TARGETS" == all || "$TARGETS" == collectors ]]' in rollout
    assert '[[ "$TARGETS" == all || "$TARGETS" == trade ]]' in rollout
