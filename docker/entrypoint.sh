#!/usr/bin/env sh
set -e

BOT_ENV_NAME="${BOT_ENV:-prod}"
WORKDIR="/app/${BOT_ENV_NAME}"

if [ ! -d "$WORKDIR" ]; then
  echo "ERROR: env directory not found: $WORKDIR" >&2
  exit 1
fi

cd "$WORKDIR"
exec python main.py
