#!/usr/bin/env bash
set -euo pipefail
for file in /home/yuyang/frida-test/artifacts/reverse/ths-runtime-dex/classes*.dex; do
    if strings "${file}" | grep -i -m 10 -E 'SecurityTableViewModel|sif-quoter-dataapi-sector-statistics|AStockSector'; then
        echo "===${file}"
    fi
done
