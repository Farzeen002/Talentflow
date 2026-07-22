"""
app/api/auth.py

Authentication API endpoints.

Routes:
  GET  /auth/login                → Returns Google OAuth 2.0 consent URL
  GET  /auth/callback             → Exchanges Google code, issues JWT
  GET  /auth/microsoft/login      → Returns Microsoft OAuth 2.0 consent URL
  GET  /auth/microsoft/callback   → Exchanges Microsoft code, issues JWT
  GET  /auth/me                   → Returns current recruiter's profile (JWT required)
  POST /auth/revoke               → Revokes recruiter's OAuth tokens (JWT required)

All business logic is delegated to app.services.auth_service and
app.services.microsoft_auth.  Route handlers are kept thin — they only
validate input and shape responses.
"""

import logging
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel

from app.config import get_settings
from app.db.mongo import get_db
from app.dependencies import get_current_user
from app.models.recruiter import RecruiterResponse
from app.services.auth_service import (
    get_recruiter_by_id,
    handle_oauth_callback,
    revoke_recruiter,
)
from app.services.microsoft_auth import handle_microsoft_oauth_callback

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["Auth"])

# ── Google OAuth constants ─────────────────────────────────────────────────────
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"

_GOOGLE_SCOPES: list[str] = [
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# ── Microsoft OAuth constants ─────────────────────────────────────────────────
# Single-tenant: authorize URL uses the tenant-specific endpoint.
# The URL is constructed at request time from settings.MICROSOFT_TENANT_ID
# so changing the tenant requires only an .env update, not a code deploy.
_MS_AUTH_BASE = "https://login.microsoftonline.com"

_MS_SCOPES: list[str] = [
    "openid",
    "email",
    "profile",
    "offline_access",
    "Mail.Read",
    "Mail.Send",
]


# ── Response schemas ──────────────────────────────────────────────────────────

class LoginResponse(BaseModel):
    auth_url: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    message: str


# ══════════════════════════════════════════════════════════════════════════════
# Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/login",
    response_model=LoginResponse,
    summary="Initiate Google OAuth login",
    description=(
        "Constructs and returns the Google OAuth 2.0 consent URL. "
        "The client must redirect the end-user's browser to ``auth_url``."
    ),
)
async def login() -> LoginResponse:
    """
    Build and return the Google OAuth 2.0 consent URL.

    Includes ``access_type=offline`` to ensure a refresh token is issued,
    and ``prompt=consent`` to force re-consent on every call (guarantees
    a fresh refresh token even for returning users).
    """
    params: dict[str, str] = {
        "client_id":     settings.GOOGLE_CLIENT_ID,
        "redirect_uri":  settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         " ".join(_GOOGLE_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
    }
    auth_url = f"{_GOOGLE_AUTH_URL}?{urlencode(params)}"
    logger.debug("OAuth login URL generated.")
    return LoginResponse(auth_url=auth_url)


@router.get(
    "/callback",
    summary="Handle Google OAuth callback",
    description=(
        "Exchanges the one-time authorisation ``code`` from Google for OAuth tokens, "
        "encrypts and persists them, upserts the recruiter record, and either returns "
        "a signed JWT (local dev) or redirects the browser to the frontend with the token."
    ),
)
async def callback(
    code: str = Query(..., description="One-time authorisation code issued by Google."),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Full OAuth 2.0 callback handler.

    Delegates entirely to ``auth_service.handle_oauth_callback``.

    - In ``local`` mode (``APP_ENV=local``): returns a JSON ``TokenResponse``
      for easy testing with tools like curl / Swagger.
    - In all other environments: issues a 302 redirect to the frontend
      at ``http://localhost:3000/auth/success?token=<jwt>``.

    Raises:
        HTTPException(400): Invalid or expired authorisation code.
        HTTPException(502): Google API failure.
        HTTPException(500): Internal server error (DB write, missing token, etc.)
    """
    result = await handle_oauth_callback(code=code, db=db)

    jwt_token: str = result["access_token"]

    # ── Dev shortcut: return raw JSON when running locally ────────────────────
    if settings.APP_ENV == "local":
        logger.debug("APP_ENV=local → returning JSON token response (Google).")
        return TokenResponse(access_token=jwt_token)

    # ── All other environments: redirect browser to frontend ──────────────────
    frontend_url = f"{settings.FRONTEND_URL.rstrip('/')}/auth/success"
    logger.debug("Redirecting to frontend: %s", frontend_url)
    return RedirectResponse(
        url=f"{frontend_url}?token={jwt_token}",
        status_code=302,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Microsoft OAuth 2.0 endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/microsoft/login",
    response_model=LoginResponse,
    summary="Initiate Microsoft OAuth login",
    description=(
        "Constructs and returns the Microsoft OAuth 2.0 consent URL. "
        "The client must redirect the end-user's browser to ``auth_url``. "
        "Returns HTTP 503 if MICROSOFT_CLIENT_ID / SECRET / REDIRECT_URI are "
        "not configured in the server environment."
    ),
)
async def microsoft_login() -> LoginResponse:
    """
    Build and return the Microsoft OAuth 2.0 consent URL.

    Uses the tenant-specific authorize endpoint:
        https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize

    This restricts login to accounts from the Infomatics Corp Azure AD
    directory only.  Accounts from other organisations or personal
    Microsoft accounts will be rejected by Azure before reaching this
    application.

    Requires ``MICROSOFT_TENANT_ID``, ``MICROSOFT_CLIENT_ID``, and
    ``MICROSOFT_REDIRECT_URI`` to be set in the server environment.
    Returns HTTP 503 with a clear message if any are missing.

    Scopes requested:
        openid, email, profile, offline_access, Mail.Read, Mail.Send

    ``offline_access`` is required to obtain a refresh token so the
    ingestion pipeline can operate without the recruiter being online.
    ``Mail.Send`` is required for Daily Report outbound email.
    """
    if (
        not settings.MICROSOFT_TENANT_ID
        or not settings.MICROSOFT_CLIENT_ID
        or not settings.MICROSOFT_REDIRECT_URI
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Microsoft OAuth is not configured on this server. "
                "Set MICROSOFT_TENANT_ID, MICROSOFT_CLIENT_ID, "
                "MICROSOFT_CLIENT_SECRET, and MICROSOFT_REDIRECT_URI "
                "in your .env file."
            ),
        )

    # Build tenant-specific authorize URL.
    # Using /{tenant_id}/ instead of /common/ locks this app to the
    # Infomatics Corp Azure AD directory (single-tenant behaviour).
    ms_auth_url = (
        f"{_MS_AUTH_BASE}/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/authorize"
    )

    params: dict[str, str] = {
        "client_id":     settings.MICROSOFT_CLIENT_ID,
        "redirect_uri":  settings.MICROSOFT_REDIRECT_URI,
        "response_type": "code",
        "scope":         " ".join(_MS_SCOPES),
        "response_mode": "query",
        # ``prompt=consent`` forces the consent screen on every call so we
        # always receive a refresh token (even for returning users).
        # "prompt":        "consent",
    }
    auth_url = f"{ms_auth_url}?{urlencode(params)}"
    logger.info(
        "event=ms_auth.login_url_generated tenant_id=%s",
        settings.MICROSOFT_TENANT_ID,
    )
    return LoginResponse(auth_url=auth_url)


@router.get(
    "/microsoft/callback",
    summary="Handle Microsoft OAuth callback",
    description=(
        "Exchanges the one-time Microsoft authorisation ``code`` for OAuth tokens, "
        "encrypts and persists them with ``provider='outlook'``, upserts the "
        "recruiter record, and either returns a signed JWT (local dev) or "
        "redirects the browser to the frontend with the token."
    ),
)
async def microsoft_callback(
    code: str = Query(..., description="One-time authorisation code from Microsoft."),
    db: AsyncIOMotorDatabase = Depends(get_db),
):
    """
    Full Microsoft OAuth 2.0 callback handler.

    Delegates entirely to ``microsoft_auth.handle_microsoft_oauth_callback``.

    - In ``local`` mode (``APP_ENV=local``): returns a JSON ``TokenResponse``
      for easy testing with curl / Swagger.
    - In all other environments: issues a 302 redirect to the frontend
      at ``<FRONTEND_URL>/auth/success?token=<jwt>``.

    Raises:
        HTTPException(400): Invalid or expired authorisation code.
        HTTPException(502): Microsoft API failure.
        HTTPException(503): Microsoft OAuth not configured.
        HTTPException(500): Internal server error (DB write, missing token).
    """
    result = await handle_microsoft_oauth_callback(code=code, db=db)

    jwt_token: str = result["access_token"]

    # ── Dev shortcut: return raw JSON when running locally ────────────────────
    if settings.APP_ENV == "local":
        logger.debug("APP_ENV=local → returning JSON token response (Microsoft).")
        return TokenResponse(access_token=jwt_token)

    # ── All other environments: redirect browser to frontend ──────────────────
    frontend_url = f"{settings.FRONTEND_URL.rstrip('/')}/auth/success"
    logger.debug("Redirecting to frontend after Microsoft OAuth: %s", frontend_url)
    return RedirectResponse(
        url=f"{frontend_url}?token={jwt_token}",
        status_code=302,
    )


@router.get(
    "/me",
    response_model=RecruiterResponse,
    summary="Get current recruiter profile",
    description="Returns the authenticated recruiter's profile. Requires a valid Bearer JWT.",
)
async def get_me(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> RecruiterResponse:
    """
    Return the profile of the currently authenticated recruiter.

    Extracts ``recruiter_id`` from the JWT payload, fetches the record
    from MongoDB, and returns a public-safe :class:`RecruiterResponse`.

    Raises:
        HTTPException(401): Missing or invalid JWT.
        HTTPException(404): Recruiter record not found (edge case: DB and JWT out of sync).
        HTTPException(500): Database read failure.
    """
    recruiter_id: str | None = current_user.get("recruiter_id")

    if not recruiter_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing 'recruiter_id' claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    recruiter = await get_recruiter_by_id(db, recruiter_id)

    return RecruiterResponse(
        recruiter_id=recruiter.recruiter_id,
        email=recruiter.email,
        name=recruiter.name,
        oauth_status=recruiter.oauth_status,
        provider=recruiter.provider,
        created_at=recruiter.created_at,
    )


@router.post(
    "/revoke",
    response_model=MessageResponse,
    summary="Revoke OAuth tokens",
    description=(
        "Revokes the current recruiter's Google OAuth authorisation. "
        "Sets status to 'revoked' and clears the stored encrypted tokens. "
        "Requires a valid Bearer JWT."
    ),
)
async def revoke(
    current_user: dict[str, Any] = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> MessageResponse:
    """
    Revoke the authenticated recruiter's OAuth tokens.

    After revocation the recruiter must re-authenticate via ``/auth/login``
    to restore Gmail access.

    Raises:
        HTTPException(401): Missing or invalid JWT.
        HTTPException(404): Recruiter record not found.
        HTTPException(500): Database write failure.
    """
    recruiter_id: str | None = current_user.get("recruiter_id")

    if not recruiter_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token payload is missing 'recruiter_id' claim.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await revoke_recruiter(db, recruiter_id)
    return MessageResponse(message="OAuth tokens revoked successfully.")
