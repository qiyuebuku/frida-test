#!/usr/bin/env python3
from pathlib import Path

from androguard.core.analysis.analysis import Analysis
from androguard.core.dex import DEX
from androguard.decompiler.decompiler import DecompilerDAD


target_name = "Lcom/myhexin/android/biz_securities_table_card/dsl/SecurityTableViewModel;"
dexes = []
analysis = Analysis()
target_dex = None
target_class = None
for path in sorted(Path("/home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex").glob("classes*.dex")):
    dex = DEX(path.read_bytes().replace(b"\r\n", b"\n"))
    dexes.append(dex)
    analysis.add(dex)
    found = dex.get_class(target_name)
    if found is not None:
        target_dex = dex
        target_class = found
analysis.create_xref()
if target_dex is None or target_class is None:
    raise SystemExit("target class not found")
decompiler = DecompilerDAD(target_dex, analysis)
for method in target_class.get_methods():
    if method.get_name() in {"W0", "i1", "j1", "f1", "m1", "V0", "A1", "S0"}:
        print("\n=====", method.get_name(), method.get_descriptor(), "=====")
        print(decompiler.get_source_method(method))
