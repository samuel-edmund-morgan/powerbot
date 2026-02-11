#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${1:-powerbot}"
SINCE_WINDOW="${SINCE_WINDOW:-90s}"

# "Bad" patterns that should fail the deploy if found in recent logs.
BAD_PATTERNS='Traceback|SyntaxError|CRITICAL|ERROR:|Exception'

# Known noisy/acceptable patterns to ignore in the gate.
WHITELIST_PATTERNS=(
  'WARNING:alerts:ukrainealarm: 401 Unauthorized'
  'INFO:aiohttp.access:.*"POST /api/v1/heartbeat HTTP/1.1" 200'
)

echo "Log health gate: docker compose logs --since ${SINCE_WINDOW} ${SERVICE_NAME}"
RAW_LOGS="$(docker compose logs --since "${SINCE_WINDOW}" "${SERVICE_NAME}" 2>&1 || true)"

if [[ -z "${RAW_LOGS}" ]]; then
  echo "No recent logs captured; skipping log gate."
  exit 0
fi

FILTERED_LOGS="${RAW_LOGS}"

has_rg=0
if command -v rg >/dev/null 2>&1; then
  has_rg=1
fi

for pattern in "${WHITELIST_PATTERNS[@]}"; do
  if [[ "${has_rg}" == "1" ]]; then
    FILTERED_LOGS="$(printf '%s\n' "${FILTERED_LOGS}" | rg -v "${pattern}" || true)"
  else
    FILTERED_LOGS="$(printf '%s\n' "${FILTERED_LOGS}" | grep -Ev "${pattern}" || true)"
  fi
done

if [[ "${has_rg}" == "1" ]]; then
  printf '%s\n' "${FILTERED_LOGS}" | rg -n "${BAD_PATTERNS}" >/tmp/powerbot_bad_log_lines.txt || true
else
  printf '%s\n' "${FILTERED_LOGS}" | grep -En "${BAD_PATTERNS}" >/tmp/powerbot_bad_log_lines.txt || true
fi

if [[ -s /tmp/powerbot_bad_log_lines.txt ]]; then
  echo "Log health gate FAILED. Suspicious lines:"
  cat /tmp/powerbot_bad_log_lines.txt
  exit 1
fi

echo "Log health gate passed."
