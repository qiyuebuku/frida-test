from pathlib import Path


DEPLOYMENT_DIR = (
    Path(__file__).resolve().parents[1] / "deployment" / "android-emulator"
)


def _read_env(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (DEPLOYMENT_DIR / name).read_text(encoding="utf-8").splitlines():
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
    env_files = sorted(DEPLOYMENT_DIR.glob("ths-bridge-*.env"))
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
