"""
app/services/storage/credentials.py

GCP service-account credential builder.

Constructs a ``google.oauth2.service_account.Credentials`` object from
individual environment variables, replacing the conventional
``GOOGLE_APPLICATION_CREDENTIALS`` JSON-file approach.

Why this module exists
----------------------
Mounting a service-account JSON key as a file is not always practical in
containerised / serverless environments.  Splitting the credentials into
discrete environment variables lets them be injected securely via Docker
secrets, Kubernetes secrets, or CI secret stores without any file I/O.

Private key encoding
--------------------
The PEM private key contains real newline characters.  Most .env files and
secret stores encode these as the two-character escape sequence ``\\n``
(backslash + n).  This module restores the literal newlines before passing
the key to the Google SDK:

    raw (from env):   "-----BEGIN RSA PRIVATE KEY-----\\nMIIE...\\n-----END..."
    after restore():  "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END..."

Usage
-----
    from app.services.storage.credentials import get_gcp_credentials

    credentials = get_gcp_credentials()
    client = storage.Client(project=settings.GCP_PROJECT_ID, credentials=credentials)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from google.oauth2.service_account import Credentials  # pragma: no cover

logger = logging.getLogger(__name__)

# Scopes required for Google Cloud Storage operations (read + write).
_GCS_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/devstorage.read_write",
]


def get_gcp_credentials() -> "Credentials":
    """
    Build and return GCP service-account credentials from environment variables.

    Reads the following settings (all required when ``STORAGE_PROVIDER=gcs``):
        * ``GCP_PROJECT_ID``      — GCP project identifier.
        * ``GCP_PRIVATE_KEY_ID``  — Service-account key ID.
        * ``GCP_PRIVATE_KEY``     — PEM private key (``\\n`` restored to real newlines).
        * ``GCP_CLIENT_EMAIL``    — Service-account email address.
        * ``GCP_CLIENT_ID``       — Service-account numeric client ID.

    Returns
    -------
    google.oauth2.service_account.Credentials
        A scoped credential object suitable for passing to ``storage.Client()``.

    Raises
    ------
    ImportError
        If ``google-auth`` is not installed.
    ValueError
        If any required GCP credential field is missing from the environment.
    RuntimeError
        If credential construction fails for any other reason (e.g. malformed key).
    """
    # ── Step 1: verify google-auth is installed ───────────────────────────────
    logger.debug("event=gcs.credentials.import_check")
    try:
        from google.oauth2 import service_account  # type: ignore[import]
    except ImportError as exc:
        logger.error(
            "event=gcs.credentials.import_failed "
            "detail='google-auth package not found — run: pip install google-auth'"
        )
        raise ImportError(
            "google-auth is not installed. "
            "Run: pip install google-auth"
        ) from exc

    # ── Step 2: load settings ─────────────────────────────────────────────────
    logger.debug("event=gcs.credentials.settings_load")
    from app.config import get_settings
    settings = get_settings()

    # ── Step 3: validate all required fields are present ─────────────────────
    _REQUIRED = (
        "GCP_PROJECT_ID",
        "GCP_PRIVATE_KEY_ID",
        "GCP_PRIVATE_KEY",
        "GCP_CLIENT_EMAIL",
        "GCP_CLIENT_ID",
    )
    missing: list[str] = [
        field for field in _REQUIRED
        if not getattr(settings, field, None)
    ]

    if missing:
        logger.error(
            "event=gcs.credentials.missing_env_vars "
            "missing=%s "
            "hint='Copy values from your GCP service-account JSON key into .env'",
            missing,
        )
        raise ValueError(
            "The following GCP credential environment variables are required "
            f"when STORAGE_PROVIDER=gcs but are not set: {', '.join(missing)}. "
            "Check your .env file against .env.example."
        )

    logger.debug(
        "event=gcs.credentials.env_vars_present "
        "project_id=%s client_email=%s key_id=%s",
        settings.GCP_PROJECT_ID,
        settings.GCP_CLIENT_EMAIL,
        settings.GCP_PRIVATE_KEY_ID,
        # GCP_PRIVATE_KEY is intentionally never logged
    )

    # ── Step 4: restore literal newlines in the private key ───────────────────
    # .env files / secret stores encode PEM newlines as the two-character \n.
    # The Google SDK requires real newlines inside the PEM block.
    raw_key: str = settings.GCP_PRIVATE_KEY  # type: ignore[assignment]
    private_key: str = raw_key.replace("\\n", "\n")

    # Sanity-check the PEM header so we catch encoding mistakes early.
    if not private_key.startswith("-----BEGIN"):
        logger.error(
            "event=gcs.credentials.private_key_malformed "
            "hint='GCP_PRIVATE_KEY must start with -----BEGIN after \\\\n decoding. "
            "Verify the value in .env preserves \\\\n between key lines.'"
        )
        raise ValueError(
            "GCP_PRIVATE_KEY does not appear to be a valid PEM block after newline "
            "restoration.  Make sure every newline in the key is stored as \\n "
            "(two characters) in .env."
        )

    logger.debug(
        "event=gcs.credentials.private_key_decoded "
        "pem_header='%s' total_chars=%d",
        private_key.split("\n")[0],   # logs only the header line, never the key body
        len(private_key),
    )

    # ── Step 5: build service-account info dict (mirrors a JSON key file) ─────
    service_account_info: dict[str, str] = {
        "type": "service_account",
        "project_id":     settings.GCP_PROJECT_ID,         # type: ignore[assignment]
        "private_key_id": settings.GCP_PRIVATE_KEY_ID,     # type: ignore[assignment]
        "private_key":    private_key,
        "client_email":   settings.GCP_CLIENT_EMAIL,       # type: ignore[assignment]
        "client_id":      settings.GCP_CLIENT_ID,          # type: ignore[assignment]
        "auth_uri":  "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    logger.debug("event=gcs.credentials.service_account_info_built")

    # ── Step 6: construct and scope the credentials ────────────────────────────
    try:
        credentials: Credentials = (
            service_account.Credentials.from_service_account_info(
                service_account_info,
                scopes=_GCS_SCOPES,
            )
        )
    except Exception as exc:
        logger.error(
            "event=gcs.credentials.build_failed "
            "error_type=%s detail=%s "
            "hint='Check GCP_PRIVATE_KEY format — must be RSA PEM with \\\\n newlines in .env'",
            type(exc).__name__,
            exc,
        )
        raise RuntimeError(
            "Failed to construct GCP service-account credentials from environment "
            "variables.  Ensure GCP_PRIVATE_KEY is a valid PEM-encoded RSA key "
            f"with \\n-encoded newlines in the .env file.  Detail: {exc}"
        ) from exc

    logger.info(
        "event=gcs.credentials.ready "
        "project_id=%s client_email=%s scopes=%s",
        settings.GCP_PROJECT_ID,
        settings.GCP_CLIENT_EMAIL,
        _GCS_SCOPES,
    )
    return credentials
