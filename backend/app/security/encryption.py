"""
app/security/encryption.py

Symmetric encryption using cryptography.fernet (AES-128-CBC + HMAC-SHA256).

The Fernet key is loaded from Settings.FERNET_KEY.
Generate a new key with: cryptography.fernet.Fernet.generate_key()
"""

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

settings = get_settings()

# ── Initialise Fernet cipher once ────────────────────────────────────────────
_fernet: Fernet = Fernet(settings.FERNET_KEY.encode())


def encrypt(data: str) -> str:
    """
    Encrypt a plaintext string.

    Args:
        data: The plaintext value to encrypt.

    Returns:
        A URL-safe base64-encoded ciphertext string.

    Raises:
        ValueError: If ``data`` is empty.
    """
    if not data:
        raise ValueError("Cannot encrypt an empty string.")
    token: bytes = _fernet.encrypt(data.encode("utf-8"))
    return token.decode("utf-8")


def decrypt(data: str) -> str:
    """
    Decrypt a Fernet-encrypted string.

    Args:
        data: The ciphertext string produced by :func:`encrypt`.

    Returns:
        The original plaintext string.

    Raises:
        ValueError: If the token is invalid or has been tampered with.
    """
    if not data:
        raise ValueError("Cannot decrypt an empty string.")
    try:
        plaintext: bytes = _fernet.decrypt(data.encode("utf-8"))
        return plaintext.decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Decryption failed: invalid or corrupted token.") from exc
