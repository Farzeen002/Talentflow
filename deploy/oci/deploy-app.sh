#!/usr/bin/env bash
# Build and start TalentFlow production stack on the OCI VM.
# Usage (from repo root on the VM):
#   export VITE_BASE_URL=https://129-146-1-2.sslip.io/api/v1
#   ./deploy/oci/deploy-app.sh
#
# Or place VITE_BASE_URL in a root `.env` file (see deploy/oci/compose.env.example).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT_DIR}"

if [[ -z "${VITE_BASE_URL:-}" ]]; then
  if [[ -f .env ]] && grep -q '^VITE_BASE_URL=' .env; then
    # shellcheck disable=SC1091
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
  fi
fi

if [[ -z "${VITE_BASE_URL:-}" ]]; then
  echo "ERROR: Set VITE_BASE_URL (recommended: /api/v1 for same-origin Nginx proxy)"
  echo "Example: export VITE_BASE_URL=/api/v1"
  exit 1
fi

if [[ ! -f backend/.env ]]; then
  echo "ERROR: Missing backend/.env — copy deploy/oci/env.production.example and fill secrets."
  exit 1
fi

echo "==> Building and starting stack (VITE_BASE_URL=${VITE_BASE_URL})"
docker compose -f docker-compose.prod.yml up -d --build

echo "==> Container status"
docker compose -f docker-compose.prod.yml ps

echo ""
echo "Smoke checks (on the VM):"
echo "  curl -sS http://127.0.0.1:8000/ | head"
echo "  curl -sS -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:3000/"
