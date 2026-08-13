#!/usr/bin/env python3
from pathlib import Path

from loguru import logger

logger.remove()

from androguard.misc import AnalyzeDex  # noqa: E402


targets_by_dex = {
    "classes4.dex": ("Lenu;", "Lfqu;"),
    "classes5.dex": ("Lhzu;", "Liqu;", "Livu;", "Lmqu;", "Lmzu;"),
    "classes6.dex": ("Lryu;",),
}
for dex_name, class_names in targets_by_dex.items():
    source = Path("/home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex") / dex_name
    normalized = Path("/tmp/ths-runtime-dex-normalized") / dex_name
    normalized.parent.mkdir(parents=True, exist_ok=True)
    normalized.write_bytes(source.read_bytes().replace(b"\r\n", b"\n"))
    _, vm, analysis = AnalyzeDex(str(normalized))
    for class_name in class_names:
        target = vm.get_class(class_name)
        if target is None:
            continue
        print(f"CLASS {class_name} superclass={target.get_superclassname()}")
        for field in target.get_fields():
            print("FIELD", field.get_name(), field.get_descriptor(), field.get_access_flags_string())
        for method in target.get_methods():
            print("METHOD", method.get_name(), method.get_descriptor(), method.get_access_flags_string())
            if method.get_name() in {
                "H", "a", "a0", "b", "b0", "c", "c0", "d", "d0", "e",
                "e0", "f", "f0", "g", "g0", "h", "i", "receive", "y", "A",
                "B", "C", "K", "L", "M", "O", "X", "Y", "request", "w",
            }:
                method_analysis = analysis.get_method(method)
                if method_analysis is not None:
                    print(method_analysis.get_method().get_source())
