"""
scripts/apply_gcs_cors.py

One-time script to apply CORS configuration to the GCS bucket.

Run this once from the project root before testing resume preview:

    python scripts/apply_gcs_cors.py

Requirements:
    - STORAGE_PROVIDER=gcs in .env
    - GCS_BUCKET_NAME set in .env
    - All GCP_* credentials set in .env

What it does:
    Allows browsers to embed the GCS signed URL in an <iframe> or <object>
    tag for native PDF preview. Without this, browsers block cross-origin
    responses from GCS even on valid signed URLs.

CORS policy applied:
    - Allowed origins : * (all — lock to your frontend domain in production)
    - Allowed methods : GET only
    - Exposed headers : Content-Type, Content-Disposition, Content-Length,
                        Cache-Control
    - Max age         : 3600 seconds (1 hour preflight cache)

After running, verify with:
    python scripts/apply_gcs_cors.py --verify
"""

from __future__ import annotations

import sys

# ── Load .env so the script works standalone ──────────────────────────────────
# This imports the same settings object used by the FastAPI app.
import os
# Ensure we can import app modules when running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import get_settings
from app.services.storage.credentials import get_gcp_credentials


def _get_credentials_for_admin():
    """
    Build GCP credentials with full_control scope.

    The app's default credentials use ``devstorage.read_write`` (sufficient
    for upload/download).  Patching bucket metadata (CORS) requires the
    broader ``devstorage.full_control`` scope — so we build a separate
    credential object here, only for this admin script.
    """
    from google.oauth2 import service_account  # type: ignore[import]
    from app.config import get_settings

    settings = get_settings()
    raw_key: str = settings.GCP_PRIVATE_KEY  # type: ignore[assignment]
    private_key = raw_key.replace("\\n", "\n")

    info = {
        "type":            "service_account",
        "project_id":      settings.GCP_PROJECT_ID,
        "private_key_id":  settings.GCP_PRIVATE_KEY_ID,
        "private_key":     private_key,
        "client_email":    settings.GCP_CLIENT_EMAIL,
        "client_id":       settings.GCP_CLIENT_ID,
        "auth_uri":        "https://accounts.google.com/o/oauth2/auth",
        "token_uri":       "https://oauth2.googleapis.com/token",
    }
    return service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/devstorage.full_control"],
    )


def apply_cors(verify_only: bool = False) -> None:
    settings = get_settings()

    if settings.STORAGE_PROVIDER.lower() != "gcs":
        print(f"[ERROR] STORAGE_PROVIDER={settings.STORAGE_PROVIDER!r} — must be 'gcs' to apply CORS.")
        sys.exit(1)

    if not settings.GCS_BUCKET_NAME:
        print("[ERROR] GCS_BUCKET_NAME is not set in .env.")
        sys.exit(1)

    try:
        from google.cloud import storage as gcs  # type: ignore[import]
    except ImportError:
        print("[ERROR] google-cloud-storage not installed. Run: pip install google-cloud-storage")
        sys.exit(1)

    credentials = _get_credentials_for_admin()
    client       = gcs.Client(project=settings.GCP_PROJECT_ID, credentials=credentials)
    bucket       = client.bucket(settings.GCS_BUCKET_NAME)


    if verify_only:
        # ── Verify current CORS config ────────────────────────────────────────
        bucket.reload()
        rules = bucket.cors
        if rules:
            print(f"[OK] Current CORS rules on gs://{settings.GCS_BUCKET_NAME}:")
            for rule in rules:
                print(f"     {rule}")
        else:
            print(f"[WARN] No CORS rules found on gs://{settings.GCS_BUCKET_NAME}.")
        return

    # ── Apply CORS config ─────────────────────────────────────────────────────
    # origin="*" — replace with your frontend domain in production, e.g.:
    #   "https://your-recruiter-portal.com"
    bucket.cors = [
        {
            "origin":         ["*"],
            "method":         ["GET"],
            "responseHeader": [
                "Content-Type",
                "Content-Disposition",
                "Content-Length",
                "Cache-Control",
            ],
            "maxAgeSeconds":  3600,
        }
    ]
    bucket.patch()
    print(f"[OK] CORS applied to gs://{settings.GCS_BUCKET_NAME}")
    print( "     GET from any origin is now allowed for iframe/embed PDF preview.")
    print( "     Content-Type and Content-Disposition headers are exposed to the browser.")
    print()
    print( "     ⚠  For production: restrict 'origin' to your frontend domain.")
    print( "        Edit the bucket.cors list above and re-run this script.")


if __name__ == "__main__":
    verify = "--verify" in sys.argv
    apply_cors(verify_only=verify)
