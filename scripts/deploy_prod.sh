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
if should_enable_business_profile "${PROD_DIR}/.env"; then
  echo "Business profile enabled (BUSINESS_MODE=1 and BUSINESS_BOT_API_KEY is set)."
  profiles+=(--profile business)
else
  echo "Business profile disabled (missing BUSINESS_BOT_API_KEY or BUSINESS_MODE!=1)."
fi
if should_enable_admin_profile "${PROD_DIR}/.env"; then
  echo "Admin profile enabled (ADMIN_BOT_API_KEY is set)."
  profiles+=(--profile admin)
else
  echo "Admin profile disabled (missing ADMIN_BOT_API_KEY)."
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
