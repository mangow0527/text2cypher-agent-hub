#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/common.sh"

sync_dir "${SOURCE_CYPHER_GENERATOR}" "${REPO_ROOT}/agents/cypher-generator-agent" "cypher-generator-agent"
sync_dir "${SOURCE_TESTING}" "${REPO_ROOT}/agents/testing-agent" "testing-agent"
sync_dir "${SOURCE_REPAIR}" "${REPO_ROOT}/agents/repair-agent" "repair-agent"
sync_dir_optional "${SOURCE_KNOWLEDGE}" "${REPO_ROOT}/agents/knowledge-agent" "knowledge-agent"
sync_dir_optional "${SOURCE_QA}" "${REPO_ROOT}/agents/qa-agent" "qa-agent"
sync_dir "${SOURCE_CONSOLE}" "${REPO_ROOT}/console/runtime-console" "runtime-console"
sync_dir "${SOURCE_CONTRACTS}" "${REPO_ROOT}/contracts" "contracts"

write_manifest
echo "🎉 full sync complete"
