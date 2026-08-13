#!/usr/bin/env python3
from pathlib import Path

from androguard.misc import AnalyzeDex


source_root = Path("/home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex")
normalized_root = Path("/tmp/ths-runtime-dex-normalized")
normalized_root.mkdir(parents=True, exist_ok=True)

for source_path in sorted(source_root.glob("*.dex")):
    dex_path = normalized_root / source_path.name
    dex_path.write_bytes(source_path.read_bytes().replace(b"\r\n", b"\n"))
    print(f"DEX {dex_path.name}")
    _, vm, analysis = AnalyzeDex(str(dex_path))
    target = vm.get_class("Luzu;")
    if target is None:
        continue
    print(f"FOUND {target.get_name()}")
    for method in target.get_methods():
        print(
            "METHOD",
            method.get_name(),
            method.get_descriptor(),
            method.get_access_flags_string(),
        )
        if method.get_name() in {"a", "H", "a0", "b", "c", "d", "e", "f", "g"}:
            method_analysis = analysis.get_method(method)
            if method_analysis is not None:
                print(method_analysis.get_method().get_source())
