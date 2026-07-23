# TalentFlow — OCI Always Free deploy ($0, no domain)

**Goal:** Run the full app on one Always Free Ampere VM and share a client URL like:

`https://<public-ip-with-dashes>.sslip.io`

**Cost rules:** Use only **Always Free Eligible** resources. Never create Load Balancer, Autonomous DB, paid shapes, or extra paid block volumes.

---

## 0. Confirm home region (required — avoids accidental billing)

Always Free Compute is free **only in your tenancy home region**.

1. In OCI Console, open the profile menu (top right) → **Tenancy: farzeen** (or Tenancy details).
2. Note **Home Region**.
3. Set the console region selector (top right) to that **same** home region before creating the VM.
4. If Home Region is **not** India West (Mumbai), create the VM in the home region — do **not** create a paid instance in Mumbai just because the UI was left on Mumbai.

Only continue when the region selector matches Home Region.

---

## 1. Budget safety (recommended)

1. **Billing & Cost Management** → **Budgets** → create a budget with alert at **$1** (or lowest allowed).
2. Confirm you will only select shapes marked **Always Free Eligible**.

---

## 2. Network (free)

If you have no VCN yet:

1. **Networking** → **Virtual cloud networks** → **Start VCN Wizard**.
2. Create VCN with Internet Connectivity (default).
3. Finish — no load balancer.

Ensure the subnet’s NSG / security list allows ingress:

| Port | Source | Purpose |
|------|--------|---------|
| 22 | Your IP (preferred) or `0.0.0.0/0` | SSH |
| 80 | `0.0.0.0/0` | HTTP + Certbot |
| 443 | `0.0.0.0/0` | HTTPS |

**Do not** open 27017, 6379, or 8000 to the internet.

---

## 3. Create Always Free VM

1. **Compute** → **Instances** → **Create instance**.
2. Name: `talentflow-free` (or similar).
3. **Placement:** any AD in the **home** region.
4. **Image:** Canonical Ubuntu **22.04** (aarch64 / ARM).
5. **Shape:** click **Change shape** → filter or select **Ampere** → `VM.Standard.A1.Flex`.
   - OCPUs: **2**
   - Memory: **12 GB**
   - Confirm the shape shows **Always Free Eligible**.
6. **Networking:** public subnet, **Assign a public IPv4 address** (ephemeral — free).
7. **SSH keys:** generate or upload your public key. Save the private key locally.
8. **Boot volume:** **50–100 GB** (stay under the ~200 GB Always Free storage pool).
9. **Create**. If you see **Out of capacity**, retry another AD or try again later — **do not** switch to a paid shape.

### After create

1. Copy the instance **Public IP** (example `129.146.1.2`).
2. Build the free hostname (dots → dashes + `.sslip.io`):

   `129.146.1.2` → `129-146-1-2.sslip.io`

3. Client URL will be: `https://129-146-1-2.sslip.io`

---

## 4. SSH into the VM

From your laptop (PowerShell or Git Bash), using the private key you saved:

```bash
ssh -i /path/to/private_key ubuntu@PUBLIC_IP
```

Default user for Canonical Ubuntu images is usually `ubuntu`.

---

## 5. Install host software

Copy the repo to the VM (from your laptop, repo root):

```bash
# Example: rsync (Git Bash / WSL). Exclude bulky local folders.
rsync -avz -e "ssh -i /path/to/private_key" \
  --exclude node_modules --exclude .git --exclude frontend/dist \
  --exclude backend/.venv --exclude '**/__pycache__' \
  ./ ubuntu@PUBLIC_IP:~/talentflow/
```

PowerShell (`scp` recursive; slower, includes more files):

```powershell
scp -i $HOME\.ssh\your_oci_key -r D:\Infomatics\Talentflow ubuntu@PUBLIC_IP`:~/talentflow
```

Short checklist: [deploy/oci/README.md](../../deploy/oci/README.md).

On the VM:

```bash
cd ~/talentflow
chmod +x deploy/oci/*.sh
./deploy/oci/setup-vm.sh
# re-login or:
newgrp docker
```

---

## 6. Configure environment

On the VM:

```bash
cd ~/talentflow
HOST="YOUR-IP-DASHES.sslip.io"   # e.g. 129-146-1-2.sslip.io

cp deploy/oci/env.production.example backend/.env
# Replace HOST_PLACEHOLDER with $HOST everywhere in backend/.env
sed -i "s/HOST_PLACEHOLDER/${HOST}/g" backend/.env

# Paste real secrets into backend/.env:
# JWT_SECRET_KEY, FERNET_KEY, INTERNAL_API_TOKEN,
# GOOGLE_*, MICROSOFT_*, OPENAI_API_KEY, etc.
nano backend/.env

cp deploy/oci/compose.env.example .env
# Default VITE_BASE_URL=/api/v1 (same-origin) is recommended.
# Only replace HOST_PLACEHOLDER if you switched to an absolute URL in that file.
```

Generate secrets if needed:

```bash
openssl rand -hex 32
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 7. Start the app

```bash
cd ~/talentflow
./deploy/oci/deploy-app.sh
```

Smoke tests on the VM:

```bash
curl -sS http://127.0.0.1:8000/
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/
docker compose -f docker-compose.prod.yml ps
```

---

## 8. Nginx + HTTPS (sslip.io)

```bash
cd ~/talentflow
HOST="YOUR-IP-DASHES.sslip.io"
./deploy/oci/install-nginx-site.sh "$HOST"

# Issue Let's Encrypt cert (needs ports 80/443 open in NSG + UFW)
sudo certbot --nginx -d "$HOST"
```

Open in a browser:

`https://YOUR-IP-DASHES.sslip.io`

That is the **client live link**.

---

## 9. OAuth redirect URIs (required for login)

In Google Cloud Console and Azure App Registration, add:

- Google: `https://YOUR-IP-DASHES.sslip.io/api/v1/auth/callback`
- Microsoft: `https://YOUR-IP-DASHES.sslip.io/api/v1/auth/microsoft/callback`

These must match `GOOGLE_REDIRECT_URI` / `MICROSOFT_REDIRECT_URI` in `backend/.env`. Then:

```bash
cd ~/talentflow
docker compose -f docker-compose.prod.yml up -d --force-recreate backend
```

---

## 10. Files added for this deploy path

| Path | Role |
|------|------|
| [docker-compose.prod.yml](../../docker-compose.prod.yml) | Prod stack: Mongo, Redis, API, 2 workers, static frontend |
| [frontend/Dockerfile.prod](../Dockerfile.prod) | Multi-stage Vite build + Nginx |
| [frontend/nginx.prod.conf](../nginx.prod.conf) | SPA routing inside frontend container |
| [deploy/nginx/talentflow.conf](../../deploy/nginx/talentflow.conf) | Host reverse proxy template |
| [deploy/oci/setup-vm.sh](../../deploy/oci/setup-vm.sh) | Docker, Nginx, Certbot, UFW |
| [deploy/oci/install-nginx-site.sh](../../deploy/oci/install-nginx-site.sh) | Enable site with sslip.io hostname |
| [deploy/oci/deploy-app.sh](../../deploy/oci/deploy-app.sh) | `compose up --build` |
| [deploy/oci/env.production.example](../../deploy/oci/env.production.example) | Backend env template |
| [deploy/oci/compose.env.example](../../deploy/oci/compose.env.example) | `VITE_BASE_URL` for image build |

Local development continues to use root `docker-compose.yml` unchanged.

---

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| Out of capacity on create | Retry AD / later; never pick a paid shape |
| Certbot fails | NSG + UFW allow 80/443; hostname is `ip-with-dashes.sslip.io` |
| Frontend loads but API fails | Rebuild frontend after setting `VITE_BASE_URL`; check Nginx `/api/` proxy |
| OAuth error redirect_uri_mismatch | Console URIs must exactly match backend `.env` |
| OOM / containers restarting | `docker stats`; keep `APP_ENV=staging` and merged workers; avoid raising memory limits past ~12 GB host |

---

## Hand-off checklist

- [ ] Home region confirmed; VM is Always Free A1 2 OCPU / 12 GB
- [ ] `https://<ip-dashes>.sslip.io` loads the UI over HTTPS
- [ ] API responds via `https://<ip-dashes>.sslip.io/api/...` (or root health on backend)
- [ ] OAuth redirect URIs updated
- [ ] Client given the HTTPS sslip.io URL only (not raw IP over HTTP)
