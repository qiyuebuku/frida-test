#!/usr/bin/env bash
set -euo pipefail
cd /home/yuyang/frida-test/smart-fund-server
set -a
source deployment/.deployment.local.env
set +a
source <(sed '/^main "$@"$/d' deployment/deploy_113.sh)
setup_ssh_key
ssh_cmd "ls -lh /tmp/ths-production-base.apk; unzip -t /tmp/ths-production-base.apk >/dev/null && echo apk_ok; rm -rf /tmp/ths-dex && mkdir -p /tmp/ths-dex; unzip -q /tmp/ths-production-base.apk 'classes*.dex' -d /tmp/ths-dex; for f in /tmp/ths-dex/classes*.dex; do if grep -a -q 'Luzu;' \"\$f\"; then echo FOUND=\$f; ls -lh \"\$f\"; fi; done"
