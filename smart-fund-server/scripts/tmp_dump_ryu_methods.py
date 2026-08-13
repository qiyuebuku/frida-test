#!/usr/bin/env python3
from pathlib import Path

from androguard.core.dex import DEX


path = Path("/home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex/classes6.dex")
dex = DEX(path.read_bytes().replace(b"\r\n", b"\n"))
target = dex.get_class("Lryu;")
if target is None:
    raise SystemExit("Lryu not found")

for method in target.get_methods():
    if method.get_name() not in {"a0", "M", "O", "C", "w", "request"}:
        continue
    print(f"METHOD {method.get_name()}{method.get_descriptor()}")
    code = method.get_code()
    if code is None:
        continue
    for instruction in code.get_bc().get_instructions():
        print(f"  {instruction.get_name():24} {instruction.get_output()}")
