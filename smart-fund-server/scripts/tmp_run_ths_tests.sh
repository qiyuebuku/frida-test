#!/usr/bin/env bash
set -euo pipefail

cd /home/yuyang/frida-test/smart-fund-server
/home/yuyang/anaconda3/envs/frida-test/bin/python -m pytest -q \
  tests/unit/test_ths_sector_pipeline.py \
  tests/unit/collection/test_collection_schedules.py \
  tests/unit/test_ths_bridge_routing.py \
  tests/unit/test_ths_native_stream.py

cd /home/yuyang/frida-test/ths
/home/yuyang/anaconda3/envs/frida-test/bin/python -m pytest -q \
  deployment/android-emulator/test_ths_native_proxy.py
