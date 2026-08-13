#!/usr/bin/env python3
from pathlib import Path

from androguard.core.analysis.analysis import Analysis
from androguard.core.dex import DEX


analysis = Analysis()
for path in sorted(Path("/home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex").glob("classes*.dex")):
    data = path.read_bytes().replace(b"\r\n", b"\n")
    dex = DEX(data)
    analysis.add(dex)
analysis.create_xref()

target = analysis.get_class_analysis("Lryu;")
if target is None:
    raise SystemExit("Lryu not found")

for method_analysis in target.get_methods():
    method = method_analysis.get_method()
    if method.get_name() not in {"H", "a0", "b0", "request", "w", "M", "f0"}:
        continue
    print(f"TARGET {method.get_name()}{method.get_descriptor()}")
    for caller_class, caller_method, offset in sorted(
        method_analysis.get_xref_from(),
        key=lambda item: (item[0].name, item[1].name, item[2]),
    ):
        print(
            "  FROM",
            caller_class.name,
            caller_method.name,
            caller_method.descriptor,
            hex(offset),
        )
