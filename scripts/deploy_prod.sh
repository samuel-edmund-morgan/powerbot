#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERHUB_USER="${DOCKERHUB_USER:-semorgana}"
VERSION="${VERSION:-$(date +%Y.%m.%d-%H%M)}"
MIGRATE="${MIGRATE:-0}"
PROD_DIR="/opt/powerbot"

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

is_placeholder_value() {
  local value="${1:-}"
  local lower="${value,,}"
  if [[ -z "${lower}" ]]; then
    return 0
  fi
  case "${lower}" in
    your|your-*|your_*|*placeholder*|*example*|*changeme*|*replace*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

ensure_required_prod_profiles() {
  # From 2026-02: prod always runs 3 bots (powerbot + adminbot + businessbot).
  local env_file="$1"
  local business_token admin_token
  business_token="$(get_env_value "BUSINESS_BOT_API_KEY" "$env_file")"
  admin_token="$(get_env_value "ADMIN_BOT_API_KEY" "$env_file")"

  if [[ -z "${business_token}" ]]; then
    echo "ERROR: BUSINESS_BOT_API_KEY is empty in ${env_file} (prod always runs businessbot)."
    exit 1
  fi
  if [[ -z "${admin_token}" ]]; then
    echo "ERROR: ADMIN_BOT_API_KEY is empty in ${env_file} (prod always runs adminbot)."
    exit 1
  fi
}

should_enable_adbot() {
  local env_file="$1"
  env_flag_true "$(get_env_value "ADBOT_ENABLED" "$env_file")"
}

count_numeric_chat_ids() {
  local raw="${1:-}"
  local token count=0
  for token in ${raw//,/ }; do
    token="$(strip_quotes "$token")"
    [[ -z "${token}" ]] && continue
    if [[ "${token}" =~ ^-?[0-9]+$ ]]; then
      count=$((count + 1))
    fi
  done
  echo "${count}"
}

chat_id_variants() {
  local value="${1:-}"
  local abs raw
  if [[ ! "${value}" =~ ^-?[0-9]+$ ]]; then
    echo ""
    return 0
  fi
  abs="${value#-}"
  raw="${abs}"
  if [[ "${value}" =~ ^- ]] && [[ "${raw}" == 100* ]] && [[ "${#raw}" -gt 3 ]]; then
    echo "${value} -${raw:3}"
    return 0
  fi
  if [[ "${value}" =~ ^- ]] && [[ "${raw}" != 100* ]]; then
    echo "${value} -100${raw}"
    return 0
  fi
  echo "${value}"
}

ensure_adbot_prod_config() {
  local env_file="$1"
  local adbot_test_mode api_id api_hash session source_ids internal_id target_username source_count
  local pair_count_raw pair_count pair_mode idx source_key internal_key sensor_key fallback_building_key fallback_section_key cooldown_key
  local source_id pair_internal_id sensor_uuid fallback_building fallback_section cooldown_raw

  adbot_test_mode="$(get_env_value "ADBOT_TEST_MODE" "$env_file")"
  if env_flag_true "${adbot_test_mode}"; then
    echo "ERROR: ADBOT_TEST_MODE must be 0 in prod when ADBOT_ENABLED=1."
    exit 1
  fi

  api_id="$(get_env_value "TELETHON_API_ID" "$env_file")"
  if [[ ! "${api_id}" =~ ^[0-9]+$ ]]; then
    echo "ERROR: TELETHON_API_ID must be a numeric value when ADBOT_ENABLED=1."
    exit 1
  fi

  api_hash="$(get_env_value "TELETHON_API_HASH" "$env_file")"
  if is_placeholder_value "${api_hash}"; then
    echo "ERROR: TELETHON_API_HASH is empty or placeholder when ADBOT_ENABLED=1."
    exit 1
  fi

  session="$(get_env_value "ADBOT_STRING_SESSION" "$env_file")"
  if is_placeholder_value "${session}"; then
    echo "ERROR: ADBOT_STRING_SESSION is empty or placeholder when ADBOT_ENABLED=1."
    exit 1
  fi

  target_username="$(get_env_value "ADBOT_TARGET_POWERBOT_USERNAME" "$env_file")"
  target_username="${target_username#@}"
  if is_placeholder_value "${target_username}"; then
    echo "ERROR: ADBOT_TARGET_POWERBOT_USERNAME is empty or placeholder when ADBOT_ENABLED=1."
    exit 1
  fi

  pair_count_raw="$(get_env_value "ADBOT_PAIR_COUNT" "$env_file")"
  pair_count=0
  if [[ -n "${pair_count_raw}" ]]; then
    if [[ ! "${pair_count_raw}" =~ ^[0-9]+$ ]]; then
      echo "ERROR: ADBOT_PAIR_COUNT must be numeric when set."
      exit 1
    fi
    pair_count="${pair_count_raw}"
  fi
  pair_mode=0
  if [[ "${pair_count}" -gt 0 ]]; then
    pair_mode=1
  fi

  if [[ "${pair_mode}" -eq 1 ]]; then
    declare -A seen_source_variants=()
    declare -A seen_internal_variants=()
    for idx in $(seq 1 "${pair_count}"); do
      source_key="ADBOT_PAIR_${idx}_SOURCE_CHAT_ID"
      internal_key="ADBOT_PAIR_${idx}_INTERNAL_CHAT_ID"
      sensor_key="ADBOT_PAIR_${idx}_SENSOR_UUID"
      fallback_building_key="ADBOT_PAIR_${idx}_FALLBACK_BUILDING_ID"
      fallback_section_key="ADBOT_PAIR_${idx}_FALLBACK_SECTION_ID"
      cooldown_key="ADBOT_PAIR_${idx}_REPLY_COOLDOWN_SEC"

      source_id="$(get_env_value "${source_key}" "$env_file")"
      if [[ ! "${source_id}" =~ ^-?[0-9]+$ ]]; then
        echo "ERROR: ${source_key} must be a numeric chat id in pair-mode."
        exit 1
      fi

      pair_internal_id="$(get_env_value "${internal_key}" "$env_file")"
      if [[ ! "${pair_internal_id}" =~ ^-?[0-9]+$ ]]; then
        echo "ERROR: ${internal_key} must be a numeric chat id in pair-mode."
        exit 1
      fi

      sensor_uuid="$(get_env_value "${sensor_key}" "$env_file")"
      if is_placeholder_value "${sensor_uuid}"; then
        echo "ERROR: ${sensor_key} is empty or placeholder in pair-mode."
        exit 1
      fi

      fallback_building="$(get_env_value "${fallback_building_key}" "$env_file")"
      if [[ ! "${fallback_building}" =~ ^[0-9]+$ ]] || [[ "${fallback_building}" -le 0 ]]; then
        echo "ERROR: ${fallback_building_key} must be integer > 0 in pair-mode."
        exit 1
      fi

      fallback_section="$(get_env_value "${fallback_section_key}" "$env_file")"
      if [[ ! "${fallback_section}" =~ ^[0-9]+$ ]] || [[ "${fallback_section}" -le 0 ]]; then
        echo "ERROR: ${fallback_section_key} must be integer > 0 in pair-mode."
        exit 1
      fi

      cooldown_raw="$(get_env_value "${cooldown_key}" "$env_file")"
      if [[ -n "${cooldown_raw}" ]] && { [[ ! "${cooldown_raw}" =~ ^-?[0-9]+$ ]] || [[ "${cooldown_raw}" -lt 0 ]]; }; then
        echo "ERROR: ${cooldown_key} must be integer >= 0 when set."
        exit 1
      fi

      for variant in $(chat_id_variants "${source_id}"); do
        if [[ -n "${seen_source_variants[${variant}]:-}" ]]; then
          echo "ERROR: ${source_key} duplicates another source chat id (including variants) in pair-mode."
          exit 1
        fi
      done
      for variant in $(chat_id_variants "${source_id}"); do
        seen_source_variants["${variant}"]=1
      done

      for variant in $(chat_id_variants "${pair_internal_id}"); do
        if [[ -n "${seen_internal_variants[${variant}]:-}" ]]; then
          echo "ERROR: ${internal_key} duplicates another internal chat id (including variants) in pair-mode."
          exit 1
        fi
      done
      for variant in $(chat_id_variants "${pair_internal_id}"); do
        seen_internal_variants["${variant}"]=1
      done
    done
  else
    source_ids="$(get_env_value "ADBOT_SOURCE_CHAT_IDS" "$env_file")"
    source_count="$(count_numeric_chat_ids "${source_ids}")"
    if [[ "${source_count}" -lt 1 ]]; then
      echo "ERROR: ADBOT_SOURCE_CHAT_IDS must contain at least one numeric chat id when ADBOT_ENABLED=1."
      exit 1
    fi

    internal_id="$(get_env_value "ADBOT_INTERNAL_CHAT_ID" "$env_file")"
    if [[ ! "${internal_id}" =~ ^-?[0-9]+$ ]]; then
      echo "ERROR: ADBOT_INTERNAL_CHAT_ID must be a numeric chat id when ADBOT_ENABLED=1."
      exit 1
    fi
  fi
}

assert_service_running() {
  local svc="$1"
  local cid running
  cid="$(docker compose ps -q "$svc" 2>/dev/null || true)"
  if [[ -z "${cid}" ]]; then
    echo "ERROR: service ${svc} has no container (expected to be running)."
    docker compose ps || true
    exit 1
  fi
  running="$(docker inspect -f '{{.State.Running}}' "${cid}" 2>/dev/null || echo "false")"
  if [[ "${running}" != "true" ]]; then
    echo "ERROR: service ${svc} container is not running."
    docker compose ps || true
    docker logs --tail=120 "${cid}" 2>/dev/null || true
    exit 1
  fi
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

echo "Sync docker-compose.yml to ${PROD_DIR}..."
install -m 0644 "${REPO_DIR}/docker-compose.yml" "${PROD_DIR}/docker-compose.yml"

echo "Sync .env keys to ${PROD_DIR}..."
if [[ -f "${REPO_DIR}/.env.example" ]]; then
  touch "${PROD_DIR}/.env"
  while IFS= read -r line; do
    [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]] || continue
    key="${line%%=*}"
    if ! grep -q "^${key}=" "${PROD_DIR}/.env"; then
      echo "${line}" >> "${PROD_DIR}/.env"
    fi
  done < "${REPO_DIR}/.env.example"
fi

cd "${PROD_DIR}"

echo "NOTE: deploy_prod no longer forces light_notifications_global=off."

ensure_required_prod_profiles "${PROD_DIR}/.env"

# Freeze sensors automatically around deploy to avoid false "down/up" due to compose down/pull/up.
# We freeze only sensors that are not already frozen (or whose freeze is expired).
# After the stack is up, we wait a bit for sensors to report heartbeat, then unfreeze only those
# we froze in this deploy (tracked by frozen_at=FREEZE_AT).
DEPLOY_FREEZE_SENSORS="${DEPLOY_FREEZE_SENSORS:-1}"
DEPLOY_FREEZE_MINUTES="${DEPLOY_FREEZE_MINUTES:-20}"
DEPLOY_UNFREEZE_WAIT_SEC="${DEPLOY_UNFREEZE_WAIT_SEC:-120}"

FREEZE_AT=""
FROZEN_BY_DEPLOY_COUNT="0"
if [[ "${DEPLOY_FREEZE_SENSORS}" == "1" && -f "${PROD_DIR}/state.db" ]]; then
  sensors_table="$(sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sensors' LIMIT 1;" 2>/dev/null || echo "")"
  if [[ "${sensors_table}" == "1" ]]; then
    FREEZE_AT="$(sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" "SELECT strftime('%Y-%m-%dT%H:%M:%S','now','localtime');")"
    FREEZE_UNTIL="$(sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" "SELECT strftime('%Y-%m-%dT%H:%M:%S','now','localtime','+${DEPLOY_FREEZE_MINUTES} minutes');")"

    section_table="$(sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" "SELECT 1 FROM sqlite_master WHERE type='table' AND name='building_section_power_state' LIMIT 1;" 2>/dev/null || echo "")"
    if [[ "${section_table}" == "1" ]]; then
      echo "Freezing active sensors until ${FREEZE_UNTIL}..."
      sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" \
        "UPDATE sensors
            SET frozen_until='${FREEZE_UNTIL}',
                frozen_at='${FREEZE_AT}',
                frozen_is_up=COALESCE(
                  (SELECT is_up
                     FROM building_section_power_state s
                    WHERE s.building_id=sensors.building_id
                      AND s.section_id=COALESCE(sensors.section_id, CASE WHEN sensors.building_id=1 THEN 2 ELSE 1 END)
                  ),
                  1
                )
          WHERE is_active=1
            AND (frozen_until IS NULL OR replace(frozen_until,' ','T') < '${FREEZE_AT}');"
    else
      echo "Freezing active sensors until ${FREEZE_UNTIL} (no building_section_power_state table; default frozen_is_up=1)..."
      sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" \
        "UPDATE sensors
            SET frozen_until='${FREEZE_UNTIL}',
                frozen_at='${FREEZE_AT}',
                frozen_is_up=1
          WHERE is_active=1
            AND (frozen_until IS NULL OR replace(frozen_until,' ','T') < '${FREEZE_AT}');"
    fi

    FROZEN_BY_DEPLOY_COUNT="$(sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" "SELECT COUNT(*) FROM sensors WHERE is_active=1 AND frozen_at='${FREEZE_AT}';" 2>/dev/null || echo "0")"
    echo "Frozen by deploy: ${FROZEN_BY_DEPLOY_COUNT} sensor(s)."
  fi
fi

docker compose down
if ! docker compose pull; then
  echo "Warning: docker compose pull failed; continuing with local images built in this run."
fi

if [[ "${MIGRATE}" == "1" ]]; then
  docker compose --profile migrate run --rm migrate
fi

profiles=()
echo "Prod profiles forced: admin + business."
profiles+=(--profile admin --profile business)
if should_enable_adbot "${PROD_DIR}/.env"; then
  ensure_adbot_prod_config "${PROD_DIR}/.env"
  echo "Adbot profile enabled (ADBOT_ENABLED=1)."
  profiles+=(--profile adbot)
else
  echo "Adbot profile disabled (ADBOT_ENABLED!=1)."
fi
docker compose "${profiles[@]}" up -d

docker compose ps

echo "Health check (prod)..."
health_ok=0
for i in {1..60}; do
  if curl -sf --max-time 2 http://127.0.0.1:18081/api/v1/health >/dev/null; then
    health_ok=1
    break
  fi
  sleep 1
done
if [[ "${health_ok}" != "1" ]]; then
  echo "Health check failed (prod)."
  exit 1
fi

SENSOR_API_KEY="$(grep -m1 "^SENSOR_API_KEY=" .env | sed 's/^SENSOR_API_KEY=//')"
if [[ -n "${SENSOR_API_KEY}" ]]; then
  curl -sf --max-time 3 -H "X-API-Key: ${SENSOR_API_KEY}" http://127.0.0.1:18081/api/v1/sensors >/dev/null
fi

echo "Ensuring all prod bot services are running..."
assert_service_running "powerbot"
assert_service_running "adminbot"
assert_service_running "businessbot"
if should_enable_adbot "${PROD_DIR}/.env"; then
  assert_service_running "adbot"
fi

# Unfreeze sensors we froze for this deploy (best-effort).
if [[ -n "${FREEZE_AT}" && "${FROZEN_BY_DEPLOY_COUNT}" != "0" && -f "${PROD_DIR}/state.db" ]]; then
  UP_AT="$(sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" "SELECT strftime('%Y-%m-%dT%H:%M:%S','now','localtime');" 2>/dev/null || echo "")"
  if [[ -n "${UP_AT}" ]]; then
    echo "Waiting for sensors to report heartbeat after restart (max ${DEPLOY_UNFREEZE_WAIT_SEC}s)..."
    reported="0"
    for _ in $(seq 1 "${DEPLOY_UNFREEZE_WAIT_SEC}"); do
      reported="$(sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" \
        "SELECT COUNT(*)
           FROM sensors
          WHERE is_active=1
            AND frozen_at='${FREEZE_AT}'
            AND last_heartbeat IS NOT NULL
            AND replace(last_heartbeat,' ','T') >= '${UP_AT}';" 2>/dev/null || echo "0")"
      if [[ "${reported}" == "${FROZEN_BY_DEPLOY_COUNT}" ]]; then
        break
      fi
      sleep 1
    done
    echo "Unfreezing deploy-frozen sensors (${reported}/${FROZEN_BY_DEPLOY_COUNT} reported)..."
  else
    echo "Unfreezing deploy-frozen sensors (skip wait; failed to read UP_AT)..."
  fi

  sqlite3 -cmd ".timeout 5000" "${PROD_DIR}/state.db" \
    "UPDATE sensors
        SET frozen_until=NULL,
            frozen_is_up=NULL,
            frozen_at=NULL
      WHERE frozen_at='${FREEZE_AT}';" >/dev/null 2>&1 || true
fi

# Optional: mini app health if endpoint exists.
curl -s http://127.0.0.1:18081/api/v1/webapp/health >/dev/null || true

# Log health gate (fail only on bad patterns).
"${REPO_DIR}/scripts/log_health_check.sh" powerbot

echo "Prod deployed."
