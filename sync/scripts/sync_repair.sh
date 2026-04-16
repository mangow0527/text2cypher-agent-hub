#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
sync_dir "${SOURCE_REPAIR}" "${REPO_ROOT}/agents/repair-agent" "repair-agent"
write_manifest

