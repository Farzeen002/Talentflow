# TalentFlow FREE Deployment (Netlify + Render + Atlas)

This guide gets TalentFlow publicly live over HTTPS with free tiers and minimal code changes.

## Architecture

- Frontend: Netlify (static Vite build)
- Backend API: Render Web Service (Docker)
- Background workers: Render Workers (RQ queues)
- Database: MongoDB Atlas Free (M0)
- Redis queue: Upstash Redis Free (required for RQ/scheduler)
- Resume file persistence: Google Cloud Storage bucket (uses existing `STORAGE_PROVIDER=gcs`)

## Why this is the fastest free setup

- No VM setup, no SSL config, no domain needed.
- Netlify + Render provide HTTPS URLs automatically.
- Existing code already supports Mongo Atlas and GCS.
- Existing worker architecture maps directly to Render workers.

## 1) MongoDB Atlas (Free)

1. Create an M0 cluster.
2. Create DB user and password.
3. Allow network access from anywhere (`0.0.0.0/0`) for demo speed.
4. Copy connection string:

```bash
mongodb+srv://<user>:<password>@<cluster>/<db>?retryWrites=true&w=majority
```

## 2) Upstash Redis (Free)

1. Create a Redis database in [Upstash](https://upstash.com/).
2. Copy TLS Redis URL (`rediss://...`).
3. Use this as `REDIS_URL` in Render services.

## 3) Google Cloud Storage (Free usage tier)

1. Create a GCS bucket for resumes.
2. Create a service account with storage object permissions for this bucket.
3. Generate key JSON and copy values into Render env vars:
   - `GCP_PROJECT_ID`
   - `GCP_PRIVATE_KEY_ID`
   - `GCP_PRIVATE_KEY` (keep `\n` newlines escaped)
   - `GCP_CLIENT_EMAIL`
   - `GCP_CLIENT_ID`
4. Set:
   - `STORAGE_PROVIDER=gcs`
   - `GCS_BUCKET_NAME=<your_bucket>`

## 4) Deploy Backend + Workers on Render

1. Push this repo to GitHub.
2. In Render, create a Blueprint using `render.yaml` (repo root).
3. It creates:
   - `talentflow-api` (web)
   - `talentflow-queue-worker`
   - `talentflow-analysis-worker`
4. Add all required env vars (from `.env.example`) to all Render services.
5. Wait for deploy success.
6. Note backend URL:

```text
https://your-render-service.onrender.com
```

7. Verify API:

```bash
curl https://your-render-service.onrender.com/health
```

## 5) Deploy Frontend on Netlify

1. Import the same GitHub repo into Netlify.
2. Netlify uses `netlify.toml` automatically.
3. Set env var in Netlify:

```text
VITE_BASE_URL=https://your-render-service.onrender.com/api/v1
```

4. Deploy and note frontend URL:

```text
https://your-site-name.netlify.app
```

## 6) Final production env values

Set these in Render after Netlify URL is known:

- `FRONTEND_URL=https://your-site-name.netlify.app`
- `CORS_ORIGINS=https://your-site-name.netlify.app`
- `APP_PUBLIC_URL=https://your-render-service.onrender.com`

## 7) OAuth Redirect URIs (exact values)

Use these exact patterns once URLs are final:

- Google OAuth redirect URI:
  - `https://your-render-service.onrender.com/api/v1/auth/callback`
- Microsoft OAuth redirect URI:
  - `https://your-render-service.onrender.com/api/v1/auth/microsoft/callback`

These must exactly match:

- `GOOGLE_REDIRECT_URI`
- `MICROSOFT_REDIRECT_URI`

## 8) Deployment Commands

### Local verification before pushing

```bash
# From repo root
cd frontend
npm ci
npm run build

cd ../backend
python -m pip install -r requirements.txt
python -m compileall app
```

### Git commands

```bash
cd D:/Infomatics/Talentflow
git add .
git commit -m "Prepare free-tier deployment for Netlify, Render, and Atlas"
git push origin <your-branch>
```

### Netlify CLI (optional alternative)

```bash
npm install -g netlify-cli
netlify login
netlify init
netlify env:set VITE_BASE_URL https://your-render-service.onrender.com/api/v1
netlify deploy --build --prod
```

### Render deploy trigger

Render deploys automatically after push when connected to your repo.

## 9) Important free-tier notes

- Render free services can spin down when idle; first request can be slow.
- Keep one recruiter account active before demos to warm services.
- Atlas and GCS keep data persistent.
- Never use local filesystem storage in Render for resumes (ephemeral).
