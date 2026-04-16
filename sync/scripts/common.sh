#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# shellcheck source=/dev/null
source "${REPO_ROOT}/sync/sources.conf"

EXCLUDES=(
  "--exclude=.git/"
  "--exclude=.venv/"
  "--exclude=node_modules/"
  "--exclude=__pycache__/"
  "--exclude=.pytest_cache/"
  "--exclude=dist/"
  "--exclude=build/"
  "--exclude=artifacts/"
  "--exclude=data/"
  "--exclude=.DS_Store"
)

sync_dir() {
  local source_dir="$1"
  local target_dir="$2"
  local label="$3"

  if [ ! -d "${source_dir}" ]; then
    echo "❌ source not found for ${label}: ${source_dir}"
    exit 1
  fi

  mkdir -p "${target_dir}"
  rsync -a --delete "${EXCLUDES[@]}" "${source_dir}/" "${target_dir}/"
  echo "✅ synced ${label}"
}

write_manifest() {
  local timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  cat >"${REPO_ROOT}/sync/manifests/latest-sync.json" <<EOF
{
  "synced_at_utc": "${timestamp}",
  "sources": {
    "query_generator_agent": "${SOURCE_QUERY_GENERATOR}",
    "testing_agent": "${SOURCE_TESTING}",
    "repair_agent": "${SOURCE_REPAIR}",
    "knowledge_agent": "${SOURCE_KNOWLEDGE}",
    "qa_agent": "${SOURCE_QA}",
    "runtime_console": "${SOURCE_CONSOLE}",
    "contracts": "${SOURCE_CONTRACTS}"
  },
  "targets": {
    "query_generator_agent": "agents/query-generator-agent",
    "testing_agent": "agents/testing-agent",
    "repair_agent": "agents/repair-agent",
    "knowledge_agent": "agents/knowledge-agent",
    "qa_agent": "agents/qa-agent",
    "runtime_console": "console/runtime-console",
    "contracts": "contracts"
  }
}
EOF
}

