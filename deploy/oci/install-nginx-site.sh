#!/usr/bin/env bash
# Install / refresh the host Nginx site for TalentFlow.
# Usage:
#   ./deploy/oci/install-nginx-site.sh 129-146-1-2.sslip.io
# Run from the repo root on the VM.

set -euo pipefail

HOST="${1:-}"
if [[ -z "${HOST}" ]]; then
  echo "Usage: $0 <sslip.io-hostname>"
  echo "Example: $0 129-146-1-2.sslip.io"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC="${ROOT_DIR}/deploy/nginx/talentflow.conf"
DEST="/etc/nginx/sites-available/talentflow"

if [[ ! -f "${SRC}" ]]; then
  echo "Missing ${SRC}"
  exit 1
fi

TMP="$(mktemp)"
sed "s/TALENTFLOW_HOST/${HOST}/g" "${SRC}" > "${TMP}"
sudo cp "${TMP}" "${DEST}"
rm -f "${TMP}"

sudo ln -sfn "${DEST}" /etc/nginx/sites-enabled/talentflow
# Disable default site if present (avoids conflicting server_name _).
if [[ -L /etc/nginx/sites-enabled/default ]]; then
  sudo rm -f /etc/nginx/sites-enabled/default
fi

sudo nginx -t
sudo systemctl reload nginx
echo "Nginx site installed for ${HOST}"
echo "After containers are up, run:"
echo "  sudo certbot --nginx -d ${HOST}"
