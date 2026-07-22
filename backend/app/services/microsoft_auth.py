"""
app/services/microsoft_auth.py

Microsoft OAuth 2.0 callback orchestration.

Responsibilities
----------------
- Exchange a Microsoft authorisation code for OAuth tokens
- Fetch the user's profile (email + display name) from Microsoft Graph /me
- Encrypt tokens + upsert the recruiter document with provider="outlook"
- Issue an application-level HS256 JWT (reuses the shared JWT infrastructure)

Designed to be called **exclusively** from the
``GET /auth/microsoft/callback`` route handler in ``api/auth.py``.

No business logic should live in the route handler — this module owns it all,
mirroring the structure of ``auth_service.py`` for Google OAuth.

Token endpoint
--------------
https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token

Using the tenant-specific endpoint (instead of /common) restricts
authentication to accounts from the Infomatics Corp Azure AD directory only.
Personal Microsoft accounts and accounts from other organisations will be
rejected by Azure before the token exchange completes.

Required scopes (requested during the consent flow in ``/auth/microsoft/login``)
-----------------------------------------------------------------------------------
    openid          — required for id_token (user identity)
    email           — email claim in id_token
    profile         — name claim in id_token
    offline_access  — issues a refresh_token (essential for long-running ingestion)
    Mail.Read       — read the recruiter's Outlook messages
    Mail.Send       — send daily reports from the recruiter's mailbox

Configuration prerequisites (.env)
------------------------------------
    MICROSOFT_CLIENT_ID     = <Azure App Registration Application ID>
    MICROSOFT_CLIENT_SECRET = <Client secret value>
    MICROSOFT_REDIRECT_URI  = https://<your-domain>/api/v1/auth/microsoft/callback

Azure App Registration checklist
----------------------------------
1. Azure Portal → Azure Active Directory → App Registrations → New Registration
2. Name: "Recruitment Automation"
3. Supported account types: "Accounts in any organizational directory and
   personal Microsoft accounts" (multi-tenant — supports M365 + Outlook.com)
4. Redirect URI (Web platform):
   - Production: https://<your-domain>/api/v1/auth/microsoft/callback
   - Development: http://localhost:8000/api/v1/auth/microsoft/callback
5. API Permissions → Add → Microsoft Graph → Delegated:
   openid, email, profile, offline_access, Mail.Read, Mail.Send
   (No Application permissions — delegated only)
6. Certificates & Secrets → New client secret → copy immediately
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.services.auth_service import (
    encrypt_oauth_tokens,
    upsert_recruiter,
)
from app.security.jwt import create_access_token

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Microsoft identity platform base URL ─────────────────────────────────────
# Token URL is built at call-time using settings.MICROSOFT_TENANT_ID.
# Using /{tenant_id}/ instead of /common/ restricts token exchange to
# Infomatics Corp Azure AD accounts only (single-tenant behaviour).
_MS_TOKEN_BASE = "https://login.microsoftonline.com"
_MS_GRAPH_ME   = "https://graph.microsoft.com/v1.0/me"


# ══════════════════════════════════════════════════════════════════════════════
# Token exchange
# ══════════════════════════════════════════════════════════════════════════════

async def exchange_code_for_ms_tokens(code: str) -> dict[str, Any]:
    """
    Exchange a one-time Microsoft authorisation code for OAuth tokens.

    Args:
        code: The ``code`` query parameter received from Microsoft's
              OAuth callback redirect.

    Returns:
        Raw JSON from Microsoft's token endpoint, containing at minimum:
        ``access_token``, ``expires_in``, ``token_type``.
        ``refresh_token`` is present when ``offline_access`` scope was
        requested (which it always is in our consent flow).

    Raises:
        HTTPException(400): Code is invalid, expired, or already consumed.
        HTTPException(502): Microsoft token endpoint is unreachable.
        HTTPException(500): Unexpected server-side error during exchange.
    """
    _require_ms_config()

    # Build tenant-specific token URL.
    # /{tenant_id}/ locks the exchange to Infomatics Corp accounts only.
    token_url = (
        f"{_MS_TOKEN_BASE}/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/token"
    )

    payload: dict[str, str] = {
        "code":          code,
        "client_id":     settings.MICROSOFT_CLIENT_ID,      # type: ignore[arg-type]
        "client_secret": settings.MICROSOFT_CLIENT_SECRET,  # type: ignore[arg-type]
        "redirect_uri":  settings.MICROSOFT_REDIRECT_URI,   # type: ignore[arg-type]
        "grant_type":    "authorization_code",
        "scope":         "openid email profile offline_access Mail.Read Mail.Send",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                token_url,
                data=payload,
                headers={"Accept": "application/json"},
            )
    except httpx.RequestError as exc:
        logger.error(
            "event=ms_auth.token_exchange_network_error detail=%s", exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Microsoft authentication service.",
        ) from exc

    if response.status_code == 400:
        body: dict[str, Any] = response.json()
        desc = (
            body.get("error_description")
            or body.get("error")
            or "unknown"
        )
        logger.warning(
            "event=ms_auth.token_exchange_rejected status=400 detail=%s", desc
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid or expired Microsoft authorisation code: {desc}",
        )

    if not response.is_success:
        logger.error(
            "event=ms_auth.token_exchange_failed status=%d body=%s",
            response.status_code,
            response.text[:300],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Microsoft token exchange returned an unexpected error.",
        )

    data: dict[str, Any] = response.json()
    logger.info(
        "event=ms_auth.token_exchange_success "
        "expires_in=%s refresh_token_present=%s",
        data.get("expires_in"),
        "refresh_token" in data,
    )
    return data


# ══════════════════════════════════════════════════════════════════════════════
# User profile
# ══════════════════════════════════════════════════════════════════════════════

async def fetch_ms_userinfo(access_token: str) -> dict[str, Any]:
    """
    Retrieve the authenticated user's profile from Microsoft Graph ``/me``.

    Args:
        access_token: A valid Microsoft Graph access token.

    Returns:
        Graph /me response dict containing at minimum ``mail`` (or
        ``userPrincipalName``) and ``displayName``.

    Raises:
        HTTPException(502): On network failure or non-2xx Graph response.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept":        "application/json",
    }
    params = {"$select": "id,mail,userPrincipalName,displayName"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                _MS_GRAPH_ME,
                headers=headers,
                params=params,
            )
    except httpx.RequestError as exc:
        logger.error(
            "event=ms_auth.userinfo_network_error detail=%s", exc
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Microsoft Graph user profile endpoint.",
        ) from exc

    if not response.is_success:
        logger.error(
            "event=ms_auth.userinfo_failed status=%d body=%s",
            response.status_code,
            response.text[:300],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to fetch user profile from Microsoft Graph.",
        )

    return response.json()


# ══════════════════════════════════════════════════════════════════════════════
# Full OAuth callback orchestration
# ══════════════════════════════════════════════════════════════════════════════

async def handle_microsoft_oauth_callback(
    code: str,
    db:   AsyncIOMotorDatabase,
) -> dict[str, str]:
    """
    Orchestrate the complete Microsoft OAuth callback flow.

    Steps:
      1. Exchange authorisation code → Microsoft OAuth tokens
      2. Fetch user profile (email + display name) from Graph /me
      3. Validate required fields exist
      4. Encrypt OAuth tokens as a single Fernet blob
      5. Upsert the recruiter document in MongoDB with ``provider="outlook"``
      6. Issue an application-level HS256 JWT

    Mirrors ``auth_service.handle_oauth_callback()`` for Google — the
    route handler can call either and receive the same ``{"access_token": ...,
    "token_type": "bearer"}`` response.

    Args:
        code: One-time authorisation code from Microsoft.
        db:   Motor database instance (injected at call site).

    Returns:
        Dict with ``access_token`` (JWT string) and ``token_type = "bearer"``.

    Raises:
        HTTPException(400): Invalid or expired authorisation code.
        HTTPException(502): Microsoft API failure.
        HTTPException(500): Internal error (DB write, missing token, etc.).
    """
    # ── Step 1: Exchange code → Microsoft tokens ──────────────────────────────
    token_data = await exchange_code_for_ms_tokens(code)

    raw_access_token:  str | None = token_data.get("access_token")
    raw_refresh_token: str | None = token_data.get("refresh_token")
    expires_in: int = int(token_data.get("expires_in", 3600))

    if not raw_access_token:
        logger.error(
            "event=ms_auth.callback_missing_access_token response=%s",
            token_data,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Microsoft did not return an access token.",
        )

    if not raw_refresh_token:
        # offline_access scope was requested; a missing refresh token is
        # unexpected and means the recruiter cannot be ingested long-term.
        logger.error(
            "event=ms_auth.callback_missing_refresh_token "
            "reason=offline_access_scope_may_be_missing"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Microsoft did not return a refresh token. "
                "Ensure the 'offline_access' scope is requested during consent."
            ),
        )

    # ── Step 2: Fetch user profile from Graph /me ─────────────────────────────
    userinfo = await fetch_ms_userinfo(raw_access_token)

    # Microsoft 365 accounts use ``mail``; personal accounts may use
    # ``userPrincipalName`` as a fallback.
    email: str | None = (
        userinfo.get("mail")
        or userinfo.get("userPrincipalName")
    )
    name: str = (
        userinfo.get("displayName")
        or email
        or "Unknown"
    )
    ms_user_id: str | None = userinfo.get("id")

    # ── Step 3: Validate required fields ─────────────────────────────────────
    if not email or not ms_user_id:
        logger.error(
            "event=ms_auth.callback_missing_profile_fields "
            "email=%s ms_user_id=%s",
            email, ms_user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Microsoft Graph /me response is missing required fields "
                "(email / id). Ensure 'email' and 'profile' scopes are granted."
            ),
        )

    logger.info(
        "event=ms_auth.callback_success "
        "email=%s expires_in=%ss refresh_token_present=%s",
        email,
        expires_in,
        bool(raw_refresh_token),
    )

    # ── Step 4: Encrypt tokens ─────────────────────────────────────────────
    encrypted_tokens = encrypt_oauth_tokens(raw_access_token, raw_refresh_token)

    # ── Step 5: Upsert recruiter with provider="outlook" ──────────────────
    recruiter = await upsert_recruiter(
        db,
        email=email,
        name=name,
        encrypted_tokens=encrypted_tokens,
        provider="outlook",
    )

    # ── Step 6: Issue application JWT ──────────────────────────────────────
    jwt_payload: dict[str, str] = {
        "sub":          recruiter.recruiter_id,
        "recruiter_id": recruiter.recruiter_id,
        "email":        recruiter.email,
    }
    app_token = create_access_token(jwt_payload)

    logger.info(
        "event=ms_auth.jwt_issued recruiter_id=%s email=%s",
        recruiter.recruiter_id,
        recruiter.email,
    )

    return {"access_token": app_token, "token_type": "bearer"}


# ══════════════════════════════════════════════════════════════════════════════
# Internal guard
# ══════════════════════════════════════════════════════════════════════════════

def _require_ms_config() -> None:
    """
    Raise an HTTPException if Microsoft OAuth is not fully configured.

    Checks for all four required environment variables and raises a single
    descriptive ``HTTP 503`` listing every missing variable.  This produces
    a clear error message instead of a cryptic ``None``-related crash deep
    in the request flow.

    Raises:
        HTTPException(503): One or more required env vars are missing.
    """
    missing: list[str] = []
    if not settings.MICROSOFT_TENANT_ID:
        missing.append("MICROSOFT_TENANT_ID")
    if not settings.MICROSOFT_CLIENT_ID:
        missing.append("MICROSOFT_CLIENT_ID")
    if not settings.MICROSOFT_CLIENT_SECRET:
        missing.append("MICROSOFT_CLIENT_SECRET")
    if not settings.MICROSOFT_REDIRECT_URI:
        missing.append("MICROSOFT_REDIRECT_URI")

    if missing:
        logger.error(
            "event=ms_auth.not_configured missing_vars=%s", missing
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"Microsoft OAuth is not configured on this server. "
                f"Missing environment variables: {', '.join(missing)}. "
                f"Contact the system administrator."
            ),
        )
