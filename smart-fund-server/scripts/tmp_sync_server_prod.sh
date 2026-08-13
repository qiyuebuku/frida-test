#!/usr/bin/env bash
set -euo pipefail

cd /home/yuyang/frida-test/smart-fund-server

# Do not source deploy_113.sh through process substitution. Its BASH_SOURCE
# would resolve to /dev/fd/* and make LOCAL_SERVER_DIR point at /dev.
bash deployment/deploy_113.sh --sync-only
bash deployment/deploy_113.sh --restart
