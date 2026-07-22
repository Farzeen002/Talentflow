"""
app/services/gmail_service.py

Production-grade async Gmail API interaction layer.

Responsibilities:
  - Use stored OAuth access/refresh tokens to call Gmail API
  - Transparent token refresh on 401 (invalid_grant / expired)
  - Paginated message listing  (list_messages)
  - Full message fetch + MIME parsing (get_message)
  - Basic attachment metadata extraction (no body download — Phase 3)
  - Structured exception hierarchy for upstream consumers

Design constraints:
  - Async-first: uses a single shared httpx.AsyncClient (injected or owned)
  - NO database access — pure HTTP interaction layer
  - NO FastAPI request/response objects
  - NO recruiter-level business logic

Intended usage (ingestion pipeline):
    tokens = decrypt_oauth_tokens(recruiter.oauth_tokens_encrypted)
    service = GmailService(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
    )
    async with service:
        messages = await service.list_messages(label_ids=["INBOX"])
        detail   = await service.get_message(messages["messages"][0]["id"])
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.config import get_settings
from app.services.provider_errors import (
    EmailProviderAuthError,
    EmailProviderRateLimitError,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Gmail / Google API base URLs ──────────────────────────────────────────────
_GMAIL_BASE      = "https://gmail.googleapis.com/gmail/v1/users/me"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# ── HTTP timeouts (config-driven, with hardcoded fallback defaults) ───────────
_CONNECT_TIMEOUT: float = getattr(settings, "GMAIL_CONNECT_TIMEOUT", 10.0)
_READ_TIMEOUT:    float = getattr(settings, "GMAIL_READ_TIMEOUT", 30.0)
_DEFAULT_TIMEOUT  = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)


# ── Gmail label for NVite candidate emails ──────────────────────────────────
# Human-readable label name as it appears in the recruiter's Gmail account.
# GmailService.get_label_id() resolves this to the opaque Gmail label ID
# (e.g. "Label_123456789") before the API call.
# Kept here (rather than in ingestion_service.py) because it is a Gmail
# concept: it belongs with the Gmail API client, not the orchestrator.
_NVITE_LABEL: str = "Nvite"


# ══════════════════════════════════════════════════════════════════════════════
# Structured exception hierarchy
# ══════════════════════════════════════════════════════════════════════════════

class GmailServiceError(Exception):
    """Base exception for all GmailService errors."""


class GmailAuthError(GmailServiceError, EmailProviderAuthError):
    """
    Raised when authentication fails and token refresh cannot recover it.

    Inherits from both ``GmailServiceError`` (preserving the existing
    exception hierarchy for any code that catches ``GmailServiceError``)
    and ``EmailProviderAuthError`` (allowing ``ingestion_service.py`` to
    catch auth failures from any provider without listing each class).
    """


class GmailPermissionError(GmailServiceError):
    """Raised on HTTP 403 — insufficient OAuth scopes or access denied."""


class GmailRateLimitError(GmailServiceError, EmailProviderRateLimitError):
    """
    Raised on HTTP 429 — upstream caller should back off and retry.

    Inherits from both ``GmailServiceError`` and
    ``EmailProviderRateLimitError`` for the same dual-catchability reason
    as ``GmailAuthError``.
    """


class GmailNetworkError(GmailServiceError):
    """Raised on transport-level failures (timeout, DNS, connection reset)."""


class GmailAPIError(GmailServiceError):
    """Raised for unexpected non-2xx responses not covered by other types."""
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"Gmail API error {status_code}: {detail}")


class GmailLabelNotFoundError(GmailServiceError):
    """Raised when a named label cannot be found in the Gmail account."""
    def __init__(self, label_name: str) -> None:
        self.label_name = label_name
        super().__init__(
            f"Label {label_name!r} not found in Gmail account. "
            "Ensure the label exists and the OAuth scope includes gmail.readonly."
        )


# ══════════════════════════════════════════════════════════════════════════════
# Parsed data types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class AttachmentMeta:
    """Lightweight attachment descriptor (body not downloaded until Phase 3)."""
    filename:      str
    attachment_id: str
    mime_type:     str
    size:          int = 0


@dataclass
class ParsedMessage:
    """Fully parsed representation of a single Gmail message."""
    id:        str
    thread_id: str
    snippet:   str
    headers:   dict[str, str]        = field(default_factory=dict)
    body:      str                   = ""
    attachments: list[AttachmentMeta] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id":          self.id,
            "thread_id":   self.thread_id,
            "snippet":     self.snippet,
            "headers":     self.headers,
            "body":        self.body,
            "attachments": [
                {
                    "filename":      a.filename,
                    "attachment_id": a.attachment_id,
                    "mime_type":     a.mime_type,
                    "size":          a.size,
                }
                for a in self.attachments
            ],
        }


# ══════════════════════════════════════════════════════════════════════════════
# GmailService
# ══════════════════════════════════════════════════════════════════════════════

class GmailService:
    """
    Async Gmail API client with transparent token refresh.

    Lifecycle
    ---------
    Preferred usage is as an async context manager so the underlying
    ``httpx.AsyncClient`` is cleanly closed:

        async with GmailService(access_token=..., refresh_token=...) as svc:
            result = await svc.list_messages()

    Alternatively call ``await service.aclose()`` explicitly.

    Thread-safety
    -------------
    A single instance should not be shared across concurrent coroutines
    without external locking because token refresh mutates ``_access_token``
    in-place.  For concurrent ingestion, create one instance per task.
    """

    def __init__(
        self,
        access_token:  str,
        refresh_token: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Initialise the service.

        Args:
            access_token:  Current Google OAuth access token.
            refresh_token: Long-lived refresh token used to obtain new access tokens.
            client:        Optional pre-built ``httpx.AsyncClient``.  When
                           omitted the service creates and owns its own client.
        """
        if not access_token:
            raise ValueError("access_token must not be empty.")
        if not refresh_token:
            raise ValueError("refresh_token must not be empty.")

        self._access_token:  str = access_token
        self._refresh_token: str = refresh_token

        # Guard: prevents a second coroutine from entering refresh concurrently
        self._is_refreshing: bool = False

        # Per-instance label name → label ID cache.
        # Populated lazily by get_label_id(); avoids a labels.list API call
        # on every ingestion cycle.  Cleared only when the instance is replaced.
        self._label_cache: dict[str, str] = {}

        self._owns_client = client is None
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    # ── Async context-manager protocol ───────────────────────────────────────

    async def __aenter__(self) -> "GmailService":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()

    # =========================================================================
    # Public API
    # =========================================================================

    def get_current_access_token(self) -> str:
        """
        Return the current (possibly refreshed) access token.

        The ingestion layer should call this after any Gmail operation to
        detect whether a refresh occurred and persist the new token to DB::

            await service.list_messages(...)
            new_token = service.get_current_access_token()
            # compare with original; if different, update DB

        Returns:
            The in-memory access token string.
        """
        return self._access_token

    def get_current_refresh_token(self) -> str:
        """
        Return the current (possibly rotated) refresh token.

        The ingestion layer should call this after any email operation to
        detect whether rotation occurred and persist the new token to DB.

        Returns:
            The in-memory refresh token string.
        """
        return self._refresh_token

    async def list_new_messages(self, max_results: int = 20) -> list[str]:
        """
        Return message IDs from the recruiter's NVite Gmail label.

        This is the canonical ingestion-layer entry point.  It internally:
          1. Resolves ``_NVITE_LABEL`` (``"Nvite"``) to its opaque Gmail
             label ID via :meth:`get_label_id` (cached per instance).
          2. Calls :meth:`list_messages` with that resolved ID.
          3. Returns a flat list of message ID strings.

        Callers receive a consistent ``list[str]`` interface regardless of
        the underlying label resolution complexity.  The Outlook equivalent
        (``OutlookService.list_new_messages``) returns the same shape.

        Args:
            max_results: Maximum number of message IDs to return (default 20).

        Returns:
            List of Gmail message ID strings.  Empty list if the label
            contains no messages or the label does not exist.

        Raises:
            GmailLabelNotFoundError: The ``Nvite`` label does not exist.
            GmailAuthError:          Credentials invalid after refresh.
            GmailPermissionError:    Insufficient OAuth scopes.
            GmailRateLimitError:     API quota exceeded.
            GmailNetworkError:       Transport-level failure.
            GmailAPIError:           Other non-2xx response.
        """
        response = await self.list_messages(
            label_names=[_NVITE_LABEL],
            max_results=max_results,
        )
        return [
            ref["id"]
            for ref in (response.get("messages") or [])
            if ref.get("id")
        ]

    async def get_label_id(self, label_name: str) -> str:
        """
        Resolve a Gmail label name to its opaque label ID.

        Gmail API ``users.messages.list`` requires label **IDs** (e.g.
        ``"Label_123456789"``), not human-readable names (e.g. ``"NVite"``).
        This method maps a name to its ID by calling
        ``GET /gmail/v1/users/me/labels`` and searching the result.

        Results are cached in ``self._label_cache`` for the lifetime of
        this service instance to avoid redundant API calls on every
        ingestion cycle.

        Args:
            label_name: Exact case-sensitive label name as it appears in Gmail
                        (e.g. ``"NVite"``, ``"INBOX"``, ``"UNREAD"``).

        Returns:
            The opaque label ID string (e.g. ``"Label_123456789"``).

        Raises:
            GmailLabelNotFoundError: The label does not exist in the Gmail
                                     account, or the labels list response is
                                     empty / malformed.
            GmailAuthError:          Credentials invalid even after refresh.
            GmailPermissionError:    Insufficient OAuth scopes.
            GmailRateLimitError:     API quota exceeded.
            GmailNetworkError:       Transport-level failure.
            GmailAPIError:           Other non-2xx response.
        """
        # ── Cache hit ─────────────────────────────────────────────────────────
        if label_name in self._label_cache:
            logger.debug(
                "event=gmail.label_cache_hit label_name=%r label_id=%r",
                label_name, self._label_cache[label_name],
            )
            return self._label_cache[label_name]

        # ── Fetch labels list from Gmail API ──────────────────────────────────
        logger.info(
            "event=gmail.label_lookup_start label_name=%r",
            label_name,
        )
        url = f"{_GMAIL_BASE}/labels"
        response = await self._request_with_refresh("GET", url)

        data: dict[str, Any] = response.json()
        labels: list[dict[str, Any]] = data.get("labels") or []

        if not labels:
            logger.error(
                "event=gmail.label_not_found label_name=%r "
                "reason=empty_labels_response",
                label_name,
            )
            raise GmailLabelNotFoundError(label_name)

        # ── Search for exact name match ────────────────────────────────────────
        for label in labels:
            if label.get("name") == label_name:
                label_id: str = label["id"]
                self._label_cache[label_name] = label_id
                logger.info(
                    "event=gmail.label_resolved label_name=%r label_id=%r",
                    label_name, label_id,
                )
                return label_id

        # ── Label not found — log available names to aid diagnostics ──────────
        available = [lbl.get("name", "") for lbl in labels if lbl.get("name")]
        logger.error(
            "event=gmail.label_not_found label_name=%r available_labels=%s",
            label_name, available,
        )
        raise GmailLabelNotFoundError(label_name)

    async def list_messages(
        self,
        label_ids:   list[str] | None = None,
        label_names: list[str] | None = None,
        max_results: int = 50,
        page_token:  str | None = None,
        query:       str | None = None,
    ) -> dict[str, Any]:
        """
        List Gmail message references for the authenticated user.

        Args:
            label_ids:   Filter by **label IDs** (e.g. ``["INBOX"]``, ``["UNREAD"]``).
                         These must be opaque Gmail label ID strings.
            label_names: Filter by **label names** (e.g. ``["NVite"]``).
                         Each name is resolved to its label ID via
                         :meth:`get_label_id` before the API call.  Resolved IDs
                         are merged with any IDs supplied via ``label_ids``.
                         Use this parameter instead of ``label_ids`` when you
                         have a human-readable label name.
            max_results: Maximum number of messages to return (1–500, Gmail cap).
            page_token:  Token from a previous response to retrieve the next page.
            query:       Gmail search query string (same syntax as the Gmail search bar).

        Returns:
            Dict matching Gmail ``users.messages.list`` response shape::

                {
                    "messages":      [{"id": "...", "threadId": "..."}, ...],
                    "nextPageToken": "...",   # absent when no further pages
                    "resultSizeEstimate": 42
                }

        Raises:
            GmailLabelNotFoundError: A name in ``label_names`` cannot be resolved.
            GmailAuthError:          Credentials invalid even after refresh attempt.
            GmailPermissionError:    Insufficient OAuth scopes.
            GmailRateLimitError:     API quota exceeded.
            GmailNetworkError:       Transport-level failure.
            GmailAPIError:           Other non-2xx response.
        """
        # ── Resolve label names → IDs ─────────────────────────────────────────
        resolved_ids: list[str] = list(label_ids or [])
        for name in (label_names or []):
            resolved_ids.append(await self.get_label_id(name))

        params: dict[str, Any] = {"maxResults": max_results}
        if resolved_ids:
            params["labelIds"] = resolved_ids
        if page_token:
            params["pageToken"] = page_token
        if query:
            params["q"] = query

        url = f"{_GMAIL_BASE}/messages"
        response = await self._request_with_refresh("GET", url, params=params)
        data: dict[str, Any] = response.json()

        logger.debug(
            "list_messages → %d messages, nextPageToken=%s",
            len(data.get("messages") or []),
            data.get("nextPageToken"),
        )
        return data

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """
        Fetch and parse a single Gmail message by ID.

        Performs MIME traversal to extract plain-text body (falls back to
        HTML if no ``text/plain`` part exists) and collects attachment
        metadata from all parts that carry an ``attachmentId``.

        Args:
            message_id: Gmail message ID (from :meth:`list_messages`).

        Returns:
            Parsed message dict::

                {
                    "id":        "...",
                    "thread_id": "...",
                    "snippet":   "...",
                    "headers": {
                        "from":    "...",
                        "to":      "...",
                        "subject": "...",
                        "date":    "..."
                    },
                    "body":        "...",
                    "attachments": [
                        {
                            "filename":      "resume.pdf",
                            "attachment_id": "...",
                            "mime_type":     "application/pdf",
                            "size":          12345
                        },
                        ...
                    ]
                }

        Raises:
            GmailAuthError:       Credentials invalid even after refresh attempt.
            GmailPermissionError: Insufficient OAuth scopes.
            GmailRateLimitError:  API quota exceeded.
            GmailNetworkError:    Transport-level failure.
            GmailAPIError:        Other non-2xx response.
        """
        logger.debug("Fetching message_id=%s", message_id)
        url = f"{_GMAIL_BASE}/messages/{message_id}"
        response = await self._request_with_refresh(
            "GET", url, params={"format": "full"}
        )
        raw: dict[str, Any] = response.json()
        parsed = self._parse_message(raw)
        logger.debug(
            "get_message(%s) complete — subject=%r attachments=%d body_len=%d",
            message_id,
            parsed.headers.get("subject"),
            len(parsed.attachments),
            len(parsed.body),
        )
        return parsed.to_dict()

    async def download_attachment(
        self,
        message_id:    str,
        attachment_id: str,
    ) -> bytes:
        """
        Download the raw bytes of a Gmail attachment.

        Calls ``GET /gmail/v1/users/me/messages/{messageId}/attachments/{id}``.
        The API returns a base64url-encoded ``data`` field which is decoded
        before returning.

        Args:
            message_id:    Gmail message ID that contains the attachment part.
            attachment_id: The ``attachmentId`` from the message payload body.

        Returns:
            Raw attachment bytes (decoded from base64url).

        Raises:
            GmailAuthError:       Credentials invalid even after refresh attempt.
            GmailPermissionError: Insufficient OAuth scopes.
            GmailRateLimitError:  API quota exceeded.
            GmailNetworkError:    Transport-level failure.
            GmailAPIError:        Unexpected non-2xx response, empty data field,
                                  or base64 decoding failure.
        """
        logger.debug(
            "download_attachment: message_id=%s attachment_id=%s",
            message_id, attachment_id,
        )
        url = f"{_GMAIL_BASE}/messages/{message_id}/attachments/{attachment_id}"
        response = await self._request_with_refresh("GET", url)
        data: dict[str, Any] = response.json()

        raw_data: str = data.get("data", "")
        if not raw_data:
            raise GmailAPIError(
                200,
                f"Attachment data field is empty for "
                f"message_id={message_id!r} attachment_id={attachment_id!r}.",
            )

        try:
            padding   = "=" * (-len(raw_data) % 4)
            raw_bytes = base64.urlsafe_b64decode(raw_data + padding)
        except Exception as exc:
            raise GmailAPIError(
                200,
                f"base64 decode failed for attachment_id={attachment_id!r}: {exc}",
            ) from exc

        logger.debug(
            "download_attachment: decoded %d bytes "
            "message_id=%s attachment_id=%s",
            len(raw_bytes), message_id, attachment_id,
        )
        return raw_bytes

    async def get_full_message(self, message_id: str) -> dict[str, Any]:
        """
        Fetch a Gmail message and return an ingestion-ready structured dict.

        This method is the canonical entry point for the worker pipeline
        (Phase 3).  It differs from :meth:`get_message` in three ways:

        1. The return shape matches the contract expected by the candidate
           parser — flat top-level keys, no nesting under ``"headers"``.
        2. The ``body`` field always contains **plain text**: ``text/plain``
           is preferred; if absent, ``text/html`` content is stripped of all
           HTML tags before being returned.
        3. ``attachments`` contains only *real* attachments (those that carry
           an ``attachmentId`` from Gmail).  Inline images and CID parts are
           excluded.

        Args:
            message_id: Gmail API message ID obtained from :meth:`list_messages`.

        Returns:
            Dict with the following guaranteed keys (all values may be empty
            strings / empty lists if the corresponding data is absent)::

                {
                    "message_id":  "18e4a1b2c3d...",
                    "thread_id":   "18e4a1b2c3d...",
                    "subject":     "Re: Your application",
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

        Raises:
            GmailAuthError:       Credentials invalid even after refresh attempt.
            GmailPermissionError: Insufficient OAuth scopes.
            GmailRateLimitError:  API quota exceeded.
            GmailNetworkError:    Transport-level failure.
            GmailAPIError:        Other non-2xx response.
        """
        logger.debug("get_full_message: fetching message_id=%s", message_id)

        url = f"{_GMAIL_BASE}/messages/{message_id}"
        response = await self._request_with_refresh(
            "GET", url, params={"format": "full"}
        )
        raw: dict[str, Any] = response.json()

        payload: dict[str, Any] = raw.get("payload", {})

        # ── Extract headers ───────────────────────────────────────────────────
        headers = GmailService._extract_headers(payload)

        # ── Extract body (plain text preferred, safe HTML strip fallback) ─────
        plain_parts: list[str] = []
        html_parts:  list[str] = []
        attachments: list[dict[str, str]] = []

        GmailService._walk_full_parts(payload, plain_parts, html_parts, attachments)

        if plain_parts:
            body = "\n".join(plain_parts).strip()
        elif html_parts:
            # Strip all HTML tags to produce readable plain text.
            raw_html = "\n".join(html_parts)
            body = GmailService._strip_html_tags(raw_html).strip()
        else:
            body = ""
            logger.debug(
                "get_full_message: no body found for message_id=%s", message_id
            )

        result: dict[str, Any] = {
            "message_id":  raw.get("id", ""),
            "thread_id":   raw.get("threadId", ""),
            "subject":     headers.get("subject", ""),
            "from":        headers.get("from", ""),
            "to":          headers.get("to", ""),
            "date":        headers.get("date", ""),
            "timestamp":   headers.get("date", ""),   # alias for downstream parsers
            "body":        body,
            "attachments": attachments,
        }

        logger.debug(
            "get_full_message(%s) — subject=%r body_len=%d attachments=%d",
            message_id,
            result["subject"],
            len(result["body"]),
            len(result["attachments"]),
        )
        return result

    # =========================================================================
    # Internal helpers
    # =========================================================================

    # ── Authorization header ──────────────────────────────────────────────────

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    # ── Core request dispatcher with single-retry on 401 ─────────────────────

    async def _request_with_refresh(
        self,
        method: str,
        url: str,
        *,
        params:  dict[str, Any] | None = None,
        json:    dict[str, Any] | None = None,
        _retry:  bool = True,
    ) -> httpx.Response:
        """
        Execute an HTTP request, refreshing the access token once on 401.

        Args:
            method:  HTTP verb (``"GET"``, ``"POST"``, etc.).
            url:     Full request URL.
            params:  Query-string parameters.
            json:    JSON request body.
            _retry:  Internal flag — set to ``False`` on the recursive retry
                     to prevent infinite loops.

        Returns:
            The successful ``httpx.Response``.

        Raises:
            GmailAuthError:       401 persists after token refresh.
            GmailPermissionError: 403 response.
            GmailRateLimitError:  429 response.
            GmailNetworkError:    Transport failure.
            GmailAPIError:        Any other non-2xx response.
        """
        try:
            response = await self._client.request(
                method,
                url,
                headers=self._auth_headers,
                params=params,
                json=json,
            )
        except httpx.TimeoutException as exc:
            logger.warning("Gmail API request timed out: %s %s — %s", method, url, exc)
            raise GmailNetworkError(
                f"Request timed out: {method} {url}"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "Gmail API network error: %s %s — %s", method, url, exc
            )
            if _retry:
                # Exponential backoff before single retry on transient network error
                logger.info(
                    "Network error on %s %s — backing off 1.5s then retrying.",
                    method, url,
                )
                await asyncio.sleep(1.5)
                return await self._request_with_refresh(
                    method, url, params=params, json=json, _retry=False
                )
            raise GmailNetworkError(
                f"Network failure after retry: {method} {url}"
            ) from exc

        # ── Handle HTTP status codes ──────────────────────────────────────────
        if response.status_code == 401:
            if _retry:
                logger.info(
                    "Received 401 from Gmail API — attempting token refresh. "
                    "URL: %s", url
                )
                new_token = await self._refresh_access_token()
                self._access_token = new_token
                return await self._request_with_refresh(
                    method, url, params=params, json=json, _retry=False
                )
            # Refresh happened but still 401 → credentials permanently invalid
            logger.error(
                "Gmail API returned 401 after token refresh. "
                "Refresh token may be revoked. URL: %s", url
            )
            raise GmailAuthError(
                "Access denied: credentials invalid even after token refresh. "
                "The recruiter may need to re-authenticate."
            )

        if response.status_code == 403:
            logger.error(
                "Gmail API 403 Forbidden for %s %s — check OAuth scopes.", method, url
            )
            raise GmailPermissionError(
                "Permission denied: verify that the Gmail readonly scope is granted."
            )

        if response.status_code == 429:
            retry_after: int = int(response.headers.get("Retry-After", "1"))
            logger.warning(
                "Gmail API rate limit hit: %s %s — Retry-After=%ds, _retry=%s",
                method, url, retry_after, _retry,
            )
            if _retry:
                logger.info(
                    "Sleeping %ds before rate-limit retry: %s %s",
                    retry_after, method, url,
                )
                await asyncio.sleep(retry_after)
                return await self._request_with_refresh(
                    method, url, params=params, json=json, _retry=False
                )
            raise GmailRateLimitError(
                f"Gmail API quota exceeded after retry (Retry-After={retry_after}s). "
                "Back off and retry later."
            )

        if not response.is_success:
            logger.error(
                "Gmail API unexpected %d for %s %s: %s",
                response.status_code, method, url, response.text[:500],
            )
            raise GmailAPIError(response.status_code, response.text[:300])

        return response

    # ── Token refresh ─────────────────────────────────────────────────────────

    async def _refresh_access_token(self) -> str:
        """
        Exchange the stored refresh token for a new access token.

        Unpacks the tuple returned by :meth:`_do_refresh`.  If a new refresh
        token was included in the response, ``self._refresh_token`` is updated
        in-memory immediately and a rotation event is logged.  Callers receive
        only the new access token string — the return type is unchanged.

        Concurrency guard: raises immediately if another refresh is already
        in progress on this instance (concurrent use is unsupported).

        Raises:
            GmailAuthError:    Concurrent refresh detected, or refresh endpoint
                               returns a non-2xx status (revoked / expired token).
            GmailNetworkError: Cannot reach Google token endpoint.

        Returns:
            The new access token string.
        """
        # ── Concurrency guard ─────────────────────────────────────────────────
        if self._is_refreshing:
            logger.error(
                "Concurrent token refresh detected — this instance is not "
                "safe for concurrent use."
            )
            raise GmailAuthError(
                "Concurrent token refresh detected. "
                "Use a separate GmailService instance per concurrent task."
            )

        self._is_refreshing = True
        try:
            new_access_token, new_refresh_token = await self._do_refresh()
        finally:
            self._is_refreshing = False

        if new_refresh_token is not None:
            self._refresh_token = new_refresh_token
            logger.info("event=gmail.refresh_token_rotated")

        return new_access_token

    async def _do_refresh(self) -> tuple[str, str | None]:
        """
        Internal: perform the actual token-refresh HTTP call.

        Called exclusively from :meth:`_refresh_access_token` after the
        concurrency guard is set.  Never call this directly.

        Returns:
            A tuple of ``(new_access_token, new_refresh_token_or_None)``.
            ``new_refresh_token_or_None`` is ``None`` when the provider did
            not include a ``refresh_token`` field in the response (the normal
            case for Google, which reuses the same token).
        """
        payload: dict[str, str] = {
            "client_id":     settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "refresh_token": self._refresh_token,
            "grant_type":    "refresh_token",
        }

        logger.info("Requesting new access token from Google token endpoint.")

        try:
            response = await self._client.post(
                _GOOGLE_TOKEN_URL,
                data=payload,
                # No Bearer token needed for this call
                headers={"Accept": "application/json"},
            )
        except httpx.RequestError as exc:
            logger.error("Network error reaching token refresh endpoint: %s", exc)
            raise GmailNetworkError(
                "Could not reach Google token endpoint for refresh."
            ) from exc

        if not response.is_success:
            error_detail = ""
            try:
                body = response.json()
                error_detail = body.get("error_description") or body.get("error") or ""
            except Exception:
                error_detail = response.text[:200]

            logger.error(
                "Token refresh failed (%d): %s", response.status_code, error_detail
            )
            raise GmailAuthError(
                f"Token refresh failed ({response.status_code}): {error_detail}. "
                "The recruiter must re-authenticate."
            )

        data = response.json()
        new_token: str | None = data.get("access_token")
        if not new_token:
            logger.error("Token refresh response missing 'access_token' field.")
            raise GmailAuthError(
                "Token refresh succeeded but response contained no access_token."
            )

        new_refresh_token: str | None = data.get("refresh_token") or None

        logger.info("Access token refreshed successfully.")
        return new_token, new_refresh_token

    # ── MIME parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_message(raw: dict[str, Any]) -> ParsedMessage:
        """
        Convert a raw Gmail API message object to a :class:`ParsedMessage`.

        Args:
            raw: Full Gmail API ``Message`` resource (``format=full``).

        Returns:
            Structured :class:`ParsedMessage` instance.
        """
        msg = ParsedMessage(
            id=raw.get("id", ""),
            thread_id=raw.get("threadId", ""),
            snippet=raw.get("snippet", ""),
        )

        payload: dict[str, Any] = raw.get("payload", {})

        # ── Extract headers ───────────────────────────────────────────────────
        msg.headers = GmailService._extract_headers(payload)

        # ── Extract body and attachments (recursive MIME walk) ────────────────
        plain_parts: list[str] = []
        html_parts:  list[str] = []
        attachments: list[AttachmentMeta] = []

        GmailService._walk_parts(payload, plain_parts, html_parts, attachments)

        if plain_parts:
            msg.body = "\n".join(plain_parts)
        elif html_parts:
            # Fallback to raw HTML when no plain-text part exists
            msg.body = "\n".join(html_parts)

        msg.attachments = attachments
        return msg

    @staticmethod
    def _extract_headers(payload: dict[str, Any]) -> dict[str, str]:
        """
        Build a normalised header dict from the Gmail payload.

        Only the four headers relevant to ingestion are extracted:
        ``from``, ``to``, ``subject``, ``date``.

        Args:
            payload: The ``payload`` field of a Gmail API message resource.

        Returns:
            Dict with lowercase header names as keys.
        """
        _WANTED: set[str] = {"from", "to", "subject", "date"}
        headers: dict[str, str] = {}

        for header in payload.get("headers", []):
            name: str = header.get("name", "").lower()
            if name in _WANTED:
                headers[name] = header.get("value", "")

        return headers

    @staticmethod
    def _walk_parts(
        part:        dict[str, Any],
        plain_parts: list[str],
        html_parts:  list[str],
        attachments: list[AttachmentMeta],
    ) -> None:
        """
        Recursively traverse a MIME part tree.

        Populates ``plain_parts``, ``html_parts``, and ``attachments`` in place.

        Args:
            part:        Current MIME part (may contain ``parts`` sub-list).
            plain_parts: Accumulator for decoded ``text/plain`` content.
            html_parts:  Accumulator for decoded ``text/html`` content.
            attachments: Accumulator for :class:`AttachmentMeta` objects.
        """
        mime_type: str = part.get("mimeType", "")
        body:      dict[str, Any] = part.get("body", {})
        filename:  str = part.get("filename", "")

        # ── Attachment detection ──────────────────────────────────────────────
        attachment_id: str | None = body.get("attachmentId")
        if attachment_id and filename:
            attachments.append(
                AttachmentMeta(
                    filename=filename,
                    attachment_id=attachment_id,
                    mime_type=mime_type,
                    size=body.get("size", 0),
                )
            )
            # Do NOT attempt to decode the body of attachment parts
            return

        # ── Inline body decoding ──────────────────────────────────────────────
        raw_data: str | None = body.get("data")
        if raw_data:
            try:
                # Correct padding: add exactly as many '=' chars as needed
                padding = "=" * (-len(raw_data) % 4)
                decoded = base64.urlsafe_b64decode(
                    raw_data + padding
                ).decode("utf-8", errors="replace")
            except Exception:
                decoded = ""

            if mime_type == "text/plain":
                plain_parts.append(decoded)
            elif mime_type == "text/html":
                html_parts.append(decoded)

        # ── Recurse into sub-parts ────────────────────────────────────────────
        for sub_part in part.get("parts", []):
            GmailService._walk_parts(sub_part, plain_parts, html_parts, attachments)

    # ── Helpers for get_full_message() ────────────────────────────────────────

    @staticmethod
    def _walk_full_parts(
        part:        dict[str, Any],
        plain_parts: list[str],
        html_parts:  list[str],
        attachments: list[dict[str, str]],
    ) -> None:
        """
        Recursively traverse a MIME part tree for :meth:`get_full_message`.

        Differs from :meth:`_walk_parts` in that attachments are returned as
        plain dicts (matching the ``get_full_message`` return contract) and
        only *real* attachments — those carrying a Gmail ``attachmentId`` —
        are included.  Inline images embedded via ``Content-ID`` are excluded.

        Populates ``plain_parts``, ``html_parts``, and ``attachments`` in place.

        Args:
            part:        Current MIME part (may contain a ``parts`` sub-list).
            plain_parts: Accumulator for decoded ``text/plain`` content strings.
            html_parts:  Accumulator for decoded ``text/html`` content strings.
            attachments: Accumulator for attachment metadata dicts.
        """
        mime_type: str           = part.get("mimeType", "")
        body:      dict[str, Any] = part.get("body", {})
        filename:  str           = part.get("filename", "")

        # ── Real attachment: has both a filename and an attachmentId ──────────
        attachment_id: str | None = body.get("attachmentId")
        if attachment_id and filename:
            attachments.append(
                {
                    "filename":      filename,
                    "mime_type":     mime_type,
                    "attachment_id": attachment_id,
                }
            )
            # Do NOT attempt to decode the body of attachment parts.
            return

        # ── Inline body decoding ──────────────────────────────────────────────
        raw_data: str | None = body.get("data")
        if raw_data:
            decoded = GmailService._decode_base64_body(raw_data)
            if mime_type == "text/plain":
                plain_parts.append(decoded)
            elif mime_type == "text/html":
                html_parts.append(decoded)

        # ── Recurse into sub-parts ────────────────────────────────────────────
        for sub_part in part.get("parts", []):
            GmailService._walk_full_parts(
                sub_part, plain_parts, html_parts, attachments
            )

    @staticmethod
    def _decode_base64_body(raw_data: str) -> str:
        """
        Decode a Gmail API base64url-encoded body string to plain text.

        Gmail uses URL-safe base64 (``-`` and ``_`` instead of ``+`` / ``/``)
        and omits padding characters.  This function restores correct padding
        before decoding.

        Encoding fallback chain:
          1. UTF-8 (covers the vast majority of emails).
          2. Latin-1 / ISO-8859-1 (common for older Western European senders).
          3. ASCII with ``errors="replace"`` (last resort — never raises).

        Args:
            raw_data: URL-safe base64 string from Gmail API ``body.data`` field.

        Returns:
            Decoded text string, or an empty string on any decoding failure.
        """
        if not raw_data:
            return ""

        try:
            padding   = "=" * (-len(raw_data) % 4)
            raw_bytes = base64.urlsafe_b64decode(raw_data + padding)
        except Exception as exc:
            logger.debug("_decode_base64_body: base64 decode failed — %s", exc)
            return ""

        for encoding in ("utf-8", "latin-1"):
            try:
                return raw_bytes.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue

        # Final fallback: replace undecodable bytes with the Unicode placeholder.
        return raw_bytes.decode("ascii", errors="replace")

    @staticmethod
    def _strip_html_tags(html: str) -> str:
        """
        Remove HTML tags and normalise whitespace from an HTML string.

        Used as a safe fallback when no ``text/plain`` body part is present.
        Does NOT use an external HTML parser (e.g. BeautifulSoup) to avoid
        introducing new dependencies.

        Strategy:
          1. Replace closing block-level tags with newlines to preserve
             paragraph structure.
          2. Replace self-closing ``<br/>`` tags with newlines.
          3. Strip all remaining HTML tags via a simple regex.
          4. Decode common HTML entities.
          5. Collapse consecutive blank lines and strip leading/trailing space.

        Args:
            html: Raw HTML string (may contain inline CSS, scripts, etc.).

        Returns:
            Plain-text string.  Returns an empty string if ``html`` is empty.
        """
        import re

        if not html:
            return ""

        # Preserve paragraph breaks at block-level closing tags.
        block_end = re.compile(
            r"</(p|div|li|tr|td|th|h[1-6]|blockquote|pre)\s*>",
            re.IGNORECASE,
        )
        text = block_end.sub("\n", html)

        # Self-closing <br> variants.
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)

        # Strip all remaining tags.
        text = re.sub(r"<[^>]+>", "", text)

        # Decode the most common HTML entities.
        for entity, char in (
            ("&amp;",  "&"),
            ("&lt;",   "<"),
            ("&gt;",   ">"),
            ("&quot;", '"'),
            ("&#39;",  "'"),
            ("&apos;", "'"),
            ("&nbsp;", " "),
        ):
            text = text.replace(entity, char)

        # Collapse 3+ consecutive newlines → two (one visible blank line).
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

