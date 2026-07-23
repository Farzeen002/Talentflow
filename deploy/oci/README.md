# OCI Always Free — quick start

Full checklist: [frontend/docs/OCI_FREE_TIER_DEPLOY.md](../../frontend/docs/OCI_FREE_TIER_DEPLOY.md)

## On your laptop (after VM exists)

1. Confirm **Home Region** matches the console region (section 0 of the full guide).
2. Create **Always Free** `VM.Standard.A1.Flex` — **2 OCPU / 12 GB**, Ubuntu 22.04 aarch64, public IP, boot ≤ 100 GB.
3. Note public IP → hostname `A-B-C-D.sslip.io` (dots to dashes).

```powershell
# PowerShell example — sync repo to VM (adjust paths)
scp -i $HOME\.ssh\oci_talentflow_key -r `
  D:\Infomatics\Talentflow\ubuntu@PUBLIC_IP`:~/talentflow-upload
```

Prefer `rsync` from WSL/Git Bash (excludes `node_modules`).

## On the VM

```bash
cd ~/talentflow   # or move upload into place
chmod +x deploy/oci/*.sh
./deploy/oci/setup-vm.sh
newgrp docker

HOST="A-B-C-D.sslip.io"
cp deploy/oci/env.production.example backend/.env
sed -i "s/HOST_PLACEHOLDER/${HOST}/g" backend/.env
# edit backend/.env — paste secrets
cp deploy/oci/compose.env.example .env
# Keep VITE_BASE_URL=/api/v1 unless you need an absolute API URL

./deploy/oci/deploy-app.sh
./deploy/oci/install-nginx-site.sh "$HOST"
sudo certbot --nginx -d "$HOST"
```

## Client link

`https://A-B-C-D.sslip.io`

Update Google/Microsoft OAuth redirect URIs to that host (see full guide §9).
