#!/usr/bin/env bash
# Start the web app. Usage: ./run.sh [port]   (binds 0.0.0.0 for the Tailscale preview)
cd "$(dirname "$0")"
PORT="${1:-4510}"
export MP_PUBLIC_URL="${MP_PUBLIC_URL:-http://100.99.49.120:$PORT}"
[ -f .env ] && set -a && . ./.env && set +a
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
