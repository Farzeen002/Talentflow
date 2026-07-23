#!/bin/sh
# Single free-tier Render process: API + RQ workers together.
set -eu

PORT="${PORT:-10000}"

echo "Starting RQ worker (all queues)..."
rq worker default high_priority low_priority resume-preprocessing jd-analysis ats-scoring --url "$REDIS_URL" &
WORKER_PID=$!

cleanup() {
  echo "Shutting down worker pid=$WORKER_PID"
  kill "$WORKER_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting API on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*'
