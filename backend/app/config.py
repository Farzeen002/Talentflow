"""
app/config.py

Application configuration using Pydantic BaseSettings.
All values are loaded from environment variables or a .env file.

Supports Gmail (Google OAuth) and Outlook (Microsoft OAuth) email providers.

Production contract: ADR-0001 (docs/adr/ADR-0001-production-architecture.md).
  - Microsoft Graph OAuth is required when APP_ENV=production.
  - Google OAuth settings are optional (Microsoft-only production is valid).
  - Redis, GCS, and INTERNAL_API_TOKEN are required when APP_ENV=production.
"""

# ─────────────────────────────────────────────────────────────────────────────
# NOTE: LLM settings (OPENAI_API_KEY, etc.) are Optional so the application
# starts cleanly without them.  The worker (app/workers/jd_tasks.py) will
# log a clear error and skip analysis if the key is absent.
# ─────────────────────────────────────────────────────────────────────────────

from functools import lru_cache
from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration object for the application.

    Required core secrets (JWT, Fernet, Mongo) must be present in every
    environment. Provider-specific and production-only constraints are
    enforced by ``_validate_settings``.
    """

    # ── Application ──────────────────────────────────────────────────────────
    APP_ENV: str = "development"

    # Browser-facing SPA origin (CORS + OAuth success redirects).
    # Production: set to the public HTTPS origin (IP, OCI hostname, or domain).
    FRONTEND_URL: str = "http://localhost:3000"

    # Public host used by operators and TLS bootstrap (IP or hostname, no scheme).
    # Example: ``203.0.113.10`` or ``talentflow.example.com``.
    APP_PUBLIC_HOST: Optional[str] = None

    # Full public HTTPS origin for the single-domain deployment.
    # Example: ``https://203.0.113.10``. Derived from APP_PUBLIC_HOST when omitted.
    APP_PUBLIC_URL: Optional[str] = None

    # Comma-separated CORS allowlist override.
    # When unset, origins are derived from FRONTEND_URL and APP_PUBLIC_URL.
    # Wildcard ``*`` is never produced by ``cors_allow_origins`` (ADR-0001 D-SEC-06).
    CORS_ORIGINS: Optional[str] = None

    # Shared secret for ``/api/v1/internal/*`` (header auth). Required in production.
    INTERNAL_API_TOKEN: Optional[str] = None

    # ── MongoDB ───────────────────────────────────────────────────────────────
    MONGODB_URL: str
    MONGODB_DB_NAME: str

    # ── Redis ─────────────────────────────────────────────────────────────────
    # Optional in development (scheduler/RQ degrade). Required when APP_ENV=production.
    REDIS_URL: Optional[str] = None

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    # ── Google OAuth ──────────────────────────────────────────────────────────
    # Optional — Microsoft-only deployments omit these. Google login/ingestion
    # paths fail at use-time if invoked without configuration.
    GOOGLE_CLIENT_ID: Optional[str] = None
    GOOGLE_CLIENT_SECRET: Optional[str] = None
    GOOGLE_REDIRECT_URI: Optional[str] = None

    # ── Encryption ────────────────────────────────────────────────────────────
    FERNET_KEY: str

    # ── Gmail HTTP timeouts ───────────────────────────────────────────────────
    GMAIL_CONNECT_TIMEOUT: float = 10.0   # seconds to establish TCP connection
    GMAIL_READ_TIMEOUT:    float = 30.0   # seconds to wait for response body

    # ── Storage ───────────────────────────────────────────────────────────────
    # Active backend: "local" (default) or "gcs".
    # Production (ADR-0001): must be ``gcs``.
    STORAGE_PROVIDER:   str           = "local"
    # Absolute path for local filesystem storage (forward slashes on Windows).
    # Path(LOCAL_STORAGE_ROOT).resolve() is called internally — never prefix
    # this value with BASE_DIR or the project root in application code.
    # Example: D:/infomatics_project/Automation_resumes
    LOCAL_STORAGE_ROOT: str           = "./storage"
    # GCS bucket name — required only when STORAGE_PROVIDER=gcs.
    GCS_BUCKET_NAME:    Optional[str] = None

    # ── Google Cloud Storage — Service Account Credentials ───────────────────
    # Supply these when STORAGE_PROVIDER=gcs to authenticate without mounting
    # a GOOGLE_APPLICATION_CREDENTIALS JSON file.
    # All fields are Optional so the app starts cleanly with STORAGE_PROVIDER=local.
    #
    # GCP_PRIVATE_KEY must use \n (literal backslash-n) in the .env file;
    # the credential builder restores real newlines at runtime.
    GCP_PROJECT_ID:     Optional[str] = None
    GCP_PRIVATE_KEY_ID: Optional[str] = None
    GCP_PRIVATE_KEY:    Optional[str] = None   # store \n in .env; decoded at runtime
    GCP_CLIENT_EMAIL:   Optional[str] = None
    GCP_CLIENT_ID:      Optional[str] = None

    # ── LLM ───────────────────────────────────────────────────────────────────
    # Provider selection — "openai" is the only implemented value today.
    # Future values: gemini | claude | azure_openai
    LLM_PROVIDER: str = "openai"

    # OpenAI API key — required for JD analysis.  Optional here so the
    # FastAPI process starts without it; the worker validates at task time.
    OPENAI_API_KEY: Optional[str] = None

    # Model passed to the OpenAI chat completion API.
    OPENAI_MODEL: str = "gpt-4o-mini"

    # Per-request network timeout in seconds.  LLM responses can be slow.
    OPENAI_REQUEST_TIMEOUT: float = 30.0

    # Upper bound on completion tokens returned by the LLM (cost control).
    LLM_MAX_TOKENS: int = 2000

    # Maximum retry attempts on transient LLM failures (tenacity).
    LLM_MAX_RETRIES: int = 3

    # ── Stale processing guards ───────────────────────────────────────────────
    # Intentionally SEPARATE keys: JD analysis and ATS scoring have completely
    # different runtime profiles and must be tuned independently.
    #
    #   JD analysis  → single LLM call, completes in ~5–15 seconds total.
    #   ATS scoring  → one LLM call PER candidate, runtime scales with batch.
    #
    # JD stale guard: minutes to wait before reclaiming a crashed JD task.
    # Must satisfy: JD_STALE_PROCESSING_MINUTES > OPENAI_REQUEST_TIMEOUT
    #               * LLM_MAX_RETRIES / 60  (~2 min with default settings).
    # Kept aggressive (10 min) so JD crash-recovery is fast.
    JD_STALE_PROCESSING_MINUTES: int = 10

    # RQ job timeout (seconds) for a single ATS scoring run.
    # The RQ worker kills any job that exceeds this limit.
    # Formula: max_candidates × avg_llm_seconds_per_candidate × safety_factor
    # 5400s (90 min) → covers ~300 candidates at 10s avg with LLM spike room.
    # Real-world OpenAI latency can spike to 15–20s under API load —
    # a naive 8s-average calculation underestimates real production runtime.
    # To support 500+ candidates, increase this AND ATS_STALE_PROCESSING_MINUTES
    # together (the model_validator below enforces the relationship).
    ATS_JOB_TIMEOUT_SECONDS: int = 5400

    # ATS stale guard: minutes to wait before reclaiming a crashed ATS task.
    # INVARIANT (enforced by _validate_settings below):
    #   ATS_STALE_PROCESSING_MINUTES  >  ATS_JOB_TIMEOUT_SECONDS / 60
    # The stale guard MUST NOT fire while a job is still legitimately running.
    # Violating this causes silent duplicate scoring — two workers race on the
    # same batch.  The validator turns this into a startup crash instead.
    # Default: 105 min  (= 5400 / 60 + 15 min safety buffer)
    ATS_STALE_PROCESSING_MINUTES: int = 105

    # ── Microsoft OAuth 2.0 (Outlook provider) ────────────────────────────────
    # Optional in development — absent values disable the Outlook login flow.
    # Required when APP_ENV=production (ADR-0001 D-APP-05).
    #
    # This application is configured as SINGLE-TENANT.
    # All recruiters authenticate with their @infomaticscorp.com Microsoft
    # accounts (Azure AD / Microsoft 365).  Single-tenant is safer and
    # simpler than multi-tenant — only accounts from your own Azure AD
    # directory can complete the OAuth flow.
    #
    # Registration steps (Azure Portal):
    #   1. App Registrations → New Registration
    #   2. Supported account types:
    #      "Accounts in this organizational directory only (Single tenant)"
    #   3. Redirect URI (Web): https://<APP_PUBLIC_HOST>/api/v1/auth/microsoft/callback
    #   4. API Permissions → Microsoft Graph → Delegated:
    #      openid, email, profile, offline_access, Mail.Read, Mail.Send
    #
    # How to find your Tenant ID:
    #   Azure Portal → Azure Active Directory → Overview → Tenant ID
    #   (Format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
    MICROSOFT_TENANT_ID:     Optional[str] = None   # Azure AD Tenant ID (Directory ID)
    MICROSOFT_CLIENT_ID:     Optional[str] = None   # App Registration → Application (client) ID
    MICROSOFT_CLIENT_SECRET: Optional[str] = None   # App Registration → Certificates & secrets
    MICROSOFT_REDIRECT_URI:  Optional[str] = None   # Must match exactly what is registered in Azure

    # ── Outlook NVite folder ──────────────────────────────────────────────────
    # Display name of the Outlook folder that contains NVite candidate emails.
    # Recruiters must create this folder and set up an Outlook rule to route
    # NVite emails into it.  The folder ID is resolved and cached at runtime.
    #
    # Change this value (and restart) if the folder is renamed — no code deploy
    # required.  Not stored in the database; not per-recruiter.
    OUTLOOK_NVITE_FOLDER: str = "Nvite"

    # Page size for Graph delta pagination (Prefer: odata.maxpagesize).
    OUTLOOK_DELTA_PAGE_SIZE: int = 50

    # Maximum wall-clock seconds to retry a single delta page on HTTP 429
    # before aborting the round (delta link is not advanced).
    OUTLOOK_DELTA_MAX_RETRY_SECONDS: float = 300.0

    # ── Daily Reports ─────────────────────────────────────────────────────────
    # Business-day timezone for ``report_date`` validation and lookback
    # (IANA name, e.g. Asia/Kolkata). Not used for UTC audit timestamps.
    REPORT_TZ: str = "Asia/Kolkata"

    # Max calendar days before today that may be opened/created (today always
    # allowed). Phase 1 uses calendar days, not working-day calendars.
    # Example: 2 → today, yesterday, and the day before yesterday.
    REPORT_DATE_LOOKBACK_DAYS: int = 2

    # Default To/CC by report_kind — comma-separated email lists.
    # Loaded into a new draft's working ``recipients``; never stored as global
    # user prefs in Mongo. Leave empty until ops supplies real addresses.
    # Users may override To/CC on the draft before submit; the submitted
    # report freezes an immutable recipients_snapshot.
    REPORT_RECRUITER_DEFAULT_TO: str = ""
    REPORT_RECRUITER_DEFAULT_CC: str = ""
    REPORT_LEAD_DEFAULT_TO: str = ""
    REPORT_LEAD_DEFAULT_CC: str = ""

    # Subject templates per kind. Placeholders:
    #   {report_kind}  {recruiter_name}  {report_date}
    REPORT_RECRUITER_SUBJECT_TEMPLATE: str = (
        "Daily Report (recruiter) — {recruiter_name} — {report_date}"
    )
    REPORT_LEAD_SUBJECT_TEMPLATE: str = (
        "Daily Report (lead) — {recruiter_name} — {report_date}"
    )

    # Max ``limit`` accepted by GET /reports list pagination.
    REPORT_LIST_MAX_LIMIT: int = 100

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # ── Derived helpers ───────────────────────────────────────────────────────

    @property
    def is_production(self) -> bool:
        """True when ``APP_ENV`` is ``production`` (case-insensitive)."""
        return self.APP_ENV.strip().lower() == "production"

    @property
    def google_oauth_configured(self) -> bool:
        """True when all Google OAuth client settings are present."""
        return bool(
            (self.GOOGLE_CLIENT_ID or "").strip()
            and (self.GOOGLE_CLIENT_SECRET or "").strip()
            and (self.GOOGLE_REDIRECT_URI or "").strip()
        )

    @property
    def microsoft_oauth_configured(self) -> bool:
        """True when all Microsoft OAuth client settings are present."""
        return bool(
            (self.MICROSOFT_TENANT_ID or "").strip()
            and (self.MICROSOFT_CLIENT_ID or "").strip()
            and (self.MICROSOFT_CLIENT_SECRET or "").strip()
            and (self.MICROSOFT_REDIRECT_URI or "").strip()
        )

    @property
    def cors_allow_origins(self) -> list[str]:
        """
        Explicit CORS allowlist for FastAPI CORSMiddleware.

        Never returns ``*``. Production and development both use concrete origins
        derived from ``CORS_ORIGINS`` or from ``FRONTEND_URL`` / ``APP_PUBLIC_URL``.
        """
        if self.CORS_ORIGINS and self.CORS_ORIGINS.strip():
            parsed = [
                origin.strip().rstrip("/")
                for origin in self.CORS_ORIGINS.split(",")
                if origin.strip() and origin.strip() != "*"
            ]
            if parsed:
                return parsed

        origins: list[str] = []
        for candidate in (self.FRONTEND_URL, self.APP_PUBLIC_URL):
            if not candidate or not candidate.strip():
                continue
            normalized = candidate.strip().rstrip("/")
            if normalized not in origins:
                origins.append(normalized)

        if not origins:
            return ["http://localhost:3000"]
        return origins

    # ── Startup invariant validator ───────────────────────────────────────────

    @model_validator(mode="after")
    def _validate_settings(self) -> "Settings":
        """
        Enforce ATS timeout invariants, report bounds, URL derivation, and
        production fail-closed requirements (ADR-0001).
        """
        min_required = self.ATS_JOB_TIMEOUT_SECONDS / 60
        if self.ATS_STALE_PROCESSING_MINUTES <= min_required:
            raise ValueError(
                f"CONFIG INVARIANT VIOLATED: "
                f"ATS_STALE_PROCESSING_MINUTES ({self.ATS_STALE_PROCESSING_MINUTES}) "
                f"must be strictly greater than "
                f"ATS_JOB_TIMEOUT_SECONDS / 60 "
                f"({self.ATS_JOB_TIMEOUT_SECONDS} / 60 = {min_required:.1f}). "
                f"The stale guard must never fire on a running job. "
                f"Set ATS_STALE_PROCESSING_MINUTES > {min_required:.0f} in your .env."
            )
        if self.REPORT_DATE_LOOKBACK_DAYS < 0:
            raise ValueError(
                "CONFIG INVARIANT VIOLATED: REPORT_DATE_LOOKBACK_DAYS must be >= 0."
            )
        if self.REPORT_LIST_MAX_LIMIT < 1:
            raise ValueError(
                "CONFIG INVARIANT VIOLATED: REPORT_LIST_MAX_LIMIT must be >= 1."
            )

        # Derive APP_PUBLIC_URL from APP_PUBLIC_HOST when only the host is set.
        if not (self.APP_PUBLIC_URL or "").strip() and (self.APP_PUBLIC_HOST or "").strip():
            host = self.APP_PUBLIC_HOST.strip().rstrip("/")
            if host.startswith("http://") or host.startswith("https://"):
                object.__setattr__(self, "APP_PUBLIC_URL", host.rstrip("/"))
            else:
                object.__setattr__(self, "APP_PUBLIC_URL", f"https://{host}")

        if self.is_production:
            self._enforce_production_requirements()

        return self

    def _enforce_production_requirements(self) -> None:
        """
        Fail closed when APP_ENV=production and mandatory production settings
        are missing (ADR-0001).
        """
        missing: list[str] = []

        if not (self.REDIS_URL or "").strip():
            missing.append("REDIS_URL")

        if not (self.APP_PUBLIC_URL or "").strip():
            missing.append("APP_PUBLIC_URL (or APP_PUBLIC_HOST)")

        if not (self.FRONTEND_URL or "").strip():
            missing.append("FRONTEND_URL")
        elif self.FRONTEND_URL.strip().rstrip("/") in {
            "http://localhost:3000",
            "https://localhost:3000",
            "http://127.0.0.1:3000",
            "https://127.0.0.1:3000",
        }:
            missing.append(
                "FRONTEND_URL must be the public HTTPS origin "
                "(not localhost) when APP_ENV=production"
            )

        if not (self.INTERNAL_API_TOKEN or "").strip():
            missing.append("INTERNAL_API_TOKEN")

        if self.STORAGE_PROVIDER.strip().lower() != "gcs":
            missing.append("STORAGE_PROVIDER must be 'gcs' when APP_ENV=production")

        if not (self.GCS_BUCKET_NAME or "").strip():
            missing.append("GCS_BUCKET_NAME")

        for name in (
            "GCP_PROJECT_ID",
            "GCP_PRIVATE_KEY_ID",
            "GCP_PRIVATE_KEY",
            "GCP_CLIENT_EMAIL",
            "GCP_CLIENT_ID",
        ):
            if not (getattr(self, name) or "").strip():
                missing.append(name)

        for name in (
            "MICROSOFT_TENANT_ID",
            "MICROSOFT_CLIENT_ID",
            "MICROSOFT_CLIENT_SECRET",
            "MICROSOFT_REDIRECT_URI",
        ):
            if not (getattr(self, name) or "").strip():
                missing.append(name)

        if self.CORS_ORIGINS and "*" in {
            part.strip() for part in self.CORS_ORIGINS.split(",")
        }:
            missing.append("CORS_ORIGINS must not contain '*' when APP_ENV=production")

        if missing:
            joined = ", ".join(missing)
            raise ValueError(
                "CONFIG INVARIANT VIOLATED: APP_ENV=production requires the "
                f"following settings to be set correctly: {joined}. "
                "See docs/adr/ADR-0001-production-architecture.md."
            )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    Uses lru_cache so the .env file is only read once per process lifetime.
    """
    return Settings()
