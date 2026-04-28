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
assert_file_contains "${WORKFLOW_FILE}" "mangow0527/NL2Cypher"
assert_file_contains "${WORKFLOW_FILE}" "KG-AT-HOME/knowledge-agent"
assert_file_contains "${WORKFLOW_FILE}" "KG-AT-HOME/qa-agent"
assert_file_contains "${WORKFLOW_FILE}" "Repository access token secrets are required"
assert_file_contains "${WORKFLOW_FILE}" "NL2CYPHER_REPO_TOKEN"
assert_file_contains "${WORKFLOW_FILE}" "KNOWLEDGE_REPO_TOKEN"
assert_file_contains "${WORKFLOW_FILE}" "QA_REPO_TOKEN"
assert_file_contains "${WORKFLOW_FILE}" "tr -d '\\r\\n'"
assert_file_contains "${WORKFLOW_FILE}" "value#token "
assert_file_contains "${WORKFLOW_FILE}" "value#Bearer "
assert_file_contains "${WORKFLOW_FILE}" "id: source_tokens"
assert_file_contains "${WORKFLOW_FILE}" "nl2cypher_token="
assert_file_contains "${WORKFLOW_FILE}" "knowledge_token="
assert_file_contains "${WORKFLOW_FILE}" "qa_token="
assert_file_contains "${WORKFLOW_FILE}" 'token: ${{ steps.source_tokens.outputs.nl2cypher_token }}'
assert_file_contains "${WORKFLOW_FILE}" 'token: ${{ steps.source_tokens.outputs.knowledge_token }}'
assert_file_contains "${WORKFLOW_FILE}" 'token: ${{ steps.source_tokens.outputs.qa_token }}'
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

if grep -Fq 'https://x-access-token:' "${WORKFLOW_FILE}" || grep -Fq 'extraheader=AUTHORIZATION: basic' "${WORKFLOW_FILE}" || grep -Fq 'token: ${{ secrets.SOURCE_REPO_TOKEN }}' "${WORKFLOW_FILE}" || grep -Fq 'token: ${{ env.SOURCE_REPO_TOKEN }}' "${WORKFLOW_FILE}"; then
  echo "Expected ${WORKFLOW_FILE} to use sanitized token outputs for source repository checkout" >&2
  exit 1
fi

assert_file_contains "${SOURCES_FILE}" 'SOURCE_ROOT_NL2CYPHER="${SOURCE_ROOT_NL2CYPHER:-/Users/mangowmac/Desktop/code/NL2Cypher}"'
assert_file_contains "${SOURCES_FILE}" 'SOURCE_KNOWLEDGE="${SOURCE_KNOWLEDGE:-'
assert_file_contains "${SOURCES_FILE}" 'SOURCE_QA="${SOURCE_QA:-'

echo "sync configuration checks passed"
