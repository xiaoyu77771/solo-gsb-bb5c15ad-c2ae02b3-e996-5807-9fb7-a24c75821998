#!/bin/sh
# Container entrypoint: dependency installation is already done in the image;
# only process startup happens here.
set -e

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
WORKERS="${WORKERS:-1}"

echo "Starting desensitization API on ${HOST}:${PORT} (workers=${WORKERS})"
exec uvicorn app.main:app --host "$HOST" --port "$PORT" --workers "$WORKERS"
