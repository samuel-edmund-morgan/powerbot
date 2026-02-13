#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERHUB_USER="${DOCKERHUB_USER:-semorgana}"
VERSION="${VERSION:-$(date +%Y.%m.%d-%H%M)}"
MIGRATE="${MIGRATE:-0}"
TEST_DIR="/opt/powerbot-test"

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

should_enable_business_profile() {
  local env_file="$1"
  local mode token
  mode="$(get_env_value "BUSINESS_MODE" "$env_file")"
  token="$(get_env_value "BUSINESS_BOT_API_KEY" "$env_file")"
  [[ "$mode" == "1" && -n "$token" ]]
}

should_enable_admin_profile() {
  local env_file="$1"
  local token
  token="$(get_env_value "ADMIN_BOT_API_KEY" "$env_file")"
  [[ -n "$token" ]]
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

# У test завжди працюємо через mock-оплати (без реальних списань Stars).
if grep -q "^BUSINESS_PAYMENT_PROVIDER=" "${TEST_DIR}/.env"; then
  sed -i -E 's/^BUSINESS_PAYMENT_PROVIDER=.*/BUSINESS_PAYMENT_PROVIDER=mock/' "${TEST_DIR}/.env"
else
  echo "BUSINESS_PAYMENT_PROVIDER=mock" >> "${TEST_DIR}/.env"
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
  echo "Business profile enabled (BUSINESS_MODE=1 and BUSINESS_BOT_API_KEY is set)."
  profiles+=(--profile business)
else
  echo "Business profile disabled (missing BUSINESS_BOT_API_KEY or BUSINESS_MODE!=1)."
fi
if should_enable_admin_profile "${TEST_DIR}/.env"; then
  echo "Admin profile enabled (ADMIN_BOT_API_KEY is set)."
  profiles+=(--profile admin)
else
  echo "Admin profile disabled (missing ADMIN_BOT_API_KEY)."
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

# Automated smoke: business mock payments state machine + idempotency.
# Run inside container (all runtime deps are present there).
echo "Running business payments smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_payments.py"

# Automated smoke: telegram_stars provider flow (intent -> pre_checkout -> success -> idempotency).
echo "Running business Telegram Stars flow smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_telegram_stars_flow.py"

# Automated smoke: telegram_stars non-success terminal events (cancel/fail/refund + idempotency).
echo "Running business Telegram Stars terminal-events smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_telegram_stars_terminal_events.py"

# Automated smoke: parity mock vs telegram_stars for non-success outcomes.
echo "Running business payment-provider parity smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_payment_provider_parity.py"

# Automated smoke: canonical refund event (persist/audit/idempotency contract).
echo "Running business refund event smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_refund_event.py"

# Automated smoke: subscription lifecycle reconciliation (active -> past_due -> free).
echo "Running business subscription lifecycle smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_subscription_lifecycle.py"

# Automated smoke: claim-token create/rotate/claim flow.
echo "Running business claim-token smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_claim_tokens.py"

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

# Automated smoke: admin owner-request alert UI policy (single-message + nav callbacks).
echo "Running admin owner-alert UI policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_admin_owner_alert_ui_policy.py"

# Automated smoke: static write-retry policy for business repository.
echo "Running business write-retry policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_write_retry_policy.py"

# Automated smoke: businessbot inline-only menu policy.
echo "Running business UI inline/menu policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_ui_inline_menu_policy.py"

# Automated smoke: businessbot user copy hygiene (no technical IDs in owner UI).
echo "Running business UI copy hygiene smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_ui_copy_sanitized.py"

# Automated smoke: businessbot single-message rendering policy.
echo "Running business single-message policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_single_message_policy.py"

# Automated smoke: transaction/network boundary policy for business layer.
echo "Running business transaction boundary policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_transaction_boundary_policy.py"

# Automated smoke: payment pipeline policy (UI handlers -> apply_payment_event).
echo "Running business payment pipeline policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_payment_pipeline_policy.py"

# Automated smoke: shared sqlite transaction boundary policy (database/repository DB-only).
echo "Running sqlite transaction boundary policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_sqlite_transaction_boundary_policy.py"

# Automated smoke: resident-facing BUSINESS_MODE guard policy.
echo "Running business guard policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_guard_policy.py"

# Automated smoke: resident places UI policy for BUSINESS_MODE on/off.
echo "Running business mode UI policy smoke test..."
python3 "${REPO_DIR}/scripts/smoke_business_mode_ui_policy.py"

# Automated smoke: verify resident-bot isolation when BUSINESS_MODE=0.
echo "Running business mode-off isolation smoke test in test container..."
docker compose exec -T powerbot env BUSINESS_MODE=0 BUSINESS_BOT_API_KEY= python - < "${REPO_DIR}/scripts/smoke_business_mode_off.py"

# Automated smoke: compare resident catalog OFF(no-op) vs ON(integration) business metadata path.
echo "Running business mode catalog compare smoke test in test container..."
docker compose exec -T powerbot python - < "${REPO_DIR}/scripts/smoke_business_mode_catalog_compare.py"

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
