"""
app/dependencies.py

Reusable FastAPI dependency functions.

Provides:
  - get_current_user : Two-layer auth guard
      Layer 1 — JWT signature + expiry verification
      Layer 2 — Live DB lookup to enforce oauth_status ("revoked" check)

Design note:
  Trusting the JWT alone is insufficient once a revoke endpoint exists.
  A valid token could still be presented after revocation. This dependency
  resolves that by querying MongoDB on every protected request, ensuring
  the recruiter's current authorization state is always respected.
"""

import logging
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.mongo import get_db
from app.models.recruiter import OAuthStatus
from app.security.jwt import verify_token

logger = logging.getLogger(__name__)

# ── HTTP Bearer scheme ────────────────────────────────────────────────────────
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict[str, Any]:
    """
    Two-layer FastAPI authentication + authorization dependency.

    **Layer 1 — JWT verification**
      Extracts and cryptographically verifies the Bearer token.
      Rejects missing, malformed, or expired tokens with HTTP 401.

    **Layer 2 — DB-backed revocation check**
      Fetches the recruiter document from MongoDB using ``recruiter_id``
      embedded in the JWT payload.
      - If the recruiter is not found → HTTP 401 (token references unknown identity)
      - If ``oauth_status == "revoked"`` → HTTP 403 (identity known, access denied)

    This two-layer approach means revoking a recruiter's OAuth access
    takes effect immediately on the next request, regardless of token expiry.

    Args:
        credentials: Bearer credentials extracted from the Authorization header.
        db:          Async Motor database instance (injected by FastAPI).

    Returns:
        Decoded JWT payload dict — structure is unchanged from before,
        so existing route handlers require no modification.

    Raises:
        HTTPException(401): Token missing / invalid / expired, or recruiter not found.
        HTTPException(403): Recruiter exists but OAuth access has been revoked.

    Usage::

        @router.get("/protected")
        async def protected_route(user: dict = Depends(get_current_user)):
            return {"recruiter_id": user["recruiter_id"]}
    """
    # ── Layer 1: JWT extraction and verification ───────────────────────────────
    _unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication token.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise _unauthorized

    try:
        payload: dict[str, Any] = verify_token(credentials.credentials)
    except ValueError:
        raise _unauthorized

    # Both "sub" and "recruiter_id" must be present in the payload
    recruiter_id: str | None = payload.get("recruiter_id")

    if not recruiter_id or not payload.get("sub"):
        logger.warning(
            "JWT accepted but missing required claims (sub / recruiter_id)."
        )
        raise _unauthorized

    # ── Layer 2: Live DB revocation check ─────────────────────────────────────
    try:
        recruiter = await db["recruiters"].find_one(
            {"recruiter_id": recruiter_id},
            # Project only the fields we need — avoids pulling encrypted tokens
            projection={"recruiter_id": 1, "oauth_status": 1, "_id": 0},
        )
    except Exception as exc:
        logger.exception(
            "DB error during auth dependency lookup for recruiter_id=%s: %s",
            recruiter_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication check failed. Please try again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Recruiter referenced by JWT no longer exists in the database
    if recruiter is None:
        logger.warning(
            "JWT references unknown recruiter_id=%s — denying access.", recruiter_id
        )
        raise _unauthorized

    # Recruiter exists but their OAuth access has been explicitly revoked
    if recruiter.get("oauth_status") == OAuthStatus.revoked.value:
        logger.warning(
            "Access denied — recruiter_id=%s has oauth_status='revoked'.", recruiter_id
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="OAuth access revoked. Please re-authenticate via /auth/login.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # ── Auth passed — return the decoded payload unchanged ─────────────────────
    return payload
