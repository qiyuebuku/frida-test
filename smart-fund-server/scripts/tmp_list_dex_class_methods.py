#!/usr/bin/env python3
import os
from pathlib import Path

from androguard.core.dex import DEX


target = os.environ.get(
    "DEX_TARGET",
    "Lcom/hexin/android/biz_quote_base_api/Security;",
)
for path in sorted(Path("/home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex").glob("classes*.dex")):
    dex = DEX(path.read_bytes().replace(b"\r\n", b"\n"))
    found = dex.get_class(target)
    if found is None:
        continue
    print(path)
    for method in found.get_methods():
        print(method.get_name(), method.get_descriptor())
    break
else:
    raise SystemExit("target class not found")
