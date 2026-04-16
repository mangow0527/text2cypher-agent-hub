#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
sync_dir "${SOURCE_CONTRACTS}" "${REPO_ROOT}/contracts" "contracts"
write_manifest
