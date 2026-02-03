#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERHUB_USER="${DOCKERHUB_USER:-semorgana}"
VERSION="${VERSION:-$(date +%Y.%m.%d-%H%M)}"
MIGRATE="${MIGRATE:-0}"
TEST_DIR="/opt/powerbot-test"

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

cd "${TEST_DIR}"
docker compose down
docker compose pull
if [[ "${MIGRATE}" == "1" ]]; then
  docker compose --profile migrate run --rm migrate
fi
docker compose up -d

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

# Optional: mini app health if endpoint exists.
curl -s http://127.0.0.1:18082/api/v1/webapp/health >/dev/null || true

# Log health gate (fail only on bad patterns).
"${REPO_DIR}/scripts/log_health_check.sh" powerbot
