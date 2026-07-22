"""
app/security/jwt.py

JSON Web Token utilities using python-jose.

Algorithm : HS256
Key source : Settings.JWT_SECRET_KEY
Expiry     : Settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.config import get_settings

settings = get_settings()

# ── Constants ─────────────────────────────────────────────────────────────────
ALGORITHM = "HS256"


def create_access_token(data: dict[str, Any]) -> str:
    """
    Create a signed JWT access token.

    Args:
        data: Arbitrary claims to embed in the token payload.
              A ``sub`` (subject) key is strongly recommended.

    Returns:
        A compact JWT string.

    Example::

        token = create_access_token({"sub": str(user_id), "role": "admin"})
    """
    payload = data.copy()
    expire = datetime.now(tz=timezone.utc) + timedelta(
        minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload.update({"exp": expire, "iat": datetime.now(tz=timezone.utc)})
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> dict[str, Any]:
    """
    Validate and decode a JWT access token.

    Args:
        token: The compact JWT string to verify.

    Returns:
        The decoded payload as a plain dictionary.

    Raises:
        ValueError: If the token is expired, malformed, or the signature
                    does not match.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[ALGORITHM],
        )
        return payload
    except JWTError as exc:
        raise ValueError(f"Token verification failed: {exc}") from exc
