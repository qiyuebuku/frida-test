#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DESTINATION=${1:?usage: $0 DESTINATION}
: "${ARTIFACT_SSH_KEY:?path to the read-only artifact repository deploy key is required}"
ARTIFACT_COMMIT=0cb78e3f71fb4a9ffcb82a169a3af1e86310224f

install -d -m 0700 "$DESTINATION"
checkout=$(mktemp -d)
trap 'rm -rf -- "$checkout"' EXIT
export GIT_SSH_COMMAND="ssh -i $ARTIFACT_SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
git clone --filter=blob:none --no-checkout \
    git@github.com:qiyuebuku/smart-fund-deploy-artifacts.git "$checkout"
git -C "$checkout" checkout --detach "$ARTIFACT_COMMIT"
"$checkout/materialize.sh" "$DESTINATION"
"$SCRIPT_DIR/verify-artifacts.sh" "$DESTINATION"
