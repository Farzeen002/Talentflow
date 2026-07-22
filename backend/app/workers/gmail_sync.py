"""
app/workers/gmail_sync.py

Synchronous bridge between the RQ worker (no event loop) and the
async GmailService.

GmailService is built as an async class (uses httpx.AsyncClient).
RQ workers run synchronous functions in a thread pool — there is no
running asyncio event loop to await coroutines on.

This module provides a single public function, ``fetch_full_message``,
that bridges the gap by:
  1. Decrypting the recruiter's stored OAuth tokens.
  2. Constructing a GmailService instance.
  3. Running the coroutine inside a fresh asyncio event loop via
     ``asyncio.run()``.
  4. Returning the structured message dict to the caller.

Design rules:
  - No FastAPI objects
  - No Motor / async MongoDB
  - One asyncio.run() per call (event loop is created + destroyed each time)
  - Caller handles all exceptions; this module only translates them into
    GmailServiceError subclasses for consistent upstream handling

⚠️  asyncio.run() creates a new event loop per invocation.  For high-volume
    workloads, consider using a persistent loop per worker thread.  At the
    current batch size (≤20 messages per recruiter per cycle) this approach
    is safe and simple.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.auth_service import decrypt_oauth_tokens
from app.services.gmail_service import GmailService, GmailServiceError

logger = logging.getLogger(__name__)


def fetch_full_message(
    *,
    recruiter_id:        str,
    message_id:          str,
    encrypted_token_blob: str,
) -> dict[str, Any]:
    """
    Fetch and return a fully parsed Gmail message synchronously.

    Intended for use inside RQ worker tasks where no asyncio event loop
    is running.  Internally delegates to
    :meth:`~app.services.gmail_service.GmailService.get_full_message`.

    Args:
        recruiter_id:         UUID of the owning recruiter (used only for
                              logging context; not passed to Gmail API).
        message_id:           Gmail API message ID to fetch.
        encrypted_token_blob: Fernet-encrypted JSON blob from
                              ``recruiter.oauth_tokens_encrypted``.

    Returns:
        Structured message dict as returned by
        :meth:`~app.services.gmail_service.GmailService.get_full_message`::

            {
                "message_id":  "...",
                "thread_id":   "...",
                "subject":     "...",
                "from":        "...",
                "to":          "...",
                "date":        "...",
                "timestamp":   "...",
                "body":        "...",
                "attachments": [{"filename": ..., "mime_type": ...,
                                 "attachment_id": ...}]
            }

    Raises:
        ValueError:           Token decryption failed (corrupt blob or
                              Fernet key mismatch).
        GmailServiceError:    Any Gmail API error (auth, rate limit,
                              network, unexpected HTTP status).
        Exception:            Any other unexpected error is propagated.
    """
    # ── Decrypt stored OAuth tokens ───────────────────────────────────────────
    logger.debug(
        "gmail_sync: decrypting tokens for recruiter_id=%s", recruiter_id
    )
    tokens: dict[str, str | None] = decrypt_oauth_tokens(encrypted_token_blob)

    access_token:  str | None = tokens.get("access_token")
    refresh_token: str | None = tokens.get("refresh_token")

    if not access_token or not refresh_token:
        raise ValueError(
            f"Incomplete OAuth tokens for recruiter_id={recruiter_id!r}: "
            f"access_token present={bool(access_token)}, "
            f"refresh_token present={bool(refresh_token)}."
        )

    # ── Async execution inside a new event loop ───────────────────────────────
    async def _run() -> dict[str, Any]:
        async with GmailService(
            access_token=access_token,
            refresh_token=refresh_token,
        ) as gmail:
            return await gmail.get_full_message(message_id)

    logger.debug(
        "gmail_sync: fetching message_id=%s for recruiter_id=%s",
        message_id, recruiter_id,
    )
    result: dict[str, Any] = asyncio.run(_run())

    logger.debug(
        "gmail_sync: fetched message_id=%s subject=%r body_len=%d attachments=%d",
        message_id,
        result.get("subject"),
        len(result.get("body", "")),
        len(result.get("attachments", [])),
    )
    return result


def download_attachment(
    *,
    recruiter_id:         str,
    message_id:           str,
    attachment_id:        str,
    encrypted_token_blob: str,
) -> bytes:
    """
    Download a Gmail attachment binary synchronously.

    Intended for use inside RQ worker tasks where no asyncio event loop
    is running.  Internally delegates to
    :meth:`~app.services.gmail_service.GmailService.download_attachment`.

    Args:
        recruiter_id:         UUID of the owning recruiter (logging only).
        message_id:           Gmail message ID that contains the attachment.
        attachment_id:        The ``attachmentId`` from the message payload.
        encrypted_token_blob: Fernet-encrypted JSON OAuth token blob.

    Returns:
        Raw attachment bytes (already decoded from base64url).

    Raises:
        ValueError:        Incomplete or undecodable OAuth tokens.
        GmailServiceError: Any Gmail API error (auth, network, rate limit).
    """
    tokens: dict[str, str | None] = decrypt_oauth_tokens(encrypted_token_blob)
    access_token:  str | None = tokens.get("access_token")
    refresh_token: str | None = tokens.get("refresh_token")

    if not access_token or not refresh_token:
        raise ValueError(
            f"Incomplete OAuth tokens for recruiter_id={recruiter_id!r}: "
            f"access_token present={bool(access_token)}, "
            f"refresh_token present={bool(refresh_token)}."
        )

    async def _run() -> bytes:
        async with GmailService(
            access_token=access_token,
            refresh_token=refresh_token,
        ) as gmail:
            return await gmail.download_attachment(message_id, attachment_id)

    logger.debug(
        "gmail_sync: downloading attachment message_id=%s attachment_id=%s "
        "recruiter_id=%s",
        message_id, attachment_id, recruiter_id,
    )
    raw_bytes: bytes = asyncio.run(_run())
    logger.debug(
        "gmail_sync: attachment downloaded size_bytes=%d "
        "message_id=%s attachment_id=%s",
        len(raw_bytes), message_id, attachment_id,
    )
    return raw_bytes
