#!/usr/bin/env sh
set -e

WORKDIR="/app/src"
DB_PATH="${DB_PATH:-/data/state.db}"
SCHEMA_PATH="/app/schema.sql"

if [ ! -d "$WORKDIR" ]; then
  echo "ERROR: env directory not found: $WORKDIR" >&2
  exit 1
fi

export DB_PATH

mkdir -p "$(dirname "$DB_PATH")"
if [ ! -f "$DB_PATH" ]; then
  if [ -f "$SCHEMA_PATH" ]; then
    echo "Initializing DB at $DB_PATH"
    sqlite3 "$DB_PATH" < "$SCHEMA_PATH"
  else
    echo "ERROR: schema.sql not found at $SCHEMA_PATH" >&2
    exit 1
  fi
fi

cd "$WORKDIR"
exec python main.py
