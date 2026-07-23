#!/bin/sh
# Single free-tier Render process: local Redis + RQ workers + API.
set -eu

PORT="${PORT:-10000}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export REDIS_URL

echo "Starting embedded Redis..."
redis-server --daemonize yes --bind 127.0.0.1 --port 6379 --save "" --appendonly no
# Wait until Redis accepts connections
i=0
while [ "$i" -lt 30 ]; do
  if redis-cli -h 127.0.0.1 -p 6379 ping 2>/dev/null | grep -q PONG; then
    echo "Redis is ready"
    break
  fi
  i=$((i + 1))
  sleep 0.2
done

echo "Starting RQ worker (all queues)..."
rq worker default high_priority low_priority resume-preprocessing jd-analysis ats-scoring --url "$REDIS_URL" &
WORKER_PID=$!

cleanup() {
  echo "Shutting down worker pid=$WORKER_PID"
  kill "$WORKER_PID" 2>/dev/null || true
  redis-cli shutdown nosave 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting API on port $PORT..."
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips='*'
