#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
sync_dir "${SOURCE_QA}" "${REPO_ROOT}/agents/qa-agent" "qa-agent"
write_manifest

