#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERHUB_USER="${DOCKERHUB_USER:-semorgana}"
VERSION="${VERSION:-$(date +%Y.%m.%d-%H%M)}"
MIGRATE="${MIGRATE:-0}"
TEST_DIR="/opt/powerbot-test"
TESTERBOT_RUN_TIMEOUT_SEC="${TESTERBOT_RUN_TIMEOUT_SEC:-420}"

strip_quotes() {
  local value="${1:-}"
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  echo "$value"
}

get_env_value() {
  local key="$1"
  local file="$2"
  local raw
  raw="$(grep "^${key}=" "$file" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  strip_quotes "$raw"
}

env_flag_true() {
  local raw="${1:-}"
  case "${raw,,}" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

should_enable_business_profile() {
  local env_file="$1"
  local token
  token="$(get_env_value "BUSINESS_BOT_API_KEY" "$env_file")"
  [[ -n "$token" ]]
}

should_enable_admin_profile() {
  local env_file="$1"
  local token
  token="$(get_env_value "ADMIN_BOT_API_KEY" "$env_file")"
  [[ -n "$token" ]]
}

should_enable_testerbot() {
  local env_file="$1"
  env_flag_true "$(get_env_value "TESTERBOT_ENABLED" "$env_file")"
}

should_enable_adbot() {
  local env_file="$1"
  env_flag_true "$(get_env_value "ADBOT_ENABLED" "$env_file")"
}

should_enable_adbot_e2e() {
  local env_file="$1"
  env_flag_true "$(get_env_value "ADBOT_E2E_ENABLED" "$env_file")"
}

setup_docker_auth() {
  if [[ -n "${DOCKERHUB_USERNAME:-}" && -n "${DOCKERHUB_TOKEN:-}" ]]; then
    echo "Logging in to Docker Hub..."
    echo "${DOCKERHUB_TOKEN}" | docker login -u "${DOCKERHUB_USERNAME}" --password-stdin
    return 0
  fi

  if [[ -n "${DOCKER_CONFIG:-}" && -f "${DOCKER_CONFIG}/config.json" ]]; then
    echo "Using Docker config from ${DOCKER_CONFIG}"
    return 0
  fi

  home_dir="${HOME:-}"
  if [[ -n "${home_dir}" && -f "${home_dir}/.docker/config.json" ]]; then
    export DOCKER_CONFIG="${home_dir}/.docker"
    echo "Using Docker config from ${DOCKER_CONFIG}"
    return 0
  fi

  for candidate in "/home/ghactions/.docker" "/root/.docker" "/opt/actions-runner/.docker"; do
    if [[ -f "${candidate}/config.json" ]]; then
      export DOCKER_CONFIG="${candidate}"
      echo "Using Docker config from ${candidate}"
      return 0
    fi
  done

  echo "Warning: Docker Hub credentials not provided and no Docker config found. Push may fail."
}

setup_docker_auth

echo "Building powerbot image ${DOCKERHUB_USER}/powerbot:${VERSION}..."
docker build -t "${DOCKERHUB_USER}/powerbot:${VERSION}" -f "${REPO_DIR}/Dockerfile" "${REPO_DIR}"
docker tag "${DOCKERHUB_USER}/powerbot:${VERSION}" "${DOCKERHUB_USER}/powerbot:latest"
docker push "${DOCKERHUB_USER}/powerbot:${VERSION}"
docker push "${DOCKERHUB_USER}/powerbot:latest"

if [[ "${MIGRATE}" == "1" ]]; then
  echo "Building migrate image ${DOCKERHUB_USER}/powerbot-migrate:${VERSION}..."
  docker build -t "${DOCKERHUB_USER}/powerbot-migrate:${VERSION}" -f "${REPO_DIR}/Dockerfile.migrate" "${REPO_DIR}"
  docker tag "${DOCKERHUB_USER}/powerbot-migrate:${VERSION}" "${DOCKERHUB_USER}/powerbot-migrate:latest"
  docker push "${DOCKERHUB_USER}/powerbot-migrate:${VERSION}"
  docker push "${DOCKERHUB_USER}/powerbot-migrate:latest"
fi

echo "Sync docker-compose.yml to ${TEST_DIR}..."
install -m 0644 "${REPO_DIR}/docker-compose.yml" "${TEST_DIR}/docker-compose.yml"
sed -i -E 's/18081:8081/18082:8081/g' "${TEST_DIR}/docker-compose.yml"
if [[ -f "${REPO_DIR}/docker-compose.testerbot.yml" ]]; then
  install -m 0644 "${REPO_DIR}/docker-compose.testerbot.yml" "${TEST_DIR}/docker-compose.testerbot.yml"
fi

echo "Sync .env keys to ${TEST_DIR}..."
if [[ -f "${REPO_DIR}/.env.example" ]]; then
  touch "${TEST_DIR}/.env"
  while IFS= read -r line; do
    [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    key="${line%%=*}"
    if ! grep -q "^${key}=" "${TEST_DIR}/.env"; then
      echo "${line}" >> "${TEST_DIR}/.env"
    fi
  done < "${REPO_DIR}/.env.example"
fi

# Bootstrap Telethon target usernames/IDs from bot tokens where placeholders are still present.
echo "Bootstrapping Telethon targets from bot tokens (if needed)..."
python3 "${REPO_DIR}/scripts/bootstrap_telethon_targets.py" --env-file "${TEST_DIR}/.env"

# Bootstrap adbot source/internal chat IDs from Telethon dialogs by configured chat titles.
echo "Bootstrapping adbot chat IDs from Telethon dialogs (if needed)..."
if [[ -f "${REPO_DIR}/scripts/bootstrap_adbot_chat_ids.py" ]]; then
  docker run --rm \
    --env-file "${TEST_DIR}/.env" \
    -v "${REPO_DIR}/scripts/bootstrap_adbot_chat_ids.py:/tmp/bootstrap_adbot_chat_ids.py:ro" \
    -v "${TEST_DIR}/.env:/tmp/powerbot-test.env" \
    --entrypoint python "${DOCKERHUB_USER}/powerbot:${VERSION}" \
    /tmp/bootstrap_adbot_chat_ids.py --env-file /tmp/powerbot-test.env || true
fi

# Preflight: validate Telethon env contract before stack/bootstrap.
# Helps fail fast when testerbot/adbot E2E are enabled with placeholder values.
echo "Running Telethon env preflight..."
python3 "${REPO_DIR}/scripts/smoke_telethon_env_contract.py" --env-file "${TEST_DIR}/.env"

# Automated smoke: strict adbot E2E requires resident inline mode enabled.
echo "Running adbot strict-inline preflight smoke..."
python3 "${REPO_DIR}/scripts/smoke_adbot_inline_mode_preflight.py" --env-file "${TEST_DIR}/.env"

# Automated smoke: Telethon env preflight must enforce StringSession/chat-id contract.
echo "Running Telethon env contract-cases smoke test..."
python3 "${REPO_DIR}/scripts/smoke_telethon_env_contract_cases.py"

# Увімкнути ЯСНО-графіки лише для тестового бота
if grep -q "^YASNO_ENABLED=" "${TEST_DIR}/.env"; then
  sed -i -E 's/^YASNO_ENABLED=.*/YASNO_ENABLED=1/' "${TEST_DIR}/.env"
else
  echo "YASNO_ENABLED=1" >> "${TEST_DIR}/.env"
fi

# Увімкнути режим одного повідомлення лише для тестового бота
if grep -q "^SINGLE_MESSAGE_MODE=" "${TEST_DIR}/.env"; then
  sed -i -E 's/^SINGLE_MESSAGE_MODE=.*/SINGLE_MESSAGE_MODE=1/' "${TEST_DIR}/.env"
else
  echo "SINGLE_MESSAGE_MODE=1" >> "${TEST_DIR}/.env"
fi

# У тестовому середовищі business mode увімкнений за замовчуванням.
# Окремий businessbot сервіс піднімається лише якщо BUSINESS_BOT_API_KEY не порожній.
if grep -q "^BUSINESS_MODE=" "${TEST_DIR}/.env"; then
  sed -i -E 's/^BUSINESS_MODE=.*/BUSINESS_MODE=1/' "${TEST_DIR}/.env"
else
  echo "BUSINESS_MODE=1" >> "${TEST_DIR}/.env"
fi

# Для adbot E2E у test середовищі примусово вмикаємо префіксний E2E-режим,
# щоб запити з ADBOT_E2E_PROMPT_PREFIX не блокувались cooldown-ом.
# Це test-only поведінка (prod не зачіпається).
if should_enable_adbot_e2e "${TEST_DIR}/.env"; then
  strict_real_inline="$(get_env_value "ADBOT_E2E_STRICT_REAL_INLINE" "${TEST_DIR}/.env")"

  if grep -q "^ADBOT_ALLOW_SELF_OUTGOING_E2E=" "${TEST_DIR}/.env"; then
    sed -i -E 's/^ADBOT_ALLOW_SELF_OUTGOING_E2E=.*/ADBOT_ALLOW_SELF_OUTGOING_E2E=1/' "${TEST_DIR}/.env"
  else
    echo "ADBOT_ALLOW_SELF_OUTGOING_E2E=1" >> "${TEST_DIR}/.env"
  fi

  # Default test behavior is non-strict internal reply mode:
  # якщо resident inline тимчасово недоступний (наприклад BotInlineDisabled),
  # adbot використовує fallback-відповідь замість таймауту E2E.
  # For strict mode set ADBOT_E2E_STRICT_REAL_INLINE=1.
  if [[ "${strict_real_inline}" == "1" ]]; then
    if grep -q "^ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=" "${TEST_DIR}/.env"; then
      sed -i -E 's/^ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=.*/ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=1/' "${TEST_DIR}/.env"
    else
      echo "ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=1" >> "${TEST_DIR}/.env"
    fi
  else
    if grep -q "^ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=" "${TEST_DIR}/.env"; then
      sed -i -E 's/^ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=.*/ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=0/' "${TEST_DIR}/.env"
    else
      echo "ADBOT_INTERNAL_REQUIRE_REAL_BOT_REPLY=0" >> "${TEST_DIR}/.env"
    fi
  fi
fi

# У test за замовчуванням працюємо через mock-оплати (без реальних списань Stars).
# Якщо потрібно побачити реальний UI Telegram Stars у test, вистав в /opt/powerbot-test/.env:
#   BUSINESS_TEST_ALLOW_TELEGRAM_STARS=1
#   BUSINESS_PAYMENT_PROVIDER=telegram_stars
allow_test_stars="$(get_env_value "BUSINESS_TEST_ALLOW_TELEGRAM_STARS" "${TEST_DIR}/.env")"
if [[ "${allow_test_stars}" != "1" ]]; then
  if grep -q "^BUSINESS_PAYMENT_PROVIDER=" "${TEST_DIR}/.env"; then
    sed -i -E 's/^BUSINESS_PAYMENT_PROVIDER=.*/BUSINESS_PAYMENT_PROVIDER=mock/' "${TEST_DIR}/.env"
  else
    echo "BUSINESS_PAYMENT_PROVIDER=mock" >> "${TEST_DIR}/.env"
  fi
else
  current_provider="$(get_env_value "BUSINESS_PAYMENT_PROVIDER" "${TEST_DIR}/.env")"
  echo "BUSINESS_TEST_ALLOW_TELEGRAM_STARS=1; keeping BUSINESS_PAYMENT_PROVIDER=${current_provider:-<empty>}."
fi

cd "${TEST_DIR}"
docker compose down
if ! docker compose pull; then
  echo "Warning: docker compose pull failed; continuing with local images built in this run."
fi
if [[ "${MIGRATE}" == "1" ]]; then
  docker compose --profile migrate run --rm migrate
fi

profiles=()
if should_enable_business_profile "${TEST_DIR}/.env"; then
  echo "Business profile enabled (BUSINESS_BOT_API_KEY is set)."
  profiles+=(--profile business)
else
  echo "Business profile disabled (missing BUSINESS_BOT_API_KEY)."
fi
if should_enable_admin_profile "${TEST_DIR}/.env"; then
  echo "Admin profile enabled (ADMIN_BOT_API_KEY is set)."
  profiles+=(--profile admin)
else
  echo "Admin profile disabled (missing ADMIN_BOT_API_KEY)."
fi
if should_enable_adbot "${TEST_DIR}/.env"; then
  echo "Adbot profile enabled (ADBOT_ENABLED=1)."
  profiles+=(--profile adbot)
else
  echo "Adbot profile disabled (ADBOT_ENABLED!=1)."
fi
docker compose "${profiles[@]}" up -d

docker compose ps

echo "Health check (test)..."
health_ok=0
for i in {1..90}; do
  if curl -sf --max-time 2 http://127.0.0.1:18082/api/v1/health >/dev/null; then
    health_ok=1
    break
  fi
  sleep 1
done
if [[ "${health_ok}" != "1" ]]; then
  echo "Health check failed (test)."
  exit 1
fi

SENSOR_API_KEY="$(grep -m1 "^SENSOR_API_KEY=" .env | sed 's/^SENSOR_API_KEY=//')"
if [[ -n "${SENSOR_API_KEY}" ]]; then
  curl -sf --max-time 3 -H "X-API-Key: ${SENSOR_API_KEY}" http://127.0.0.1:18082/api/v1/sensors >/dev/null
fi

# Bootstrap deterministic claim-token precondition for testerbot admin read-only flow.
echo "Bootstrapping testerbot claim-token precondition..."
python3 "${REPO_DIR}/scripts/bootstrap_testerbot_claim_token.py" --db-path "${TEST_DIR}/state.db"

# Automated testerbot E2E regression suite (runs on dedicated runtime and exits with result).
if should_enable_testerbot "${TEST_DIR}/.env"; then
  echo "Running resident single-message Telethon UAT..."
  docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/e2e_single_message_uat.py"

  echo "Running testerbot regression suite in test environment..."
  if [[ ! -f "${TEST_DIR}/docker-compose.testerbot.yml" ]]; then
    echo "ERROR: docker-compose.testerbot.yml is missing in ${TEST_DIR}"
    exit 1
  fi
  if ! timeout "${TESTERBOT_RUN_TIMEOUT_SEC}" docker compose -f docker-compose.yml -f docker-compose.testerbot.yml --profile testerbot run --rm testerbot; then
    run_exit_code=$?
    if [[ "${run_exit_code}" -eq 124 ]]; then
      echo "ERROR: testerbot regression suite exceeded ${TESTERBOT_RUN_TIMEOUT_SEC}s timeout."
    fi
    exit "${run_exit_code}"
  fi
  echo "Running testerbot callback coverage runtime smoke test..."
  python3 "${REPO_DIR}/scripts/smoke_testerbot_callback_coverage_runtime.py" \
    --coverage-file "${TEST_DIR}/logs/testerbot_callback_coverage.json" \
    --min-admin-clicked 18 \
    --min-resident-clicked 20 \
    --min-business-clicked 10
  echo "Running testerbot full coverage runtime smoke test..."
  python3 "${REPO_DIR}/scripts/smoke_testerbot_full_coverage_runtime.py" \
    --coverage-file "${TEST_DIR}/logs/testerbot_full_coverage.json"
  echo "Running testerbot full coverage strict-negative smoke test..."
  python3 "${REPO_DIR}/scripts/smoke_testerbot_full_coverage_strict_negative.py"
else
  echo "Testerbot disabled (TESTERBOT_ENABLED!=1)."
fi

# Automated smoke: adbot matcher contract (positive/negative/anti-false-positive).
echo "Running adbot matcher smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_matcher.py"

# Automated smoke: adbot matcher diagnostics contract (reason codes for no-match).
echo "Running adbot matcher diagnostics smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_matcher_diagnostics.py"

# Automated smoke: adbot strong-signal anti-false-positive guard.
echo "Running adbot strong-signal guard smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_strong_signal_guard.py"

# Automated smoke: adbot matcher quality gate (recall vs false-positive control set).
echo "Running adbot matcher quality gate smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_matcher_quality_gate.py"

# Automated smoke: adbot config contract (env requirements + allowlist rules).
echo "Running adbot config contract smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_config_contract.py"

# Automated smoke: prod adbot activation checklist contract.
echo "Running prod adbot activation checklist smoke test..."
python3 "${REPO_DIR}/scripts/smoke_prod_adbot_activation_checklist.py"

# Automated smoke: adbot chat-id bootstrap wiring contract.
echo "Running adbot chat-bootstrap policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_chat_bootstrap_policy.py"

# Automated smoke: adbot chat-id bootstrap runtime behavior (multi-pair placeholders and no-overwrite).
echo "Running adbot chat-bootstrap runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_chat_bootstrap_runtime.py"

# Automated smoke: adbot chat-analysis tool must stay runnable in container and via script wrapper.
echo "Running adbot chat-analysis tool policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_chat_analysis_tool_policy.py"

# Automated smoke: adbot anonymized pattern-storage privacy policy.
echo "Running adbot pattern privacy policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_pattern_privacy_policy.py"

# Automated smoke: adbot anonymized pattern import + review report runtime.
echo "Running adbot pattern import runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_pattern_import_runtime.py"

# Automated smoke: adbot cooldown contract (per chat+intent + dedupe behavior).
echo "Running adbot cooldown contract smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_cooldown_contract.py"

# Automated smoke: adbot inline contract (intent -> inline query -> response block).
echo "Running adbot inline contract smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_inline_contract.py"

# Automated smoke: adbot real-E2E runner contract (required prompts + anti-false guards).
echo "Running adbot E2E contract smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_e2e_contract.py"

# Automated smoke: adbot listener/pipeline integration via mock stubs.
echo "Running adbot pipeline integration smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_pipeline_integration.py"

# Runtime smoke: resident /adbot light_bind path in live test container.
echo "Running adbot light-bind runtime smoke test..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_adbot_light_bind_runtime.py"

# Runtime smoke: adbot pair-mode light routing (sensor_uuid -> fallback) in live runtime image.
echo "Running adbot pair light runtime smoke test..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_adbot_pair_light_runtime.py"

# Automated smoke: adbot decision logging contract (reasoned match/no-match logs).
echo "Running adbot decision logging smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_decision_logging.py"

# Automated smoke: adbot dedicated decision-file logging contract.
echo "Running adbot decision-file logging smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_decision_file_logging.py"

# Automated smoke: adbot source-allowlist filter must emit decision logs.
echo "Running adbot source-filter logging policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_source_filter_logging_policy.py"

# Automated smoke: adbot source-allowlist runtime behavior + decision reason.
echo "Running adbot source-filter runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_source_filter_runtime.py"

# Automated smoke: adbot listener exception path must emit decision reason.
echo "Running adbot listener-exception runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_listener_exception_runtime.py"

# Automated smoke: adbot self-outgoing E2E guard (same-session compatibility).
echo "Running adbot outgoing E2E guard smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_outgoing_e2e_guard.py"

# Automated smoke: adbot self-outgoing poll fallback policy.
echo "Running adbot self-outgoing poll policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_self_outgoing_poll_policy.py"

# Runtime smoke: strict source delivery must stay forwarded-only (no silent text fallback).
echo "Running adbot forward-delivery runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_adbot_forward_delivery_runtime.py"

# Optional real Telegram E2E for adbot on test groups.
if should_enable_adbot_e2e "${TEST_DIR}/.env"; then
  echo "Running adbot E2E test-groups suite..."

  ADBOT_E2E_DRIVER_SESSION_VAL="$(get_env_value "ADBOT_E2E_DRIVER_STRING_SESSION" "${TEST_DIR}/.env")"
  ADBOT_E2E_SOURCE_CHAT_ID_VAL="$(get_env_value "ADBOT_E2E_SOURCE_CHAT_ID" "${TEST_DIR}/.env")"
  ADBOT_E2E_INTERNAL_CHAT_ID_VAL="$(get_env_value "ADBOT_E2E_INTERNAL_CHAT_ID" "${TEST_DIR}/.env")"
  ADBOT_E2E_TIMEOUT_SEC_VAL="$(get_env_value "ADBOT_E2E_TIMEOUT_SEC" "${TEST_DIR}/.env")"
  ADBOT_E2E_POLL_SEC_VAL="$(get_env_value "ADBOT_E2E_POLL_SEC" "${TEST_DIR}/.env")"
  ADBOT_E2E_NEGATIVE_WAIT_SEC_VAL="$(get_env_value "ADBOT_E2E_NEGATIVE_WAIT_SEC" "${TEST_DIR}/.env")"
  ADBOT_E2E_VERIFY_FORWARD_VAL="$(get_env_value "ADBOT_E2E_VERIFY_FORWARD" "${TEST_DIR}/.env")"
  ADBOT_E2E_REQUIRE_SOURCE_FORWARDED_VAL="$(get_env_value "ADBOT_E2E_REQUIRE_SOURCE_FORWARDED" "${TEST_DIR}/.env")"
  ADBOT_E2E_PROMPT_PREFIX_VAL="$(get_env_value "ADBOT_E2E_PROMPT_PREFIX" "${TEST_DIR}/.env")"
  ADBOT_STRING_SESSION_VAL="$(get_env_value "ADBOT_STRING_SESSION" "${TEST_DIR}/.env")"
  TELETHON_API_ID_VAL="$(get_env_value "TELETHON_API_ID" "${TEST_DIR}/.env")"
  TELETHON_API_HASH_VAL="$(get_env_value "TELETHON_API_HASH" "${TEST_DIR}/.env")"
  ADBOT_E2E_STRICT_REAL_INLINE_VAL="$(get_env_value "ADBOT_E2E_STRICT_REAL_INLINE" "${TEST_DIR}/.env")"

  if [[ -z "${ADBOT_E2E_REQUIRE_SOURCE_FORWARDED_VAL}" ]]; then
    if [[ "${ADBOT_E2E_STRICT_REAL_INLINE_VAL}" == "1" ]]; then
      ADBOT_E2E_REQUIRE_SOURCE_FORWARDED_VAL="1"
    else
      ADBOT_E2E_REQUIRE_SOURCE_FORWARDED_VAL="0"
    fi
  fi

  if [[ -z "${ADBOT_E2E_DRIVER_SESSION_VAL}" || -z "${ADBOT_E2E_SOURCE_CHAT_ID_VAL}" ]]; then
    echo "ERROR: ADBOT_E2E_ENABLED=1 but required vars are missing:"
    echo "  - ADBOT_E2E_DRIVER_STRING_SESSION"
    echo "  - ADBOT_E2E_SOURCE_CHAT_ID"
    exit 1
  fi
  if [[ -z "${TELETHON_API_ID_VAL}" || -z "${TELETHON_API_HASH_VAL}" ]]; then
    echo "ERROR: ADBOT_E2E_ENABLED=1 but TELETHON_API_ID/TELETHON_API_HASH are missing."
    exit 1
  fi

  docker compose exec -T \
    -e TELETHON_API_ID="${TELETHON_API_ID_VAL}" \
    -e TELETHON_API_HASH="${TELETHON_API_HASH_VAL}" \
    -e ADBOT_E2E_DRIVER_STRING_SESSION="${ADBOT_E2E_DRIVER_SESSION_VAL}" \
    -e ADBOT_E2E_ADBOT_STRING_SESSION="${ADBOT_STRING_SESSION_VAL}" \
    -e ADBOT_E2E_SOURCE_CHAT_ID="${ADBOT_E2E_SOURCE_CHAT_ID_VAL}" \
    -e ADBOT_E2E_INTERNAL_CHAT_ID="${ADBOT_E2E_INTERNAL_CHAT_ID_VAL}" \
    -e ADBOT_E2E_TIMEOUT_SEC="${ADBOT_E2E_TIMEOUT_SEC_VAL:-45}" \
    -e ADBOT_E2E_POLL_SEC="${ADBOT_E2E_POLL_SEC_VAL:-1.0}" \
    -e ADBOT_E2E_NEGATIVE_WAIT_SEC="${ADBOT_E2E_NEGATIVE_WAIT_SEC_VAL:-12}" \
    -e ADBOT_E2E_VERIFY_FORWARD="${ADBOT_E2E_VERIFY_FORWARD_VAL:-1}" \
    -e ADBOT_E2E_REQUIRE_SOURCE_FORWARDED="${ADBOT_E2E_REQUIRE_SOURCE_FORWARDED_VAL}" \
    -e ADBOT_E2E_PROMPT_PREFIX="${ADBOT_E2E_PROMPT_PREFIX_VAL:-[E2E] }" \
    adbot python - < "${REPO_DIR}/scripts/e2e_adbot_test_groups.py"
else
  echo "Adbot E2E test-groups disabled (ADBOT_E2E_ENABLED!=1)."
fi

# Automated smoke: testerbot service must be test-only compose override.
echo "Running testerbot compose isolation smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_compose_isolation.py"

# Automated smoke: testerbot scenarios must stay idempotent/read-only by policy.
echo "Running testerbot idempotence policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_idempotence_policy.py"

# Automated smoke: testerbot callback-coverage strict gate must stay ON by default.
echo "Running testerbot callback-coverage strict policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_callback_coverage_strict_policy.py"

echo "Running testerbot callback contract policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_callback_contract_policy.py"

echo "Running testerbot full coverage policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_full_coverage_policy.py"

# Automated smoke: every admin/business callback must be explicitly classified (include or exclude).
echo "Running testerbot callback partition policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_callback_partition_policy.py"

# Automated smoke: testerbot admin business callbacks contract (payments/audit/export).
echo "Running testerbot admin business callbacks policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_admin_business_callbacks_policy.py"

# Automated smoke: testerbot admin claim-token open path must stay read-only-safe.
echo "Running testerbot admin claim-token readonly policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_admin_claim_tokens_readonly_policy.py"

# Runtime smoke: testerbot claim-token bootstrap must be deterministic/idempotent.
echo "Running testerbot claim-token bootstrap runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_testerbot_claim_token_bootstrap_runtime.py"

# Smoke: migrations/backfills for section-aware schema + clamp for 2-section buildings.
echo "Running sections migration/backfill smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_sections.py"

# Smoke: derived buildings.has_sensor/sensor_count must stay synced with active sensors.
echo "Running buildings sensor-stats sync smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_buildings_sensor_stats_sync.py"

# Smoke: sensor aliases contract (state propagation + stats/history fallback).
echo "Running sensor aliases smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_sensor_aliases.py"

# Automated smoke: place click stats (DB-backed views counters).
echo "Running place click stats smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_place_click_stats.py"

# Automated smoke: place clicks analytics contract (daily actions + coupon_open).
echo "Running place clicks analytics policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_place_clicks_policy.py"

# Automated smoke: schema.sql and runtime init_db parity for tables/indexes.
echo "Running schema/runtime parity smoke test..."
python3 "${REPO_DIR}/scripts/smoke_schema_runtime_parity.py"

# Automated smoke: legacy `places` schema backfill via init_db().
echo "Running init_db legacy places backfill smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_init_db_legacy_places_columns.py"

# Automated smoke: business mock payments state machine + idempotency.
# Run inside container (all runtime deps are present there).
echo "Running business payments smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_payments.py"

# Automated smoke: telegram_stars provider flow (intent -> pre_checkout -> success -> idempotency).
echo "Running business Telegram Stars flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_telegram_stars_flow.py"

# Automated smoke: telegram_stars duplicate pre_checkout safety + idempotency.
echo "Running business Telegram Stars pre-checkout idempotency smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_telegram_stars_precheckout_idempotency.py"

# Automated smoke: telegram_stars non-success terminal events (cancel/fail/refund + idempotency).
echo "Running business Telegram Stars terminal-events smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_telegram_stars_terminal_events.py"

# Automated smoke: telegram_stars refund update handler (fallback via charge_id, invoice_payload may be missing).
echo "Running business Telegram Stars refund-update smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_telegram_stars_refund_update.py"

# Automated smoke: parity mock vs telegram_stars for non-success outcomes.
echo "Running business payment-provider parity smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_payment_provider_parity.py"

# Automated smoke: canonical refund event (persist/audit/idempotency contract).
echo "Running business refund event smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_refund_event.py"

# Automated smoke: admin manual refund fallback (for real Telegram Stars refunds that may not deliver updates).
echo "Running business admin manual refund smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_admin_manual_refund.py"

# Automated smoke: subscription lifecycle reconciliation (active -> past_due -> free).
echo "Running business subscription lifecycle smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_subscription_lifecycle.py"

# Automated smoke: owner cancel keeps entitlement until expiry, then reconcile downgrades.
echo "Running business subscription cancel smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_subscription_cancel.py"

# Automated smoke: owner card copy for canceled subscription (status + active until).
echo "Running business owner-card canceled-copy smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_owner_card_cancel_copy.py"

# Automated smoke: plans keyboard contract for active/canceled/free flows.
echo "Running business plan-keyboard cancel contract smoke test..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_plan_keyboard_cancel_contract.py"

# Automated smoke: runtime style contract for plan buttons (light/pro/partner/cancel).
echo "Running business plan-button runtime style smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_plan_button_styles_runtime.py"

# Automated smoke: downgrade paid->free must purge likes gained during paid windows.
echo "Running business paid-like purge smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_paid_likes_purge.py"

# Automated smoke: admin promo/subscription tier transitions + verified sync.
echo "Running business admin subscription promo smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_admin_subscription_promo.py"

# Automated smoke: admin place lifecycle (create/publish/unpublish/delete draft).
echo "Running business admin place lifecycle smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_admin_place_lifecycle.py"

# Automated smoke: claim-token create/rotate/claim flow.
echo "Running business claim-token smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_claim_tokens.py"

# Automated smoke: bulk claim-token rotation for all places + audit.
echo "Running business claim-token bulk rotation smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_claim_tokens_bulk_rotation.py"

# Automated smoke: moderation status machine (pending -> approved/rejected terminal).
echo "Running business owner-request state-machine smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_owner_request_state_machine.py"

# Automated smoke: claim existing place -> pending -> approve flow.
echo "Running business claim moderation flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_claim_moderation_flow.py"

# Automated smoke: after approve + payment, main-bot enrichment exposes verified metadata.
echo "Running business main-bot verified-after-approve smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_mainbot_verified_after_approve.py"

# Automated smoke: moderation reject keeps place as unpublished draft.
echo "Running business reject->unpublished smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_reject_unpublished.py"

# Automated smoke: moderation approve publishes place and enables business flags.
echo "Running business approve->publish smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_approve_publish.py"

# Automated smoke: moderation approve/reject audit side-effects contract.
echo "Running business moderation audit contract smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_moderation_audit_contract.py"

# Automated smoke: resident catalog visibility gate (publish controls exposure).
echo "Running business visibility publish-gate smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_visibility_publish_gate.py"

# Automated smoke: admin owner-request alert deep-link/jump UI helpers.
echo "Running admin owner-request alert deep-link smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_admin_owner_alert_deeplink.py"

# Automated smoke: owner-request alert must be enqueued via admin_jobs queue.
echo "Running business owner-request alert queue smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_owner_alert_job_queue.py"

# Automated smoke: admin subscriptions paging/export contract.
echo "Running admin subscriptions paging smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_admin_business_subscriptions_paging.py"

# Automated smoke: admin payments paging/export contract.
echo "Running admin payments paging smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_admin_business_payments_paging.py"

# Automated smoke: admin subscriptions/payments handler runtime contract.
echo "Running admin business paging handlers runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_admin_business_paging_handler_runtime.py"

# Automated smoke: admin owner-request alert UI policy (single-message + nav callbacks).
echo "Running admin owner-alert UI policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_admin_owner_alert_ui_policy.py"

# Automated smoke: admin moderation UI contract (owner contact + approve/reject flow).
echo "Running admin business moderation UI policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_admin_business_moderation_ui_policy.py"

# Automated smoke: resident place-report -> admin moderation policy.
echo "Running business place-report policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_place_reports_policy.py"

# Automated smoke: dynamic place-reports flow (create/order/resolve/admin-job payload).
echo "Running business place-reports flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_place_reports_flow.py"

# Automated smoke: reports queue priority (Premium/Partner first, then Light, then regular).
echo "Running business reports priority policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_reports_priority_policy.py"

# Automated smoke: businessbot Free-owner suggest-edit moderation flow.
echo "Running business free edit-request policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_free_edit_request_policy.py"

# Automated smoke: businessbot free edit-request handler flow (FSM submit -> report + admin job).
echo "Running business free edit-request handler-flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_free_edit_request_handler_flow.py"

# Automated smoke: admin places UI contract (publish/hide/delete/reject/edit/promo).
echo "Running admin business places UI policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_admin_business_places_ui_policy.py"

# Automated smoke: admin claim-token UI flow policy (callbacks + token screen nav).
echo "Running admin claim-tokens UI policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_admin_business_claim_tokens_ui_policy.py"

# Automated smoke: admin claim-token security hygiene (code-tag render + no token payload logs).
echo "Running admin claim-tokens security policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_admin_claim_tokens_security_policy.py"

# Automated smoke: admin claim-token callback handler flow (menu->service->place->open->rotate).
echo "Running admin claim-tokens handler flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_admin_claim_tokens_handler_flow.py"

# Automated smoke: admin business moderation callback runtime (jump/approve/reject + visibility gate).
echo "Running admin business moderation runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_admin_business_moderation_runtime.py"

# Automated smoke: admin claim-token bulk-generation handler flow (confirm -> rotate all).
echo "Running admin claim-tokens bulk handler flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_admin_claim_tokens_bulk_handler_flow.py"

# Automated smoke: business owner address edit must use building-picker flow.
echo "Running business address edit policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_address_edit_policy.py"

# Automated smoke: static write-retry policy for business repository.
echo "Running business write-retry policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_write_retry_policy.py"

# Automated smoke: businessbot inline-only menu policy.
echo "Running business UI inline/menu policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_ui_inline_menu_policy.py"

# Automated smoke: businessbot must keep only one legacy admin callback catch-all.
echo "Running business legacy admin-callbacks policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_legacy_admin_callbacks_policy.py"

# Automated smoke: businessbot must not expose legacy admin command surface.
echo "Running business no-admin-commands policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_no_admin_commands_policy.py"

# Automated smoke: callback handlers must not be no-op stubs with dead code.
echo "Running business callback dead-code policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_callback_deadcode_policy.py"

# Automated smoke: owner/admin downgrade responsibilities (owner cancel-only, admin can force free).
echo "Running business owner/admin downgrade policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_owner_admin_downgrade_policy.py"

# Automated smoke: Free vs Light owner edit entitlement contract.
echo "Running business light owner edit contract smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_light_owner_edit_contract.py"

# Automated smoke: businessbot user copy hygiene (no technical IDs in owner UI).
echo "Running business UI copy hygiene smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_ui_copy_sanitized.py"

# Automated smoke: owner tier-gating (locked labels + handler guards).
echo "Running business owner tier-gating policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_owner_tier_gating_policy.py"

# Automated smoke: owner place-card actions must come from one shared builder.
echo "Running business owner card actions policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_owner_card_actions_policy.py"

# Automated smoke: runtime styles for locked/unlocked owner edit keyboard fields.
echo "Running business edit-keyboard runtime style smoke test..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_edit_keyboard_styles_runtime.py"

# Automated smoke: business owner card activity stats block (views + coupon opens).
echo "Running business card activity stats policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_card_activity_stats_policy.py"

# Automated smoke: business owner card activity dynamic counters (all CTA actions + CTR).
echo "Running business card activity stats dynamic smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_card_activity_stats_dynamic.py"

# Automated smoke: premium daily activity stats block (7-day timeline).
echo "Running business daily activity stats policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_daily_stats_policy.py"

# Automated smoke: free-tier resident place-card content baseline.
echo "Running business free place-card content policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_free_place_card_content_policy.py"

# Automated smoke: free-tier resident baseline (catalog/likes/minimal card/map path).
echo "Running business free resident baseline policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_free_resident_baseline_policy.py"

# Automated smoke: premium offers (2 text slots) policy.
echo "Running business offers policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_offers_policy.py"

# Automated smoke: promo-code format policy for business owners.
echo "Running business promo-code policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_promo_code_policy.py"

# Automated smoke: coupon-open contract (resident CTA + analytics counters).
echo "Running business coupon-open contract smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_coupon_open_contract.py"

# Automated smoke: resident link/coupon callback runtime (redirect/alert + analytics writes).
echo "Running business link/coupon callback runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_link_coupon_callbacks_runtime.py"

# Automated smoke: shared plan matrix (titles/prices/tiers) policy.
echo "Running business plan matrix policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_plan_matrix_policy.py"

# Automated smoke: runtime test-only Stars price overrides contract.
echo "Running business plan price overrides runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_plan_price_overrides_runtime.py"

# Automated smoke: contact value validation policy for business owners.
echo "Running business contact validation policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_contact_validation_policy.py"

# Automated smoke: resident contact CTA callbacks runtime (chat/call redirects + click analytics).
echo "Running business contact CTA runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_contact_cta_runtime.py"

# Automated smoke: Light+ logo/photo field policy.
echo "Running business logo policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_logo_policy.py"

# Automated smoke: Partner branded gallery policy.
echo "Running business partner gallery policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_partner_gallery_policy.py"

# Automated smoke: 0..N business gallery contract (schema/repo/service/handlers).
echo "Running business gallery policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_gallery_policy.py"

# Automated smoke: business gallery runtime flow (add/list/limit/open/remove).
echo "Running business gallery runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_gallery_runtime.py"

# Automated smoke: media file_id runtime (logo/offer/partner photo callbacks).
echo "Running business media file_id runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_media_file_id_runtime.py"

# Automated smoke: owner handler-flow for media photo input in waiting_value state.
echo "Running business media photo-input handler flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_media_photo_input_handler_flow.py"

# Automated smoke: Partner branded resident card runtime (badge + description + offers + photo CTAs).
echo "Running business partner branded-card runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_partner_branded_card_runtime.py"

# Automated smoke: businessbot QR deep-link policy for Light+ owners.
echo "Running business QR deep-link policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_qr_deeplink_policy.py"

# Automated smoke: Partner QR-kit (locked CTA + partner PNG templates/instructions).
echo "Running business partner QR-kit policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_partner_qr_kit_policy.py"

# Automated smoke: Partner QR-kit PDF templates via API endpoint.
echo "Running business QR-kit PDF policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_qr_kit_pdf_policy.py"

# Automated smoke: Partner priority support flow (owner CTA -> admin queue).
echo "Running business partner support policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_partner_priority_support_policy.py"

# Automated smoke: QR access behavior (Free lock -> plans, Light -> QR screen/deep-link).
echo "Running business QR access flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_qr_access_flow.py"

# Automated smoke: sponsored row runtime (daily limit + resident opt-in toggle).
echo "Running business sponsored-row runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_sponsored_row_runtime.py"

# Automated smoke: WebApp sponsored-offers toggle contract (frontend + backend wiring).
echo "Running business WebApp sponsored-toggle policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_webapp_sponsored_toggle_policy.py"

# Automated smoke: resident `/start place_<id>` deep-link renders full card + like uniqueness.
echo "Running business QR resident deep-link like-flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_qr_resident_deeplink_like_flow.py"

# Automated smoke: exactly one active Partner tier per category.
echo "Running business partner-slot uniqueness smoke test..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_partner_slot_uniqueness.py"

# Automated smoke: exactly one active Premium(Pro) slot per category.
echo "Running business pro-slot uniqueness smoke test..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_pro_slot_uniqueness.py"

# Automated smoke: resident place-card entitlement keyboard (free vs verified CTAs).
echo "Running business place-card entitlement smoke test..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_place_card_entitlement.py"

# Automated smoke: free/unverified resident card must not expose paid/contact CTAs.
echo "Running business free-card no-paid-CTA runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_free_card_no_paid_cta_runtime.py"

# Automated smoke: resident catalog ranking contract (partner -> promo -> verified -> unverified).
echo "Running business catalog ranking policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_catalog_ranking_policy.py"

# Automated smoke: resident catalog runtime ranking contract via real callback rendering.
echo "Running business catalog ranking runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_catalog_ranking_runtime.py"

# Automated smoke: Premium(Pro) promo-slot respects promo_slot_until runtime window.
echo "Running business promo-slot window runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_promo_slot_window_runtime.py"

# Automated smoke: no medals must be shown for zero-like rows in BUSINESS_MODE catalog.
echo "Running business catalog zero-like medal runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_catalog_zero_likes_medals_runtime.py"

# Automated smoke: resident catalog colored button styles for partner/pro slots.
echo "Running business catalog button styles policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_catalog_button_styles_policy.py"

# Automated smoke: resident verified badge contract in list + detail card.
echo "Running business verified badge policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_verified_badge_policy.py"

# Automated smoke: runtime verified tier display labels in resident place-card.
echo "Running business verified tier-label runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_verified_tier_label_runtime.py"

# Automated smoke: businessbot single-message rendering policy.
echo "Running business single-message policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_single_message_policy.py"

# Automated smoke: resident single-message interactive contract.
echo "Running resident single-message interactive runtime smoke test..."
python3 "${REPO_DIR}/scripts/smoke_single_message_interactive_runtime.py"

# Automated smoke: transaction/network boundary policy for business layer.
echo "Running business transaction boundary policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_transaction_boundary_policy.py"

# Automated smoke: payment pipeline policy (UI handlers -> apply_payment_event).
echo "Running business payment pipeline policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_payment_pipeline_policy.py"

# Automated smoke: shared sqlite transaction boundary policy (database/repository DB-only).
echo "Running sqlite transaction boundary policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_sqlite_transaction_boundary_policy.py"

# Automated smoke: function-level BEGIN scope policy (allowed modules only, no network in tx funcs).
echo "Running sqlite transaction function-scope policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_sqlite_tx_function_scope_policy.py"

# Automated smoke: deploy freeze/unfreeze ops policy (no global light OFF in deploy script).
echo "Running ops freeze deploy policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_ops_freeze_deploy_policy.py"

# Automated smoke: prod adbot deploy guard policy (must fail-fast on invalid prod env).
echo "Running deploy_prod adbot guard policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_deploy_prod_adbot_guard_policy.py"

# Automated smoke: resident-facing BUSINESS_MODE guard policy.
echo "Running business guard policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_guard_policy.py"

# Automated smoke: subscription maintenance guard policy (BUSINESS_MODE vs businessbot token).
echo "Running business subscription maintenance policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_subscription_maintenance_policy.py"

# Automated smoke: resident places UI policy for BUSINESS_MODE on/off.
echo "Running business mode UI policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_mode_ui_policy.py"

# Automated smoke: when verified_count=0 resident UI must not expose monetization hints.
echo "Running no-monetization-when-no-verified smoke test..."
python3 "${REPO_DIR}/scripts/smoke_no_monetization_when_no_verified.py"
echo "Running webapp business-offers hidden contract smoke test..."
python3 "${REPO_DIR}/scripts/smoke_webapp_business_offers_hidden_contract.py"
echo "Running no-monetization-when-no-verified runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_no_monetization_when_no_verified_runtime.py"

# Pre-prod style checklist snapshot (test DB): revision/hash + verified_count + stealth guard.
echo "Running pre-prod resident UI checklist (test DB snapshot)..."
python3 "${REPO_DIR}/scripts/preprod_resident_ui_checklist.py" --db-path "${TEST_DIR}/state.db"

# Automated smoke: resident place-card CTA contract for BUSINESS_MODE OFF vs ON.
echo "Running business mode place-card compare smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_mode_card_compare.py"

# Automated smoke: search-menu routing (avoid generic fallback shadowing search text).
echo "Running search menu routing policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_search_menu_routing_policy.py"

# Automated smoke: keyword search behavior (published-only, case-insensitive, likes tie-break).
echo "Running search places keywords smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_search_places_keywords.py"

# Automated smoke: public sensor API key + freeze-independent status policy.
echo "Running public sensor API policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_public_sensor_api_policy.py"

# Automated smoke: canonical sensor UUID -> building mapping (protects rollout sensors).
echo "Running sensor UUID canonical mapping policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_sensor_uuid_canonical_mapping_policy.py"

# Automated smoke: verify resident-bot isolation when BUSINESS_MODE=0.
echo "Running business mode-off isolation smoke test in test container..."
docker compose exec -T powerbot env BUSINESS_MODE=0 BUSINESS_BOT_API_KEY= python - < "${REPO_DIR}/scripts/smoke_business_mode_off.py"

# Automated smoke: "stealth" rollout (businessbot enabled with token while resident UI stays legacy).
echo "Running businessbot stealth smoke test in test container..."
docker compose exec -T powerbot env BUSINESS_MODE=0 BUSINESS_BOT_API_KEY=000000000:dummy python - < "${REPO_DIR}/scripts/smoke_businessbot_stealth.py"

# Automated smoke: compare resident catalog OFF(no-op) vs ON(integration) business metadata path.
echo "Running business mode catalog compare smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_mode_catalog_compare.py"

# Automated smoke: admin offers-digest job wiring (admin UI -> queue -> worker -> DB helpers).
echo "Running offers digest job policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_offers_digest_job_policy.py"

# Automated smoke: offers-digest runtime eligibility (opt-in + quiet-hours + rate-limit).
echo "Running offers digest runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_offers_digest_runtime.py"

# Automated smoke: offers-digest worker handler runtime (queue progress + sent marks).
echo "Running offers digest worker runtime smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_offers_digest_worker_runtime.py"

# Automated smoke: sqlite concurrent writes (3 writers + retry/backoff).
echo "Running sqlite concurrency smoke test..."
python3 "${REPO_DIR}/scripts/smoke_sqlite_concurrency.py"

# Automated smoke: admin_jobs queue concurrent claim/finish consistency.
echo "Running admin_jobs concurrency smoke test..."
python3 "${REPO_DIR}/scripts/smoke_admin_jobs_concurrency.py"

# Optional: mini app health if endpoint exists.
curl -s http://127.0.0.1:18082/api/v1/webapp/health >/dev/null || true

# Log health gate (fail only on bad patterns).
"${REPO_DIR}/scripts/log_health_check.sh" powerbot
