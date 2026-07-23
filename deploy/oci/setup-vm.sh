#!/usr/bin/env bash
# Install Docker, Compose plugin, Nginx, Certbot, and firewall rules on Ubuntu aarch64 (OCI Ampere).
# Run as a sudo-capable user on the VM:
#   chmod +x deploy/oci/setup-vm.sh && ./deploy/oci/setup-vm.sh

set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as a normal user with sudo (not as root)."
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "==> Updating apt"
sudo apt-get update -y
sudo apt-get upgrade -y

echo "==> Installing base packages"
sudo apt-get install -y \
  ca-certificates \
  curl \
  gnupg \
  lsb-release \
  ufw \
  nginx \
  certbot \
  python3-certbot-nginx \
  git \
  rsync

echo "==> Installing Docker Engine"
if ! command -v docker >/dev/null 2>&1; then
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  # shellcheck disable=SC1091
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

echo "==> Adding ${USER} to docker group"
sudo usermod -aG docker "${USER}" || true

echo "==> Configuring UFW (22/80/443 only)"
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status

echo "==> Enabling Nginx"
sudo systemctl enable --now nginx

echo "==> Done."
echo ""
echo "Next steps:"
echo "  1. Log out and back in (or: newgrp docker) so docker works without sudo."
echo "  2. Copy the app to ~/talentflow (or your chosen path)."
echo "  3. Configure backend/.env and export VITE_BASE_URL for the build."
echo "  4. Install Nginx site from deploy/nginx/talentflow.conf (replace TALENTFLOW_HOST)."
echo "  5. docker compose -f docker-compose.prod.yml up -d --build"
echo "  6. sudo certbot --nginx -d <ip-dashes>.sslip.io"
