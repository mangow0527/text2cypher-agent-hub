#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
sync_dir "${SOURCE_QUERY_GENERATOR}" "${REPO_ROOT}/agents/query-generator-agent" "query-generator-agent"
write_manifest

