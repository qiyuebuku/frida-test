#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DESTINATION=${1:?usage: $0 DESTINATION}
: "${ARTIFACT_SSH_KEY:?path to the read-only artifact repository deploy key is required}"
: "${ARTIFACT_KNOWN_HOSTS:?path to the pinned GitHub known-hosts file is required}"
ARTIFACT_COMMIT=0608fd9b25c75f9bf1d18f36fc3ce87f002b087a

install -d -m 0700 "$DESTINATION"
checkout=$(mktemp -d)
trap 'rm -rf -- "$checkout"' EXIT
export GIT_SSH_COMMAND="ssh -i $ARTIFACT_SSH_KEY -p 443 -o IdentitiesOnly=yes -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$ARTIFACT_KNOWN_HOSTS"
git clone --filter=blob:none --no-checkout \
    ssh://git@ssh.github.com:443/qiyuebuku/smart-fund-deploy-artifacts.git "$checkout"
git -C "$checkout" checkout --detach "$ARTIFACT_COMMIT"
"$checkout/materialize.sh" "$DESTINATION"
"$SCRIPT_DIR/verify-artifacts.sh" "$DESTINATION"
