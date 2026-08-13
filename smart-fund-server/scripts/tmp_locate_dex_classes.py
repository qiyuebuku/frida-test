#!/usr/bin/env python3
from pathlib import Path

from loguru import logger

logger.remove()

from androguard.core.dex import DEX  # noqa: E402


root = Path("/home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex")
targets = {
    "Lfqu;", "Lhzu;", "Lmzu;", "LivU;", "Livu;", "Lryu;", "Lymu;",
    "Lenu;", "Lmqu;", "Liqu;",
}
for source in sorted(root.glob("*.dex")):
    raw = source.read_bytes().replace(b"\r\n", b"\n")
    vm = DEX(raw)
    names = {item.get_name() for item in vm.get_classes()}
    found = sorted(targets & names)
    if found:
        print(source.name, *found)
