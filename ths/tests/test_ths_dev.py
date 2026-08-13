import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/ths_dev.py"
SPEC = importlib.util.spec_from_file_location("ths_dev", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ths_dev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ths_dev)


def test_root_shell_command_keeps_compound_command_in_one_adb_argument(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ADB", "/tmp/adb.exe")

    command = ths_dev._adb_su_shell_command(
        "device-1",
        "if test -f /data/file; then echo present; else echo absent; fi",
    )

    assert command[:5] == [
        "/tmp/adb.exe",
        "-s",
        "device-1",
        "shell",
        "su -c 'if test -f /data/file; then echo present; else echo absent; fi'",
    ]
