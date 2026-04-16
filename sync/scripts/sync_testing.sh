#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
sync_dir "${SOURCE_TESTING}" "${REPO_ROOT}/agents/testing-agent" "testing-agent"
write_manifest

