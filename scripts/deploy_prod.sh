#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKERHUB_USER="${DOCKERHUB_USER:-semorgana}"
VERSION="${VERSION:-$(date +%Y.%m.%d-%H%M)}"
MIGRATE="${MIGRATE:-0}"
PROD_DIR="/opt/powerbot"

if [[ -n "${DOCKERHUB_USERNAME:-}" && -n "${DOCKERHUB_TOKEN:-}" ]]; then
  echo "Logging in to Docker Hub..."
  echo "${DOCKERHUB_TOKEN}" | docker login -u "${DOCKERHUB_USERNAME}" --password-stdin
fi

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

echo "Disabling light notifications in DB before deploy..."
if [[ -f "${PROD_DIR}/state.db" ]]; then
  sqlite3 "${PROD_DIR}/state.db" "INSERT OR REPLACE INTO kv (k, v) VALUES ('light_notifications_global', 'off');"
fi

cd "${PROD_DIR}"
docker compose down
docker compose pull
if [[ "${MIGRATE}" == "1" ]]; then
  docker compose --profile migrate run --rm migrate
fi
docker compose up -d

echo "Health check (prod)..."
curl -s http://new-england.morgan-dev.com:18081/api/v1/health >/dev/null

SENSOR_API_KEY="$(grep -m1 "^SENSOR_API_KEY=" .env | sed 's/^SENSOR_API_KEY=//')"
if [[ -n "${SENSOR_API_KEY}" ]]; then
  curl -s -H "X-API-Key: ${SENSOR_API_KEY}" http://new-england.morgan-dev.com:18081/api/v1/sensors >/dev/null
fi

docker compose ps
echo "Prod deployed. Light notifications remain OFF until you enable them."
