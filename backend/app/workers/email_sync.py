"""
app/workers/email_sync.py

Synchronous bridge between RQ workers (no event loop) and the async
provider service layer (GmailService, OutlookService).

Why this file exists
--------------------
Both GmailService and OutlookService are built as async classes (they use
``httpx.AsyncClient`` internally).  RQ workers run synchronous functions
in a thread pool — there is no running asyncio event loop to await
coroutines on.

This module bridges that gap by:
  1. Resolving the correct service class from the ``provider`` argument.
  2. Decrypting the recruiter's stored OAuth tokens.
  3. Running the async provider method inside a fresh asyncio event loop
     via ``asyncio.run()`` and returning the result synchronously.

Why it was renamed from ``gmail_sync.py``
-----------------------------------------
The original ``gmail_sync.py`` name became incorrect once Outlook support
was added — a file whose name implies Gmail but dispatches to Outlook is
actively misleading.  ``email_sync.py`` accurately describes what this
module does for the lifetime of the project.

Dispatch strategy
-----------------
Provider→class mapping is centralised in ``_PROVIDER_CLASSES``.

    _PROVIDER_CLASSES: dict[str, type] = {
        "gmail":   GmailService,
        "outlook": OutlookService,
    }

Adding a third provider requires only one new entry in this dict.
Neither ``fetch_full_message`` nor ``download_attachment`` changes.

Design rules
------------
- No FastAPI objects
- No Motor / async MongoDB
- One ``asyncio.run()`` per public call (loop created + destroyed each time)
- All exceptions are propagated unchanged to the RQ task caller
- Both public functions accept ``provider: str = "gmail"`` as a keyword
  argument so that any RQ jobs enqueued before this change was deployed
  (which have only 2 positional args: recruiter_id, message_id) continue
  to work without error.

⚠️  ``asyncio.run()`` creates a new event loop per invocation.  For
    high-volume workloads, consider a persistent loop per worker thread.
    At the current batch size (≤20 messages per recruiter per cycle) this
    approach is safe and simple.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.auth_service import decrypt_oauth_tokens
from app.services.gmail_service import GmailService

logger = logging.getLogger(__name__)


def _resolve_service_class(provider: str) -> type:
    """
    Return the service class for a given provider identifier string.

    The provider registry uses lazy imports so that this module can be
    imported in Phase 1 before ``outlook_service.py`` exists.  The import
    only executes when that provider is actually requested at runtime.

    Args:
        provider: Email provider identifier — ``"gmail"`` or ``"outlook"``.

    Returns:
        The service class (not an instance) — either :class:`GmailService`
        or :class:`OutlookService`.

    Raises:
        ValueError: If ``provider`` is not a recognised value.  This is a
                    programming error (the caller should have validated the
                    provider string at the ingestion layer).
        ImportError: If the Outlook provider is requested but
                     ``outlook_service.py`` has not been deployed yet
                     (Phase 2 requirement).

    Adding a third provider
    -----------------------
    Add one ``elif`` branch returning the new service class.  The two
    public bridge functions do not change.
    """
    if provider == "gmail":
        return GmailService

    if provider == "outlook":
        # Lazy import — OutlookService is a Phase 2 file.
        # Importing it here (not at module level) allows email_sync.py to
        # be imported in Phase 1 before outlook_service.py exists.
        from app.services.outlook_service import OutlookService  # noqa: PLC0415
        return OutlookService

    valid = ["gmail", "outlook"]
    raise ValueError(
        f"Unknown email provider: {provider!r}. "
        f"Valid providers: {valid}. "
        f"Check that the recruiter's 'provider' field in MongoDB matches "
        f"one of these values."
    )


# ══════════════════════════════════════════════════════════════════════════════
# Public bridge functions
# ══════════════════════════════════════════════════════════════════════════════

def fetch_full_message(
    *,
    recruiter_id:         str,
    message_id:           str,
    encrypted_token_blob: str,
    provider:             str = "gmail",
) -> dict[str, Any]:
    """
    Fetch and return a fully normalised email message synchronously.

    Dispatches to :class:`~app.services.gmail_service.GmailService` or
    :class:`~app.services.outlook_service.OutlookService` based on
    ``provider``.  Both services implement ``get_full_message()`` and
    return the identical dict shape — callers do not need to handle
    provider differences.

    Args:
        recruiter_id:         UUID of the owning recruiter (logging only;
                              not passed to the provider API).
        message_id:           Provider-native message ID to fetch.
        encrypted_token_blob: Fernet-encrypted JSON blob from
                              ``recruiter.oauth_tokens_encrypted``.
        provider:             Email provider identifier.  Defaults to
                              ``"gmail"`` so any RQ jobs enqueued before the
                              provider argument was introduced continue to
                              work without error.

    Returns:
        Normalised message dict with guaranteed keys::

            {
                "message_id":  "...",
                "thread_id":   "...",
                "subject":     "...",
                "from":        "candidate@example.com",
                "to":          "recruiter@company.com",
                "date":        "Mon, 5 May 2026 07:00:00 +0000",
                "timestamp":   "Mon, 5 May 2026 07:00:00 +0000",
                "body":        "Hi, I am interested in the position...",
                "attachments": [
                    {
                        "filename":      "resume.pdf",
                        "mime_type":     "application/pdf",
                        "attachment_id": "ANGjdJ..."
                    }
                ]
            }

        All values are guaranteed present (may be empty string / list).

    Raises:
        ValueError:             Token decryption failed (corrupt blob or
                                Fernet key mismatch), or unknown provider.
        EmailProviderError:     Any provider API error — auth, rate limit,
                                network, unexpected HTTP status.
        Exception:              Any other unexpected error is propagated.
    """
    svc_cls = _resolve_service_class(provider)

    tokens: dict[str, str | None] = decrypt_oauth_tokens(encrypted_token_blob)
    access_token:  str | None = tokens.get("access_token")
    refresh_token: str | None = tokens.get("refresh_token")

    if not access_token or not refresh_token:
        raise ValueError(
            f"Incomplete OAuth tokens for recruiter_id={recruiter_id!r}: "
            f"access_token present={bool(access_token)}, "
            f"refresh_token present={bool(refresh_token)}."
        )

    async def _run() -> dict[str, Any]:
        async with svc_cls(
            access_token=access_token,
            refresh_token=refresh_token,
        ) as svc:
            return await svc.get_full_message(message_id)

    logger.debug(
        "email_sync: fetching message_id=%s provider=%s recruiter_id=%s",
        message_id, provider, recruiter_id,
    )
    result: dict[str, Any] = asyncio.run(_run())

    logger.debug(
        "email_sync: fetched message_id=%s provider=%s "
        "subject=%r body_len=%d attachments=%d",
        message_id,
        provider,
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
    provider:             str = "gmail",
) -> bytes:
    """
    Download a provider attachment binary synchronously.

    Dispatches to :class:`~app.services.gmail_service.GmailService` or
    :class:`~app.services.outlook_service.OutlookService` based on
    ``provider``.  Both services implement ``download_attachment()`` and
    return raw bytes — callers do not need to handle provider differences.

    Implementation note for Outlook:
        Graph API returns raw binary from the ``/$value`` endpoint.
        Unlike Gmail (which returns base64url-encoded data), no decoding
        step is required inside ``OutlookService.download_attachment()``.
        For attachments >3MB, Graph exposes a
        ``@microsoft.graph.downloadUrl`` redirect URL instead; the
        OutlookService handles both cases transparently.

    Args:
        recruiter_id:         UUID of the owning recruiter (logging only).
        message_id:           Provider-native message ID containing the
                              attachment.
        attachment_id:        Provider-native attachment ID from the message
                              payload.
        encrypted_token_blob: Fernet-encrypted JSON OAuth token blob.
        provider:             Email provider identifier.  Defaults to
                              ``"gmail"`` for backward compatibility with
                              in-flight RQ jobs.

    Returns:
        Raw attachment bytes.

    Raises:
        ValueError:          Incomplete or undecodable OAuth tokens, or
                             unknown provider.
        EmailProviderError:  Any provider API error.
    """
    svc_cls = _resolve_service_class(provider)

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
        async with svc_cls(
            access_token=access_token,
            refresh_token=refresh_token,
        ) as svc:
            return await svc.download_attachment(message_id, attachment_id)

    logger.debug(
        "email_sync: downloading attachment message_id=%s attachment_id=%s "
        "provider=%s recruiter_id=%s",
        message_id, attachment_id, provider, recruiter_id,
    )
    raw_bytes: bytes = asyncio.run(_run())

    logger.debug(
        "email_sync: attachment downloaded size_bytes=%d "
        "message_id=%s attachment_id=%s provider=%s",
        len(raw_bytes), message_id, attachment_id, provider,
    )
    return raw_bytes
