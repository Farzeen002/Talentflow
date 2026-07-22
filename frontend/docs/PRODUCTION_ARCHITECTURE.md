# TalentFlow — Production Architecture

**Document purpose:** Infrastructure / DevOps reference for Azure production deployment  
**Project:** Recruitment Automation Platform (TalentFlow)  
**Cloud provider:** Microsoft Azure  
**Last updated:** 2026-07-09

---

## 1. Infrastructure summary

| Item | Specification |
|------|----------------|
| **Cloud** | Microsoft Azure |
| **VM SKU** | Standard_D4s_v5 |
| **CPU** | 4 vCPU |
| **RAM** | 16 GB |
| **Storage** | 64 GB Premium SSD |
| **OS** | Ubuntu Server 22.04 LTS |
| **Public IP** | Required |
| **Frontend domain** | `ats.company.com` (example) |
| **API domain** | `api.company.com` (example) |

### Host software (provisioned on VM)

- Ubuntu Server 22.04 LTS  
- Docker Engine (latest stable)  
- Docker Compose plugin  
- Nginx (latest stable)  

### Application stack (Docker Compose on same server)

| Service | Technology | Notes |
|---------|------------|-------|
| Frontend | React.js SPA | Static build served via Nginx |
| API | **Python FastAPI** | REST API (`/api/v1/*`) |
| Workers | Python background worker(s) | ATS, email, scheduled jobs |
| Database | MongoDB | Primary persistence |
| Cache / queue | Redis | Sessions, cache, job queues |

---

## 2. Primary architecture diagram (Mermaid)

```mermaid
flowchart TB
    Users["Users / Recruiters<br/><b>Web Browser</b>"]

    subgraph Azure["Microsoft Azure"]
        subgraph VM["Azure VM — Standard_D4s_v5<br/>4 vCPU · 16 GB RAM · 64 GB Premium SSD<br/>Ubuntu 22.04 LTS · Public IP"]

            Nginx["Nginx (Host)<br/><b>Reverse Proxy + SSL/TLS</b><br/>:80 HTTP redirect · :443 HTTPS<br/>ats.company.com · api.company.com"]

            subgraph Docker["Docker Engine + Docker Compose"]
                subgraph AppServices["Application Services (Containers)"]
                    direction TB
                    React["React.js Frontend<br/><b>Static SPA build</b><br/>Container"]
                    API["Python FastAPI<br/><b>REST API</b><br/>Container · :8000"]
                    Worker["Background Worker(s)<br/><b>Python async jobs</b><br/>Container"]
                end

                subgraph Databases["Databases & Supporting Services (Containers)"]
                    direction LR
                    MongoDB["MongoDB<br/><b>Primary database</b><br/>:27017 · internal only"]
                    Redis["Redis<br/><b>Cache / queues</b><br/>:6379 · internal only"]
                end
            end
        end
    end

    Users -->|"HTTPS :443"| Nginx
    Nginx -->|"ats.company.com<br/>static assets"| React
    Nginx -->|"api.company.com<br/>/api/v1/*"| API

    API -->|"read / write"| MongoDB
    API -->|"cache · sessions"| Redis
    Worker -->|"consume jobs"| Redis
    Worker -->|"persist results"| MongoDB
    API -.->|"enqueue tasks"| Redis

    classDef user fill:#e8f4fc,stroke:#1e6b9e,stroke-width:2px
    classDef edge fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef app fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef api fill:#ede7f6,stroke:#5e35b1,stroke-width:2px
    classDef data fill:#fce4ec,stroke:#c62828,stroke-width:2px
    classDef azure fill:#f5f5f5,stroke:#0078d4,stroke-width:2px

    class Users user
    class Nginx edge
    class React app
    class API,Worker api
    class MongoDB,Redis data
    class VM,Azure azure
```

---

## 3. PNG-friendly simplified diagram (Mermaid)

Best for slides, email, and infra handoff. Export at **1920×1080**.

```mermaid
flowchart TB
    U["① Users<br/>HTTPS clients"]

    subgraph AZ["Microsoft Azure"]
        subgraph SRV["② VM Standard_D4s_v5 · Ubuntu 22.04"]
            NGX["③ Nginx (host)<br/>SSL/TLS · :443<br/>ats.company.com · api.company.com"]

            subgraph DC["④ Docker Compose — all app services"]
                FE["⑤ React.js<br/>Frontend container"]
                API["⑥ Python FastAPI<br/>REST API container"]
                WRK["⑦ Background worker(s)<br/>Python container"]
                MONGO["⑧ MongoDB<br/>:27017 internal"]
                REDIS["⑨ Redis<br/>:6379 internal"]
            end
        end
    end

    U -->|"HTTPS"| NGX
    NGX -->|"ats.company.com"| FE
    NGX -->|"api.company.com"| API
    API --> MONGO
    API --> REDIS
    WRK --> REDIS
    WRK --> MONGO
```

---

## 4. Component labels (legend)

| # | Component | Role | Exposure |
|---|-----------|------|----------|
| ① | Users | Recruiters access TalentFlow in browser | Internet |
| ② | Azure VM | Single production server hosting all services | Public IP |
| ③ | Nginx | SSL termination, HTTP→HTTPS redirect, domain routing | **:80, :443** (public) |
| ④ | Docker Compose | Orchestrates all application containers | Internal |
| ⑤ | React.js | Recruiter dashboard SPA (`npm run build`) | Via Nginx only |
| ⑥ | Python FastAPI | REST API (`/api/v1/jobs`, `/api/v1/candidates`, etc.) | Via Nginx only |
| ⑦ | Background worker(s) | ATS scoring, email, cron tasks | No public port |
| ⑧ | MongoDB | Jobs, candidates, users, audit data | **:27017 restricted** |
| ⑨ | Redis | Cache, sessions, job queues | **:6379 restricted** |

---

## 5. Network & security

### Public ports (open)

| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH administration |
| 80 | TCP | HTTP → HTTPS redirect |
| 443 | TCP | HTTPS (Nginx SSL termination) |

### Restricted ports (must NOT be public)

| Port | Service | Access |
|------|---------|--------|
| 27017 | MongoDB | Docker internal network / localhost only |
| 6379 | Redis | Docker internal network / localhost only |
| 8000 | FastAPI | Proxied via Nginx only — not exposed to internet |

### Azure Network Security Group (NSG) recommendation

```
Inbound allow:  22, 80, 443  →  Any (or office IP range for SSH)
Inbound deny:   27017, 6379, 8000  →  Internet
```

---

## 6. Domain & SSL configuration

| Domain | Routes to | Example |
|--------|-----------|---------|
| `ats.company.com` | React frontend (static SPA) | `https://ats.company.com` |
| `api.company.com` | FastAPI backend | `https://api.company.com/api/v1/...` |

**SSL/TLS:** Terminated at Nginx using Let's Encrypt (certbot) or corporate CA certificate.

### Nginx routing (reference)

```nginx
# Frontend — ats.company.com
server {
    listen 443 ssl http2;
    server_name ats.company.com;

    ssl_certificate     /etc/letsencrypt/live/ats.company.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ats.company.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:3000;   # React container
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# API — api.company.com
server {
    listen 443 ssl http2;
    server_name api.company.com;

    ssl_certificate     /etc/letsencrypt/live/api.company.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.company.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;   # FastAPI container
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

# HTTP → HTTPS redirect (both domains)
server {
    listen 80;
    server_name ats.company.com api.company.com;
    return 301 https://$host$request_uri;
}
```

---

## 7. Request flow (runtime)

```mermaid
sequenceDiagram
    autonumber
    actor User as User (Browser)
    participant NGX as Nginx :443
    participant FE as React SPA
    participant API as Python FastAPI
    participant DB as MongoDB
    participant RD as Redis
    participant WK as Background Worker

    User->>NGX: HTTPS GET ats.company.com/dashboard
    NGX->>FE: Proxy to frontend container
    FE-->>User: React SPA assets

    User->>NGX: HTTPS GET api.company.com/api/v1/jobs
    NGX->>API: proxy_pass
    API->>RD: Cache lookup (optional)
    API->>DB: Query MongoDB
    DB-->>API: Documents
    API-->>NGX: JSON response
    NGX-->>User: HTTPS 200

    API->>RD: Enqueue background job
    WK->>RD: Consume from queue
    WK->>DB: Update candidate / ATS status
```

---

## 8. Docker Compose service map

```mermaid
flowchart LR
    subgraph Compose["docker-compose.yml"]
        frontend["frontend<br/>React + Nginx"]
        api["api<br/>FastAPI + Uvicorn"]
        worker["worker<br/>Celery / RQ / custom"]
        mongo["mongodb"]
        redis["redis"]
    end

    frontend --- api
    api --- mongo
    api --- redis
    worker --- redis
    worker --- mongo
```

Typical `docker-compose.yml` services:

| Service | Image / build | Internal port | Published to host |
|---------|---------------|---------------|-------------------|
| `frontend` | React build + nginx | 3000 | `127.0.0.1:3000` |
| `api` | FastAPI + Uvicorn | 8000 | `127.0.0.1:8000` |
| `worker` | Same image as API, different CMD | — | None |
| `mongodb` | `mongo:7` | 27017 | None (internal network) |
| `redis` | `redis:7-alpine` | 6379 | None (internal network) |

---

## 9. Access requirements (from infra team)

Please provision and share:

- [ ] Root / sudo access  
- [ ] SSH credentials  
- [ ] Public IP address  
- [ ] DNS A-records: `ats.company.com` → Public IP  
- [ ] DNS A-records: `api.company.com` → Public IP  

---

## 10. Backup & recovery

| Backup type | Frequency | Scope |
|-------------|-----------|-------|
| **MongoDB dump** | Daily | All application collections |
| **Azure VM snapshot** | Weekly | Full disk (OS + Docker volumes) |

Recommended:
- Store MongoDB backups in Azure Blob Storage (geo-redundant)
- Test restore procedure before go-live
- Document RPO/RTO with infra team

---

## 11. Pre-production checklist

- [ ] Azure VM `Standard_D4s_v5` provisioned with public IP  
- [ ] Ubuntu 22.04 LTS, Docker, Docker Compose, Nginx installed  
- [ ] NSG: ports 22/80/443 open; 27017/6379 blocked from internet  
- [ ] DNS records for `ats.company.com` and `api.company.com`  
- [ ] SSL certificates installed on Nginx  
- [ ] `docker compose up -d` — all 5 services healthy  
- [ ] FastAPI `/api/v1/health` returns 200 via `api.company.com`  
- [ ] React app loads via `ats.company.com`  
- [ ] MongoDB daily backup job configured  
- [ ] Weekly Azure snapshot policy enabled  
- [ ] Environment secrets in `.env` (not committed to Git)  

---

## 12. Exporting diagrams to PNG

PNGs are pre-generated in `docs/`:

- `PRODUCTION_ARCHITECTURE.png` — full diagram  
- `PRODUCTION_ARCHITECTURE_SIMPLE.png` — slide-friendly  

Regenerate:

```bash
npx @mermaid-js/mermaid-cli \
  -i docs/PRODUCTION_ARCHITECTURE_SIMPLE.mmd \
  -o docs/PRODUCTION_ARCHITECTURE_SIMPLE.png \
  -w 1920 -H 1080 -b white
```

---

## 13. Related documentation

- `docs/DAILY_UPDATES_API.md` — Daily updates API contract  
- `docs/BLACKLIST_LIST_API_BUG.md` — Blacklist API integration notes  
