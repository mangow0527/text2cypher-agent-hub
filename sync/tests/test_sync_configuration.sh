#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKFLOW_FILE="${REPO_ROOT}/.github/workflows/sync-latest.yml"
SOURCES_FILE="${REPO_ROOT}/sync/sources.conf"

assert_file_contains() {
  local file="$1"
  local expected="$2"

  if ! grep -Fq "${expected}" "${file}"; then
    echo "Expected ${file} to contain: ${expected}" >&2
    exit 1
  fi
}

if [ ! -f "${WORKFLOW_FILE}" ]; then
  echo "Expected workflow file to exist: ${WORKFLOW_FILE}" >&2
  exit 1
fi

assert_file_contains "${WORKFLOW_FILE}" "workflow_dispatch:"
assert_file_contains "${WORKFLOW_FILE}" "cron: \"0 */6 * * *\""
assert_file_contains "${WORKFLOW_FILE}" "repository: mangow0527/NL2Cypher"
assert_file_contains "${WORKFLOW_FILE}" "repository: KG-AT-HOME/knowledge-agent"
assert_file_contains "${WORKFLOW_FILE}" "repository: KG-AT-HOME/qa-agent"
assert_file_contains "${WORKFLOW_FILE}" "SOURCE_REPO_TOKEN secret is required"
assert_file_contains "${WORKFLOW_FILE}" 'token: ${{ secrets.SOURCE_REPO_TOKEN }}'
assert_file_contains "${WORKFLOW_FILE}" "SOURCE_ROOT_NL2CYPHER:"
assert_file_contains "${WORKFLOW_FILE}" "SOURCE_KNOWLEDGE:"
assert_file_contains "${WORKFLOW_FILE}" "SOURCE_QA:"
assert_file_contains "${WORKFLOW_FILE}" "./sync/scripts/sync_all.sh"
assert_file_contains "${WORKFLOW_FILE}" "SYNC_PATHS=("
assert_file_contains "${WORKFLOW_FILE}" "agents/knowledge-agent"
assert_file_contains "${WORKFLOW_FILE}" "agents/qa-agent"
assert_file_contains "${WORKFLOW_FILE}" 'git status --porcelain -- "${SYNC_PATHS[@]}"'
assert_file_contains "${WORKFLOW_FILE}" "git push"

if grep -Fq 'github.token' "${WORKFLOW_FILE}"; then
  echo "Expected ${WORKFLOW_FILE} not to fall back to github.token for source repository checkout" >&2
  exit 1
fi

assert_file_contains "${SOURCES_FILE}" 'SOURCE_ROOT_NL2CYPHER="${SOURCE_ROOT_NL2CYPHER:-/Users/mangowmac/Desktop/code/NL2Cypher}"'
assert_file_contains "${SOURCES_FILE}" 'SOURCE_KNOWLEDGE="${SOURCE_KNOWLEDGE:-'
assert_file_contains "${SOURCES_FILE}" 'SOURCE_QA="${SOURCE_QA:-'

echo "sync configuration checks passed"
