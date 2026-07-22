"""
app/services/auth_service.py

Service layer for Google OAuth and recruiter persistence.

Responsibilities:
  - Google OAuth token exchange        (exchange_code_for_tokens)
  - Google userinfo retrieval          (fetch_google_userinfo)
  - OAuth token encryption/decryption  (encrypt_oauth_tokens / decrypt_oauth_tokens)
  - Recruiter upsert / fetch / revoke  (upsert_recruiter, get_recruiter_by_id, revoke_recruiter)
  - Full callback orchestration        (handle_oauth_callback)
  - MongoDB index bootstrap            (ensure_indexes)

NO business logic lives in route handlers — this module owns it all.
"""

import json
import logging
import uuid
from datetime import datetime
from typing import Any

import httpx
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.config import get_settings
from app.models.recruiter import OAuthStatus, ProviderType, RecruiterDocument
from app.security.encryption import decrypt, encrypt
from app.security.jwt import create_access_token

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Collection name ───────────────────────────────────────────────────────────
_COLLECTION = "recruiters"

# ── Google API endpoints ──────────────────────────────────────────────────────
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


# ══════════════════════════════════════════════════════════════════════════════
# MongoDB index bootstrap
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Create required MongoDB indexes on the recruiters collection.

    Idempotent — safe to call on every application startup.
    Indexes created:
      - ``email``        : unique (prevents duplicate recruiters)
      - ``recruiter_id`` : unique (used as JWT subject, must be lookup-fast)
    """
    await db[_COLLECTION].create_index("email", unique=True)
    await db[_COLLECTION].create_index("recruiter_id", unique=True)
    logger.info("MongoDB indexes ensured on '%s' collection.", _COLLECTION)


# ══════════════════════════════════════════════════════════════════════════════
# Google API helpers
# ══════════════════════════════════════════════════════════════════════════════

async def exchange_code_for_tokens(code: str) -> dict[str, Any]:
    """
    Exchange a one-time Google authorisation code for OAuth tokens.

    Args:
        code: The ``code`` query parameter received from Google's OAuth callback.

    Returns:
        Raw JSON from Google's token endpoint, containing at minimum:
        ``access_token``, ``expires_in``, ``token_type``.
        ``refresh_token`` is present only when ``access_type=offline`` was used.

    Raises:
        HTTPException(400): Code is invalid, expired, or already consumed.
        HTTPException(502): Google token endpoint is unreachable.
        HTTPException(500): Unexpected server-side error during exchange.
    """
    payload: dict[str, str] = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(_GOOGLE_TOKEN_URL, data=payload)
    except httpx.RequestError as exc:
        logger.error("Network error reaching Google token endpoint: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Google authentication service.",
        ) from exc

    if response.status_code == 400:
        body: dict[str, Any] = response.json()
        desc = body.get("error_description") or body.get("error") or "unknown"
        logger.warning("Google rejected code exchange (400): %s", desc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired authorisation code: {desc}",
        )

    if not response.is_success:
        logger.error(
            "Google token endpoint returned %s: %s",
            response.status_code,
            response.text,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google token exchange returned an unexpected error.",
        )

    return response.json()  # type: ignore[return-value]


async def fetch_google_userinfo(access_token: str) -> dict[str, Any]:
    """
    Retrieve the authenticated user's profile from Google userinfo endpoint.

    Args:
        access_token: A valid Google OAuth access token.

    Returns:
        Userinfo dict containing at minimum ``sub``, ``email``, and ``name``.

    Raises:
        HTTPException(502): On network failure or non-2xx Google response.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(_GOOGLE_USERINFO_URL, headers=headers)
    except httpx.RequestError as exc:
        logger.error("Network error fetching Google userinfo: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Google userinfo service.",
        ) from exc

    if not response.is_success:
        logger.error(
            "Google userinfo endpoint returned %s: %s",
            response.status_code,
            response.text,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch user profile from Google.",
        )

    return response.json()  # type: ignore[return-value]


# ══════════════════════════════════════════════════════════════════════════════
# Token encryption / decryption
# ══════════════════════════════════════════════════════════════════════════════

def encrypt_oauth_tokens(
    access_token: str,
    refresh_token: str | None,
) -> str:
    """
    Serialise OAuth tokens to a compact JSON string, then Fernet-encrypt.

    Storing a single encrypted blob (rather than two separate fields) keeps
    the document surface small and avoids partial-decryption edge cases.

    Args:
        access_token:  Google OAuth access token.
        refresh_token: Google OAuth refresh token (``None`` on re-login without
                       ``prompt=consent``).

    Returns:
        URL-safe base64 Fernet ciphertext string.
    """
    payload: dict[str, str | None] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
    }
    return encrypt(json.dumps(payload, separators=(",", ":")))


def decrypt_oauth_tokens(encrypted_blob: str) -> dict[str, str | None]:
    """
    Decrypt and deserialise a stored OAuth token blob.

    Args:
        encrypted_blob: Value from ``recruiter.oauth_tokens_encrypted``.

    Returns:
        Dict with ``access_token`` and ``refresh_token`` keys.

    Raises:
        ValueError: If the blob is corrupted or the Fernet key has changed.
    """
    raw_json = decrypt(encrypted_blob)
    return json.loads(raw_json)  # type: ignore[return-value]


# ══════════════════════════════════════════════════════════════════════════════
# Recruiter DB operations
# ══════════════════════════════════════════════════════════════════════════════

async def upsert_recruiter(
    db: AsyncIOMotorDatabase,
    email: str,
    name: str,
    encrypted_tokens: str,
    provider: str = "gmail",
) -> RecruiterDocument:
    """
    Create or update a recruiter document in MongoDB.

    Logic:
      - Match on ``email`` (unique index).
      - If found → update tokens, name, status, provider, and ``updated_at``.
      - If not found → insert with a fresh UUID ``recruiter_id``.
      - Returns the document **after** the write (``ReturnDocument.AFTER``).

    Args:
        db:               Motor database instance (injected by FastAPI dependency).
        email:            Account email — used as the natural unique key.
        name:             Display name from the provider's userinfo endpoint.
        encrypted_tokens: Fernet-encrypted token blob from ``encrypt_oauth_tokens``.
        provider:         Email provider identifier — ``"gmail"`` or ``"outlook"``.
                          Defaults to ``"gmail"`` so the existing Google OAuth
                          callback path requires no changes.

    Returns:
        The persisted :class:`RecruiterDocument` (post-upsert state).

    Raises:
        HTTPException(500): On any database write failure.
    """
    # Validate provider string against the enum to catch programming errors early.
    try:
        provider_value: str = ProviderType(provider).value
    except ValueError:
        logger.error(
            "upsert_recruiter called with unknown provider=%r for email=%s. "
            "Falling back to 'gmail'.",
            provider, email,
        )
        provider_value = ProviderType.gmail.value

    now = datetime.utcnow()

    try:
        doc = await db[_COLLECTION].find_one_and_update(
            filter={"email": email},
            update={
                "$set": {
                    "name": name,
                    "oauth_tokens_encrypted": encrypted_tokens,
                    "oauth_status": OAuthStatus.active.value,
                    "provider": provider_value,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "recruiter_id": str(uuid.uuid4()),
                    "email": email,
                    "created_at": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except Exception as exc:
        logger.exception(
            "Database error during recruiter upsert for email=%s: %s", email, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist recruiter record.",
        ) from exc

    if doc is None:
        # Defensive: find_one_and_update with upsert=True + AFTER should always return
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Recruiter upsert returned no document.",
        )

    return RecruiterDocument.from_mongo(doc)


async def get_recruiter_by_id(
    db: AsyncIOMotorDatabase,
    recruiter_id: str,
) -> RecruiterDocument:
    """
    Fetch a recruiter document by their UUID.

    Args:
        db:           Motor database instance.
        recruiter_id: UUID string of the recruiter (stored as JWT ``sub``).

    Returns:
        Matching :class:`RecruiterDocument`.

    Raises:
        HTTPException(404): No recruiter with that ID exists.
        HTTPException(500): On database read failure.
    """
    try:
        doc = await db[_COLLECTION].find_one({"recruiter_id": recruiter_id})
    except Exception as exc:
        logger.exception(
            "DB error fetching recruiter_id=%s: %s", recruiter_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch recruiter from database.",
        ) from exc

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recruiter '{recruiter_id}' not found.",
        )

    return RecruiterDocument.from_mongo(doc)


async def revoke_recruiter(
    db: AsyncIOMotorDatabase,
    recruiter_id: str,
) -> None:
    """
    Revoke a recruiter's OAuth authorisation.

    Sets ``oauth_status`` to ``revoked`` and clears the encrypted token blob
    so the stored credentials are no longer usable even if the DB is breached.

    Args:
        db:           Motor database instance.
        recruiter_id: UUID of the recruiter to revoke.

    Raises:
        HTTPException(404): No recruiter with that ID exists.
        HTTPException(500): On database write failure.
    """
    now = datetime.utcnow()

    try:
        result = await db[_COLLECTION].update_one(
            {"recruiter_id": recruiter_id},
            {
                "$set": {
                    "oauth_status": OAuthStatus.revoked.value,
                    "oauth_tokens_encrypted": "",
                    "updated_at": now,
                }
            },
        )
    except Exception as exc:
        logger.exception(
            "DB error revoking recruiter_id=%s: %s", recruiter_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to revoke recruiter tokens.",
        ) from exc

    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Recruiter '{recruiter_id}' not found.",
        )

    logger.info("OAuth tokens revoked for recruiter_id=%s", recruiter_id)


# ══════════════════════════════════════════════════════════════════════════════
# Full OAuth callback orchestration
# ══════════════════════════════════════════════════════════════════════════════

async def handle_oauth_callback(
    code: str,
    db: AsyncIOMotorDatabase,
) -> dict[str, str]:
    """
    Orchestrate the complete Google OAuth callback flow.

    Steps:
      1. Exchange authorisation code → Google OAuth tokens
      2. Fetch user profile (email + name + sub) from Google
      3. Validate required fields exist
      4. Encrypt the OAuth tokens as a single Fernet blob
      5. Upsert the recruiter document in MongoDB
      6. Issue an application-level HS256 JWT

    Args:
        code: One-time authorisation code from Google.
        db:   Motor database instance (injected at call site).

    Returns:
        Dict with ``access_token`` (JWT string) and ``token_type`` = ``"bearer"``.
    """
    # ── Step 1: Exchange code → Google tokens ─────────────────────────────────
    token_data = await exchange_code_for_tokens(code)

    raw_access_token: str | None = token_data.get("access_token")
    raw_refresh_token: str | None = token_data.get("refresh_token")
    expires_in: int = int(token_data.get("expires_in", 3600))

    if not raw_access_token:
        logger.error(
            "Google token response missing 'access_token'. Response: %s", token_data
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google did not return an access token.",
        )

    # ── Step 2: Fetch user profile ────────────────────────────────────────────
    userinfo = await fetch_google_userinfo(raw_access_token)

    email: str | None = userinfo.get("email")
    name: str = userinfo.get("name") or userinfo.get("email") or "Unknown"
    google_sub: str | None = userinfo.get("sub")

    # ── Step 3: Validate required fields ─────────────────────────────────────
    if not email or not google_sub:
        logger.error(
            "Google userinfo missing required fields. email=%s sub=%s", email, google_sub
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Google userinfo response is missing required fields (email / sub).",
        )

    logger.info(
        "Google OAuth success — email=%s expires_in=%ss refresh_token_present=%s",
        email,
        expires_in,
        bool(raw_refresh_token),
    )

    # ── Step 4: Encrypt tokens ────────────────────────────────────────────────
    encrypted_tokens = encrypt_oauth_tokens(raw_access_token, raw_refresh_token)

    # ── Step 5: Upsert recruiter in MongoDB ───────────────────────────────────
    recruiter = await upsert_recruiter(db, email, name, encrypted_tokens)

    # ── Step 6: Issue application JWT ─────────────────────────────────────────
    jwt_payload: dict[str, str] = {
        "sub": recruiter.recruiter_id,
        "recruiter_id": recruiter.recruiter_id,
        "email": recruiter.email,
    }
    app_token = create_access_token(jwt_payload)

    logger.info(
        "JWT issued for recruiter_id=%s email=%s",
        recruiter.recruiter_id,
        recruiter.email,
    )

    return {"access_token": app_token, "token_type": "bearer"}
