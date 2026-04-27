#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/common.sh"
sync_dir "${SOURCE_CYPHER_GENERATOR}" "${REPO_ROOT}/agents/cypher-generator-agent" "cypher-generator-agent"
write_manifest
