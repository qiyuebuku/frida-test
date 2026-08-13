#!/usr/bin/env bash
set -euo pipefail

cd /home/yuyang/frida-test/smart-fund-server
set -a
source deployment/.deployment.local.env
set +a
source <(sed '/^main "$@"$/d' deployment/deploy_113.sh)
setup_ssh_key

ssh_cmd "/home/yuyangruan/anaconda3/envs/smart-fund/bin/python - <<'PY'
from pathlib import Path
path = Path('/home/yuyangruan/smart-fund/config/smart-fund-server.env')
text = path.read_text()
key = 'THS_NATIVE_SECTOR_MAX_CONCURRENCY'
lines = [line for line in text.splitlines() if not line.startswith(key + '=')]
lines.append(key + '=8')
path.write_text('\\n'.join(lines) + '\\n')
PY"
sudo_cmd "systemctl restart smart-fund-worker.service"
