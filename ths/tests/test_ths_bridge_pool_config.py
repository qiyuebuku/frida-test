from pathlib import Path


DEPLOYMENT_DIR = Path(__file__).resolve().parents[1] / "deployment"
INTERNAL_DIR = DEPLOYMENT_DIR / "internal"


def _read_env(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (INTERNAL_DIR / "config" / name).read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def _hook_port_for_android_user(user_id: int) -> int:
    if user_id <= 0:
        return 18900
    if user_id >= 10:
        return 18910 + ((user_id - 10) * 10)
    return 18900 + user_id


def test_every_isolated_pool_forwards_to_its_users_hook_port() -> None:
    env_files = sorted((INTERNAL_DIR / "config").glob("bridge-*.env"))
    configured_ports: set[int] = set()

    for env_file in env_files:
        values = _read_env(env_file.name)
        if "THS_ANDROID_USER_ID" not in values:
            continue
        user_id = int(values["THS_ANDROID_USER_ID"])
        device_port = int(values["THS_DEVICE_PORT"])
        assert device_port == _hook_port_for_android_user(user_id), env_file.name
        assert device_port not in configured_ports, env_file.name
        configured_ports.add(device_port)


def test_production_collectors_use_users_10_through_17_only() -> None:
    env_files = sorted((INTERNAL_DIR / "config").glob("bridge-*.env"))
    configured_users = {
        int(values["THS_ANDROID_USER_ID"])
        for env_file in env_files
        if (values := _read_env(env_file.name)).get("THS_ANDROID_USER_ID")
    }

    assert configured_users == set(range(10, 18))


def test_collector_units_target_5556_and_docker_gateway_excludes_trade() -> None:
    template = (INTERNAL_DIR / "systemd" / "collector-bridge@.service").read_text(
        encoding="utf-8"
    )
    gateway_entrypoint = (
        DEPLOYMENT_DIR / "redroid" / "gateway" / "gateway-entrypoint.sh"
    ).read_text(
        encoding="utf-8"
    )

    assert "THS_ANDROID_SERIAL=emulator-5556" in template
    for collector_index in range(1, 9):
        port = 49609 + collector_index
        assert f"--backend collector{collector_index}=127.0.0.1:{port}" in gateway_entrypoint
    assert "--backend trade=" not in gateway_entrypoint
    assert "49600" not in gateway_entrypoint
    assert not (INTERNAL_DIR / "systemd" / "app-load-balancer.service").exists()


def test_trade_bridge_is_managed_separately_and_write_ready_is_gated() -> None:
    trade_unit = (INTERNAL_DIR / "systemd" / "trade-bridge.service").read_text(
        encoding="utf-8"
    )
    installer = (INTERNAL_DIR / "remote" / "install-services.sh").read_text(
        encoding="utf-8"
    )

    assert "THS_ANDROID_USER_ID=0" in trade_unit
    assert "THS_HOST_PORT=49500" in trade_unit
    assert "THS_DEVICE_PORT=18900" in trade_unit
    assert installer.count("ensure_trade_runtime") >= 3
    assert "\"write_ready\":true" in installer


def test_emulator_boot_runs_repository_managed_bluetooth_optimization() -> None:
    emulator_unit = (INTERNAL_DIR / "systemd" / "android-emulator.service").read_text(
        encoding="utf-8"
    )
    installer = (INTERNAL_DIR / "remote" / "install-services.sh").read_text(
        encoding="utf-8"
    )
    bluetooth_script = (INTERNAL_DIR / "runtime" / "disable-bluetooth.sh").read_text(
        encoding="utf-8"
    )

    assert "ExecStartPost=/home/yuyangruan/android-runtime/bin/ths-disable-bluetooth.sh" in emulator_unit
    assert '"${RUNTIME_SOURCE}/disable-bluetooth.sh"' in installer
    assert '"${RUNTIME_DIR}/bin/ths-disable-bluetooth.sh"' in installer
    assert 'ADB_SERIAL="${ADB_SERIAL:-emulator-5556}"' in bluetooth_script
    assert "cmd bluetooth_manager disable" in bluetooth_script
    assert "pm disable-user --user" in bluetooth_script


def test_production_deployment_finishes_with_display_off() -> None:
    installer = (INTERNAL_DIR / "remote" / "install-services.sh").read_text(
        encoding="utf-8"
    )
    screen_off = (INTERNAL_DIR / "runtime" / "screen-off.sh").read_text(
        encoding="utf-8"
    )

    assert '"${RUNTIME_SOURCE}/screen-off.sh"' in installer
    assert '"${RUNTIME_DIR}/bin/ths-screen-off.sh"' in installer
    assert installer.rstrip().endswith('"${RUNTIME_DIR}/bin/ths-screen-off.sh"')
    assert 'ADB_SERIAL="${ADB_SERIAL:-emulator-5556}"' in screen_off
    assert 'if [[ "${current_user}" != "0" ]]' in screen_off
    assert "am switch-user 0" in screen_off
    assert "KEYCODE_SLEEP" in screen_off
    assert "Display Power: state=OFF" in screen_off


def test_fresh_machine_deployment_requires_immutable_artifacts() -> None:
    deployment = (INTERNAL_DIR / "remote" / "provision-host.sh").read_text(
        encoding="utf-8"
    )
    workflow = (
        DEPLOYMENT_DIR.parents[1] / ".github/workflows/ths-android-production.yml"
    ).read_text(encoding="utf-8")

    for variable in (
        "THS_ANDROID_SDK_ARCHIVE_SHA256",
        "THS_AVD_ARCHIVE_SHA256",
        "THS_APP_APK_SHA256",
        "THS_HOOK_APK_SHA256",
        "THS_COLLECTOR_TEMPLATE_SHA256",
    ):
        assert variable in deployment
    assert "sha256sum -c" in deployment
    assert "provision-collectors.sh" in deployment
    assert "artifact-build.conf" in deployment
    assert "./deploy.sh production" in workflow
    assert "rsync -az" not in workflow


def test_collector_template_cannot_be_exported_from_trade_user() -> None:
    exporter = (INTERNAL_DIR / "tools" / "export-collector-template.sh").read_text(
        encoding="utf-8"
    )
    provisioner = (INTERNAL_DIR / "remote" / "provision-collectors.sh").read_text(
        encoding="utf-8"
    )

    assert "SOURCE_USER_ID >= 10" in exporter
    assert "thshook_trade_seed.json" in exporter
    assert "sp_weituo_login.xml" in exporter
    assert "for user_id in {10..17}" in provisioner
    assert "restorecon -RF" in provisioner
