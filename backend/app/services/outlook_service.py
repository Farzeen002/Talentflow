"""
app/services/outlook_service.py

Production-grade async Microsoft Graph API client for Outlook email access.

Responsibilities
----------------
- Use stored Microsoft OAuth access/refresh tokens to call Microsoft Graph API
- Transparent token refresh on 401 (expired / revoked access token)
- NVite folder resolution via mailFolders API (folder name read from config)
- Paginated message listing (list_new_messages)
- Full message fetch + normalization to provider-agnostic dict shape
- Attachment download with >3MB redirect URL handling
- Structured exception hierarchy that inherits from provider_errors bases

Design constraints
------------------
- Async-first: uses a single shared httpx.AsyncClient (injected or owned)
- Interface contract mirrors GmailService exactly for read paths:
    - get_current_access_token() → str
    - list_new_messages(max_results) → list[str]
    - get_full_message(message_id) → dict[str, Any]  (same shape as GmailService)
    - download_attachment(message_id, attachment_id) → bytes
- Outbound extension (Daily Reports):
    - send_mail(subject, html_body, to_recipients, cc_recipients=...) → None
- NO database access — pure HTTP interaction layer
- NO FastAPI request/response objects
- NO recruiter-level business logic / report status updates

Folder resolution
-----------------
The recruiter must create an Outlook folder named exactly as configured in
``settings.OUTLOOK_NVITE_FOLDER`` (default ``"Nvite"``) and set up an Outlook
rule to route NVite candidate emails into it.  This service resolves the
folder's display name to its opaque Graph folder ID via the ``/mailFolders``
endpoint and caches the result per instance.

Token refresh
-------------
Microsoft identity platform issues short-lived access tokens (~1 hour).
On any 401 response, this service performs a single token refresh via the
``/token`` endpoint and retries the original request.  If the 401 persists
after refresh the recruiter must re-authenticate.

Microsoft Graph API reference
------------------------------
- Messages: https://learn.microsoft.com/en-us/graph/api/resources/message
- MailFolders: https://learn.microsoft.com/en-us/graph/api/resources/mailfolder
- Attachments: https://learn.microsoft.com/en-us/graph/api/attachment-get
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import formatdate
from typing import Any

import httpx

from app.config import get_settings
from app.services.provider_errors import (
    EmailProviderAuthError,
    EmailProviderRateLimitError,
)

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Microsoft API base URLs ───────────────────────────────────────────────────
_GRAPH_BASE    = "https://graph.microsoft.com/v1.0"
# Token base URL: tenant-specific endpoint is built at runtime from
# settings.MICROSOFT_TENANT_ID (/{tenant_id}/ instead of /common/).
# This restricts token refresh to Infomatics Corp Azure AD accounts only.
_MS_TOKEN_BASE = "https://login.microsoftonline.com"

# ── HTTP timeouts ─────────────────────────────────────────────────────────────
_CONNECT_TIMEOUT: float = 10.0
_READ_TIMEOUT:    float = 30.0
_DEFAULT_TIMEOUT  = httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT)

# ── Graph API field selects ───────────────────────────────────────────────────
# Request only the fields we use — reduces payload size and latency.
_MESSAGE_FIELDS = (
    "id,conversationId,subject,from,toRecipients,"
    "receivedDateTime,body,hasAttachments"
)


# ══════════════════════════════════════════════════════════════════════════════
# Structured exception hierarchy
# ══════════════════════════════════════════════════════════════════════════════

class OutlookServiceError(Exception):
    """Base exception for all OutlookService errors."""


class OutlookAuthError(OutlookServiceError, EmailProviderAuthError):
    """
    401 persists after token refresh — recruiter must re-authenticate.

    Inherits from both ``OutlookServiceError`` (existing hierarchy for
    internal callers) and ``EmailProviderAuthError`` (allows the ingestion
    layer to catch auth failures from any provider with a single clause).
    """


class OutlookPermissionError(OutlookServiceError):
    """403 — insufficient OAuth scopes or delegated permission not granted."""


class OutlookRateLimitError(OutlookServiceError, EmailProviderRateLimitError):
    """
    429 — Microsoft Graph API throttling limit exceeded.

    Inherits from ``EmailProviderRateLimitError`` so the ingestion layer
    can catch rate limits from any provider with a single except clause.
    """


class OutlookNetworkError(OutlookServiceError):
    """Transport-level failure (timeout, DNS, connection reset)."""


class OutlookAPIError(OutlookServiceError):
    """Unexpected non-2xx response from the Graph API."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"Outlook Graph API error {status_code}: {detail}")


class OutlookFolderNotFoundError(OutlookServiceError):
    """
    The configured NVite folder does not exist in this recruiter's mailbox.

    Resolution: the recruiter must create a folder with the name that matches
    ``settings.OUTLOOK_NVITE_FOLDER`` and set up an Outlook rule to route
    NVite candidate emails into it.
    """

    def __init__(self, folder_name: str) -> None:
        self.folder_name = folder_name
        super().__init__(
            f"Folder {folder_name!r} not found in Outlook mailbox. "
            f"The recruiter must create a folder with this exact name and "
            f"configure an Outlook rule to move candidate emails into it. "
            f"(Configured via OUTLOOK_NVITE_FOLDER in .env)"
        )


class OutlookDeltaResetError(OutlookServiceError):
    """
    Delta sync state is invalid — full resynchronization is required.

    Raised on HTTP 410 Gone or ``syncStateNotFound`` responses from Graph.
    The optional ``location`` header URL (410) should be used for resync when
    present; otherwise callers restart from the initial delta URL.
    """

    def __init__(self, *, location: str | None, detail: str) -> None:
        self.location = location
        super().__init__(detail)


@dataclass(frozen=True)
class DeltaSyncResult:
    """Result of one completed Microsoft Graph delta round."""

    message_ids: list[str]
    delta_link:  str
    folder_id:   str


# ══════════════════════════════════════════════════════════════════════════════
# OutlookService
# ══════════════════════════════════════════════════════════════════════════════

class OutlookService:
    """
    Async Microsoft Graph API client with transparent token refresh.

    Lifecycle
    ---------
    Use as an async context manager so the underlying ``httpx.AsyncClient``
    is cleanly closed::

        async with OutlookService(access_token=..., refresh_token=...) as svc:
            ids = await svc.list_new_messages()

    Interface contract
    ------------------
    The public method signatures and return shapes are identical to
    ``GmailService``.  The ingestion service and email_sync bridge can call
    either service without knowing which provider they are talking to.

    Thread-safety
    -------------
    A single instance should not be shared across concurrent coroutines
    because token refresh mutates ``_access_token`` in-place.  Create one
    instance per ingestion task.
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
            access_token:  Current Microsoft OAuth access token.
            refresh_token: Long-lived refresh token (``offline_access`` scope).
            client:        Optional pre-built ``httpx.AsyncClient``.  When
                           omitted the service creates and owns its own client.
        """
        if not access_token:
            raise ValueError("access_token must not be empty.")
        if not refresh_token:
            raise ValueError("refresh_token must not be empty.")

        self._access_token:  str = access_token
        self._refresh_token: str = refresh_token
        self._is_refreshing: bool = False

        # Per-instance folder name → Graph folder ID cache.
        # Avoids redundant /mailFolders calls on each ingestion cycle.
        self._folder_id_cache: dict[str, str] = {}

        self._owns_client = client is None
        self._client: httpx.AsyncClient = client or httpx.AsyncClient(
            timeout=_DEFAULT_TIMEOUT,
            headers={"Accept": "application/json"},
        )

    # ── Async context-manager protocol ───────────────────────────────────────

    async def __aenter__(self) -> "OutlookService":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()

    # =========================================================================
    # Public API  (mirrors GmailService interface exactly)
    # =========================================================================

    def get_current_access_token(self) -> str:
        """
        Return the current (possibly refreshed) access token.

        The ingestion layer calls this after message fetching to detect
        whether a silent refresh occurred and persist the new token to DB.

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
        Return message IDs from the recruiter's NVite Outlook folder.

        Folder name is read from ``settings.OUTLOOK_NVITE_FOLDER`` (default
        ``"Nvite"``).  The folder display name is resolved to its opaque
        Graph folder ID via :meth:`_resolve_folder_id` and cached for the
        lifetime of this service instance.

        Args:
            max_results: Maximum number of message IDs to return (default 20).

        Returns:
            List of Graph message ID strings.  Empty list if the folder
            contains no messages.

        Raises:
            OutlookFolderNotFoundError: The configured NVite folder does not
                                        exist in this recruiter's mailbox.
            OutlookAuthError:           Credentials invalid after refresh.
            OutlookPermissionError:     Insufficient OAuth scopes.
            OutlookRateLimitError:      API throttle limit exceeded.
            OutlookNetworkError:        Transport-level failure.
            OutlookAPIError:            Other non-2xx response.
        """
        folder_name = settings.OUTLOOK_NVITE_FOLDER
        folder_id   = await self._resolve_folder_id(folder_name)

        url = f"{_GRAPH_BASE}/me/mailFolders/{folder_id}/messages"
        response = await self._request_with_refresh(
            "GET",
            url,
            params={
                "$select":  "id",
                "$top":     str(max_results),
                "$orderby": "receivedDateTime desc",
            },
        )
        messages: list[dict[str, Any]] = response.json().get("value") or []
        ids = [msg["id"] for msg in messages if msg.get("id")]

        logger.debug(
            "event=outlook.list_new_messages folder=%r count=%d",
            folder_name, len(ids),
        )
        return ids

    async def sync_nvite_folder_delta(
        self,
        *,
        delta_link: str | None,
        folder_id:  str | None,
    ) -> DeltaSyncResult:
        """
        Run one complete Microsoft Graph delta round on the Nvite mail folder.

        Bootstrap (``delta_link`` is ``None``):
            ``GET .../mailFolders/{id}/messages/delta?changeType=created&$select=id``

        Incremental (``delta_link`` set):
            ``GET {delta_link}`` verbatim — no query parameters added.

        Paginates every ``@odata.nextLink`` until ``@odata.deltaLink`` is
        returned.  On ``410 Gone`` / ``syncStateNotFound``, performs one
        full-resync attempt per Microsoft's synchronization-reset guidance.

        Args:
            delta_link: Stored ``@odata.deltaLink`` from the previous round,
                        or ``None`` for initial bootstrap.
            folder_id:  Cached Nvite folder Graph ID, or ``None`` to resolve.

        Returns:
            :class:`DeltaSyncResult` with all discovered message IDs, the new
            ``@odata.deltaLink``, and the resolved folder ID.

        Raises:
            OutlookFolderNotFoundError: Nvite folder missing from mailbox.
            OutlookDeltaResetError:     Resync failed after invalid delta state.
            OutlookAuthError:           Credentials invalid after refresh.
            OutlookRateLimitError:      429 retry budget exhausted on a page.
            OutlookNetworkError:        Transport failure.
            OutlookAPIError:            Unexpected Graph response.
        """
        resolved_folder_id = await self._ensure_nvite_folder_id(folder_id)
        start_url = (
            delta_link
            if delta_link
            else self._initial_delta_url(resolved_folder_id)
        )

        logger.info(
            "event=outlook.delta_round_start folder_id=%r bootstrap=%s",
            resolved_folder_id,
            delta_link is None,
        )

        try:
            message_ids, new_delta_link = await self._paginate_delta(start_url)
        except OutlookDeltaResetError as exc:
            logger.warning(
                "event=outlook.delta_reset_resync "
                "folder_id=%r location_present=%s detail=%s",
                resolved_folder_id,
                bool(exc.location),
                exc,
            )
            resync_url = exc.location or self._initial_delta_url(resolved_folder_id)
            message_ids, new_delta_link = await self._paginate_delta(resync_url)

        logger.info(
            "event=outlook.delta_round_complete folder_id=%r ids=%d",
            resolved_folder_id,
            len(message_ids),
        )
        return DeltaSyncResult(
            message_ids=message_ids,
            delta_link=new_delta_link,
            folder_id=resolved_folder_id,
        )

    async def get_full_message(self, message_id: str) -> dict[str, Any]:
        """
        Fetch a Graph message and return an ingestion-ready normalized dict.

        The return shape is **identical** to
        ``GmailService.get_full_message()`` so the downstream pipeline
        (parsing_service → candidate_store → resume_tasks) requires no
        changes.

        Args:
            message_id: Graph API message ID.

        Returns:
            Normalized message dict with guaranteed keys::

                {
                    "message_id":  "AAMkAGI2...",
                    "thread_id":   "AAQkAGI2...",
                    "subject":     "Application for Backend Engineer",
                    "from":        "candidate@example.com",
                    "to":          "recruiter@company.com",
                    "date":        "Thu, 01 May 2026 10:00:00 +0000",
                    "timestamp":   "Thu, 01 May 2026 10:00:00 +0000",
                    "body":        "Hi, I am interested...",
                    "attachments": [
                        {
                            "filename":      "resume.pdf",
                            "mime_type":     "application/pdf",
                            "attachment_id": "AQMkAD..."
                        }
                    ]
                }

        Raises:
            OutlookAuthError:       Credentials invalid after refresh.
            OutlookPermissionError: Insufficient OAuth scopes.
            OutlookRateLimitError:  API throttle limit exceeded.
            OutlookNetworkError:    Transport-level failure.
            OutlookAPIError:        Other non-2xx response.
        """
        logger.debug("event=outlook.get_full_message message_id=%s", message_id)

        # Fetch message body + metadata in one call.
        url = f"{_GRAPH_BASE}/me/messages/{message_id}"
        response = await self._request_with_refresh(
            "GET", url,
            params={"$select": _MESSAGE_FIELDS, "$expand": "attachments"},
        )
        raw: dict[str, Any] = response.json()

        result = self._normalize_message(raw)

        logger.debug(
            "event=outlook.get_full_message_done message_id=%s "
            "subject=%r body_len=%d attachments=%d",
            message_id,
            result["subject"],
            len(result["body"]),
            len(result["attachments"]),
        )
        return result

    async def download_attachment(
        self,
        message_id:    str,
        attachment_id: str,
    ) -> bytes:
        """
        Download raw attachment bytes from Graph API.

        Graph API behavior
        ------------------
        - For attachments **≤3MB**: ``contentBytes`` is present in the
          attachment JSON body as a base64-encoded string.
        - For attachments **>3MB**: ``@microsoft.graph.downloadUrl`` is
          present instead.  This method follows the redirect URL and
          returns raw bytes without decoding.

        Args:
            message_id:    Graph message ID containing the attachment.
            attachment_id: The ``id`` field from the attachment metadata.

        Returns:
            Raw attachment bytes.

        Raises:
            OutlookAuthError:       Credentials invalid after refresh.
            OutlookPermissionError: Insufficient OAuth scopes.
            OutlookRateLimitError:  API throttle limit exceeded.
            OutlookNetworkError:    Transport-level failure.
            OutlookAPIError:        Non-2xx response or empty content.
        """
        logger.debug(
            "event=outlook.download_attachment message_id=%s attachment_id=%s",
            message_id, attachment_id,
        )

        url = f"{_GRAPH_BASE}/me/messages/{message_id}/attachments/{attachment_id}"
        response = await self._request_with_refresh("GET", url)
        data: dict[str, Any] = response.json()

        # ── Large attachment: Graph provides a pre-signed download URL ─────────
        download_url: str | None = data.get("@microsoft.graph.downloadUrl")
        if download_url:
            logger.debug(
                "event=outlook.large_attachment_redirect attachment_id=%s",
                attachment_id,
            )
            try:
                dl_response = await self._client.get(
                    download_url,
                    headers={"Accept": "*/*"},
                )
            except httpx.RequestError as exc:
                raise OutlookNetworkError(
                    f"Network failure downloading large attachment {attachment_id!r}: {exc}"
                ) from exc

            if not dl_response.is_success:
                raise OutlookAPIError(
                    dl_response.status_code,
                    f"Large attachment download URL returned {dl_response.status_code}",
                )
            raw_bytes = dl_response.content

        else:
            # ── Normal attachment: base64-encoded contentBytes ─────────────────
            content_bytes: str = data.get("contentBytes", "")
            if not content_bytes:
                raise OutlookAPIError(
                    200,
                    f"Empty contentBytes for attachment_id={attachment_id!r}. "
                    f"Check attachment exists and Mail.Read scope is granted.",
                )
            try:
                raw_bytes = base64.b64decode(content_bytes)
            except Exception as exc:
                raise OutlookAPIError(
                    200,
                    f"base64 decode failed for attachment_id={attachment_id!r}: {exc}",
                ) from exc

        if not raw_bytes:
            raise OutlookAPIError(
                200,
                f"Attachment {attachment_id!r} returned 0 bytes.",
            )

        logger.debug(
            "event=outlook.download_attachment_done attachment_id=%s size_bytes=%d",
            attachment_id, len(raw_bytes),
        )
        return raw_bytes

    async def send_mail(
        self,
        *,
        subject: str,
        html_body: str,
        to_recipients: list[str],
        cc_recipients: list[str] | None = None,
        save_to_sent_items: bool = True,
    ) -> None:
        """
        Send an email from the authenticated mailbox via Microsoft Graph
        ``POST /me/sendMail``.

        Requires the ``Mail.Send`` delegated permission on the access token.

        Microsoft Graph responds with HTTP **202 Accepted** and an empty body
        on success.  No sent-message identifier is returned by this API; callers
        should treat a null provider message id as expected behaviour.

        This method performs HTTP delivery only.  Callers own persistence of
        delivery status, report lifecycle, and refreshed OAuth tokens.

        Args:
            subject:            Email subject line.
            html_body:          HTML body content.
            to_recipients:      Required To addresses.
            cc_recipients:      Optional CC addresses.
            save_to_sent_items: Whether Graph should keep a Sent Items copy.

        Raises:
            ValueError:                 Empty subject/body or empty To list.
            OutlookAuthError:           Credentials invalid after refresh.
            OutlookPermissionError:     Insufficient OAuth scopes (e.g. no Mail.Send).
            OutlookRateLimitError:      API throttle limit exceeded.
            OutlookNetworkError:        Transport-level failure.
            OutlookAPIError:            Any other non-2xx Graph response.
        """
        if not subject or not subject.strip():
            raise ValueError("subject must not be empty.")
        if html_body is None:
            raise ValueError("html_body must not be None.")
        cleaned_to = [addr.strip() for addr in to_recipients if addr and str(addr).strip()]
        if not cleaned_to:
            raise ValueError("to_recipients must contain at least one address.")
        cleaned_cc = [
            addr.strip()
            for addr in (cc_recipients or [])
            if addr and str(addr).strip()
        ]

        def _recipient(address: str) -> dict[str, Any]:
            return {"emailAddress": {"address": address}}

        payload: dict[str, Any] = {
            "message": {
                "subject": subject.strip(),
                "body": {
                    "contentType": "HTML",
                    "content": html_body,
                },
                "toRecipients": [_recipient(a) for a in cleaned_to],
            },
            "saveToSentItems": save_to_sent_items,
        }
        if cleaned_cc:
            payload["message"]["ccRecipients"] = [_recipient(a) for a in cleaned_cc]

        url = f"{_GRAPH_BASE}/me/sendMail"
        logger.info(
            "event=outlook.send_mail_start to_count=%d cc_count=%d subject=%r",
            len(cleaned_to),
            len(cleaned_cc),
            subject.strip()[:80],
        )
        await self._request_with_refresh("POST", url, json=payload)
        logger.info("event=outlook.send_mail_ok to_count=%d", len(cleaned_to))

    # =========================================================================
    # Internal helpers
    # =========================================================================

    # ── Authorization header ──────────────────────────────────────────────────

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    # ── Folder ID resolution ──────────────────────────────────────────────────

    async def _resolve_folder_id(self, folder_name: str) -> str:
        """
        Resolve an Outlook folder display name to its opaque Graph folder ID.

        Mirrors ``GmailService.get_label_id()`` in behavior — resolves once
        and caches the result for the lifetime of the service instance.

        Args:
            folder_name: Exact display name of the folder (case-sensitive).

        Returns:
            The opaque Graph folder ID string.

        Raises:
            OutlookFolderNotFoundError: Folder not found in the mailbox.
            OutlookAuthError / OutlookRateLimitError / ...: Provider errors.
        """
        if folder_name in self._folder_id_cache:
            logger.debug(
                "event=outlook.folder_cache_hit folder_name=%r", folder_name
            )
            return self._folder_id_cache[folder_name]

        logger.info(
            "event=outlook.folder_lookup_start folder_name=%r", folder_name
        )

        url = f"{_GRAPH_BASE}/me/mailFolders"
        response = await self._request_with_refresh(
            "GET",
            url,
            params={
                "$filter": f"displayName eq '{folder_name}'",
                "$select": "id,displayName",
            },
        )
        folders: list[dict[str, Any]] = response.json().get("value") or []

        if not folders:
            logger.error(
                "event=outlook.folder_not_found folder_name=%r",
                folder_name,
            )
            raise OutlookFolderNotFoundError(folder_name)

        folder_id: str = folders[0]["id"]
        self._folder_id_cache[folder_name] = folder_id

        logger.info(
            "event=outlook.folder_resolved folder_name=%r folder_id=%r",
            folder_name, folder_id,
        )
        return folder_id

    # ── Delta query (Nvite folder discovery) ─────────────────────────────────

    @staticmethod
    def _initial_delta_url(folder_id: str) -> str:
        """Build the first-request delta URL for bootstrap."""
        return (
            f"{_GRAPH_BASE}/me/mailFolders/{folder_id}/messages/delta"
            f"?changeType=created&$select=id"
        )

    async def _ensure_nvite_folder_id(self, cached_folder_id: str | None) -> str:
        """
        Return the Nvite folder Graph ID, using the cached value when provided.

        Clears the in-memory cache and re-resolves when ``cached_folder_id`` is
        ``None`` (e.g. after the folder was not found in a prior cycle).
        """
        folder_name = settings.OUTLOOK_NVITE_FOLDER
        if cached_folder_id:
            self._folder_id_cache[folder_name] = cached_folder_id
            return cached_folder_id
        self._folder_id_cache.pop(folder_name, None)
        return await self._resolve_folder_id(folder_name)

    async def _paginate_delta(self, start_url: str) -> tuple[list[str], str]:
        """
        Follow ``@odata.nextLink`` pages until ``@odata.deltaLink`` is received.

        Returns:
            Tuple of (message_ids, new_delta_link).

        Raises:
            OutlookAPIError: Round ended without ``@odata.deltaLink``.
        """
        all_ids: list[str] = []
        url: str | None = start_url
        page_num = 0

        while url:
            page_num += 1
            data = await self._fetch_delta_page(url)
            page_ids = self._extract_delta_message_ids(data)
            all_ids.extend(page_ids)

            logger.debug(
                "event=outlook.delta_page page=%d ids=%d has_next=%s has_delta=%s",
                page_num,
                len(page_ids),
                bool(data.get("@odata.nextLink")),
                bool(data.get("@odata.deltaLink")),
            )

            if delta_link := data.get("@odata.deltaLink"):
                return all_ids, delta_link

            url = data.get("@odata.nextLink")

        raise OutlookAPIError(
            200,
            "Delta round ended without @odata.deltaLink in the final response.",
        )

    @staticmethod
    def _extract_delta_message_ids(data: dict[str, Any]) -> list[str]:
        """Collect message IDs from a delta page, skipping @removed entries."""
        ids: list[str] = []
        for item in data.get("value") or []:
            if item.get("@removed"):
                continue
            message_id = item.get("id")
            if message_id:
                ids.append(message_id)
        return ids

    async def _fetch_delta_page(self, url: str) -> dict[str, Any]:
        """
        Fetch one delta page with bounded 429 retry and auth refresh.

        Uses ``Prefer: odata.maxpagesize`` from settings.  Does not advance
        pagination on 429 — retries the same URL until success or the retry
        budget is exhausted.

        Raises:
            OutlookDeltaResetError: HTTP 410 or syncStateNotFound in body.
            OutlookRateLimitError: 429 retry budget exhausted.
            OutlookAuthError:       401 persists after token refresh.
            OutlookPermissionError: HTTP 403.
            OutlookNetworkError:    Transport failure.
            OutlookAPIError:        Other non-success responses.
        """
        prefer_value = f"odata.maxpagesize={settings.OUTLOOK_DELTA_PAGE_SIZE}"
        deadline = time.monotonic() + settings.OUTLOOK_DELTA_MAX_RETRY_SECONDS
        auth_refreshed = False

        while True:
            try:
                response = await self._client.request(
                    "GET",
                    url,
                    headers={
                        **self._auth_headers,
                        "Prefer": prefer_value,
                    },
                )
            except httpx.TimeoutException as exc:
                raise OutlookNetworkError(
                    f"Delta page request timed out: GET {url}"
                ) from exc
            except httpx.RequestError as exc:
                raise OutlookNetworkError(
                    f"Delta page network error: GET {url} — {exc}"
                ) from exc

            if response.status_code == 401:
                if not auth_refreshed:
                    logger.info(
                        "event=outlook.delta_auth_refresh url=%s", url
                    )
                    self._access_token = await self._refresh_access_token()
                    auth_refreshed = True
                    continue
                raise OutlookAuthError(
                    "Access denied after token refresh during delta pagination."
                )

            if response.status_code == 410:
                raise OutlookDeltaResetError(
                    location=response.headers.get("Location"),
                    detail=(
                        "Delta sync state invalid (410 Gone). "
                        "Full resynchronization required."
                    ),
                )

            if response.status_code == 429:
                if time.monotonic() >= deadline:
                    raise OutlookRateLimitError(
                        "Graph delta page throttled beyond retry budget "
                        f"({settings.OUTLOOK_DELTA_MAX_RETRY_SECONDS}s)."
                    )
                retry_after = int(response.headers.get("Retry-After", "5"))
                logger.warning(
                    "event=outlook.delta_throttled retry_after=%ds url=%s",
                    retry_after,
                    url,
                )
                await asyncio.sleep(retry_after)
                continue

            if response.status_code == 403:
                raise OutlookPermissionError(
                    "Permission denied during delta pagination (403)."
                )

            if not response.is_success:
                body_text = response.text
                if self._is_sync_state_not_found(response.status_code, body_text):
                    raise OutlookDeltaResetError(
                        location=None,
                        detail=(
                            "Delta sync state not found. "
                            "Full resynchronization required."
                        ),
                    )
                if response.status_code == 404 and "ErrorItemNotFound" in body_text:
                    raise OutlookFolderNotFoundError(settings.OUTLOOK_NVITE_FOLDER)
                raise OutlookAPIError(response.status_code, body_text[:300])

            return response.json()

    @staticmethod
    def _is_sync_state_not_found(status_code: int, body_text: str) -> bool:
        """Return True when Graph indicates the delta token is no longer valid."""
        if status_code not in (400, 404, 410):
            return False
        lowered = body_text.lower()
        return (
            "syncstatenotfound" in lowered
            or "sync state" in lowered and "not found" in lowered
        )

    # ── Core request dispatcher with single-retry on 401 ─────────────────────

    async def _request_with_refresh(
        self,
        method: str,
        url:    str,
        *,
        params: dict[str, Any] | None = None,
        json:   dict[str, Any] | None = None,
        _retry: bool = True,
    ) -> httpx.Response:
        """
        Execute an HTTP request, refreshing the access token once on 401.

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, etc.).
            url:    Full request URL.
            params: Query-string parameters.
            json:   JSON request body.
            _retry: Internal flag — ``False`` on the recursive retry to
                    prevent infinite loops.

        Returns:
            The successful ``httpx.Response``.

        Raises:
            OutlookAuthError:       401 persists after token refresh.
            OutlookPermissionError: 403 response.
            OutlookRateLimitError:  429 after retry budget exhausted.
            OutlookNetworkError:    Transport failure.
            OutlookAPIError:        Any other non-2xx response.
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
            logger.warning(
                "event=outlook.timeout method=%s url=%s — %s", method, url, exc
            )
            raise OutlookNetworkError(
                f"Request timed out: {method} {url}"
            ) from exc
        except httpx.RequestError as exc:
            logger.warning(
                "event=outlook.network_error method=%s url=%s — %s", method, url, exc
            )
            if _retry:
                logger.info(
                    "event=outlook.network_retry method=%s url=%s — backing off 1.5s",
                    method, url,
                )
                await asyncio.sleep(1.5)
                return await self._request_with_refresh(
                    method, url, params=params, json=json, _retry=False
                )
            raise OutlookNetworkError(
                f"Network failure after retry: {method} {url}"
            ) from exc

        # ── Handle HTTP status codes ───────────────────────────────────────────
        if response.status_code == 401:
            if _retry:
                logger.info(
                    "event=outlook.token_refresh_start "
                    "reason=401 url=%s", url,
                )
                new_token = await self._refresh_access_token()
                self._access_token = new_token
                return await self._request_with_refresh(
                    method, url, params=params, json=json, _retry=False
                )
            logger.error(
                "event=outlook.auth_failed "
                "reason=401_after_refresh url=%s", url,
            )
            raise OutlookAuthError(
                "Access denied after token refresh. "
                "The recruiter must re-authenticate via the Microsoft OAuth flow."
            )

        if response.status_code == 403:
            logger.error(
                "event=outlook.permission_denied method=%s url=%s — "
                "check that Mail.Read / Mail.Send delegated permissions are granted.",
                method, url,
            )
            raise OutlookPermissionError(
                "Permission denied (403). Verify that Mail.Read / Mail.Send "
                "delegated permissions are granted and the recruiter consented."
            )

        if response.status_code == 429:
            retry_after: int = int(response.headers.get("Retry-After", "1"))
            logger.warning(
                "event=outlook.rate_limited method=%s url=%s "
                "Retry-After=%ds _retry=%s",
                method, url, retry_after, _retry,
            )
            if _retry:
                logger.info(
                    "event=outlook.rate_limit_backoff sleeping=%ds", retry_after
                )
                await asyncio.sleep(retry_after)
                return await self._request_with_refresh(
                    method, url, params=params, json=json, _retry=False
                )
            raise OutlookRateLimitError(
                f"Graph API throttle exceeded after retry "
                f"(Retry-After={retry_after}s). Back off and retry later."
            )

        if not response.is_success:
            logger.error(
                "event=outlook.api_error status=%d method=%s url=%s body=%s",
                response.status_code, method, url, response.text[:300],
            )
            raise OutlookAPIError(response.status_code, response.text[:300])

        return response

    # ── Token refresh ─────────────────────────────────────────────────────────

    async def _refresh_access_token(self) -> str:
        """
        Exchange the stored refresh token for a new Microsoft access token.

        Unpacks the tuple returned by :meth:`_do_refresh`.  If a new refresh
        token was included in the response (Microsoft rotates on every call),
        ``self._refresh_token`` is updated in-memory immediately and a rotation
        event is logged.  Callers receive only the new access token string —
        the return type is unchanged.

        Concurrency guard: raises immediately if another refresh is already
        in progress on this instance (concurrent use is unsupported).

        Returns:
            The new access token string.

        Raises:
            OutlookAuthError:    Concurrent refresh detected, or refresh
                                 endpoint returns non-2xx (revoked token).
            OutlookNetworkError: Cannot reach the Microsoft token endpoint.
        """
        if self._is_refreshing:
            logger.error(
                "event=outlook.concurrent_refresh_detected — "
                "this instance is not safe for concurrent use."
            )
            raise OutlookAuthError(
                "Concurrent token refresh detected. "
                "Use a separate OutlookService instance per concurrent task."
            )

        self._is_refreshing = True
        try:
            new_access_token, new_refresh_token = await self._do_refresh()
        finally:
            self._is_refreshing = False

        if new_refresh_token is not None:
            self._refresh_token = new_refresh_token
            logger.info("event=outlook.refresh_token_rotated")

        return new_access_token

    async def _do_refresh(self) -> tuple[str, str | None]:
        """
        Internal: perform the actual Microsoft token-refresh HTTP call.

        Called exclusively from :meth:`_refresh_access_token` after the
        concurrency guard is set.  Never call this directly.

        Returns:
            A tuple of ``(new_access_token, new_refresh_token_or_None)``.
            ``new_refresh_token_or_None`` is the rotated refresh token issued
            by Microsoft (present on every successful refresh call), or ``None``
            if the field was unexpectedly absent from the response.

        Raises:
            OutlookAuthError:    Refresh endpoint rejected the request (token
                                 revoked, client credentials wrong, etc.).
            OutlookNetworkError: Cannot reach the Microsoft token endpoint.
        """
        if (
            not settings.MICROSOFT_TENANT_ID
            or not settings.MICROSOFT_CLIENT_ID
            or not settings.MICROSOFT_CLIENT_SECRET
        ):
            missing = []
            if not settings.MICROSOFT_TENANT_ID:
                missing.append("MICROSOFT_TENANT_ID")
            if not settings.MICROSOFT_CLIENT_ID:
                missing.append("MICROSOFT_CLIENT_ID")
            if not settings.MICROSOFT_CLIENT_SECRET:
                missing.append("MICROSOFT_CLIENT_SECRET")
            raise OutlookAuthError(
                f"Microsoft OAuth is not fully configured. "
                f"Missing: {', '.join(missing)}. Set these in .env."
            )

        # Build tenant-specific token URL.
        # /{tenant_id}/ locks refresh calls to Infomatics Corp accounts only.
        token_url = (
            f"{_MS_TOKEN_BASE}/{settings.MICROSOFT_TENANT_ID}/oauth2/v2.0/token"
        )

        payload: dict[str, str] = {
            "grant_type":    "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id":     settings.MICROSOFT_CLIENT_ID,
            "client_secret": settings.MICROSOFT_CLIENT_SECRET,
            "scope":         "openid email profile offline_access Mail.Read Mail.Send",
        }

        logger.info("event=outlook.token_refresh_attempt")

        try:
            response = await self._client.post(
                token_url,
                data=payload,
                headers={"Accept": "application/json"},
            )
        except httpx.RequestError as exc:
            logger.error(
                "event=outlook.token_refresh_network_error detail=%s", exc
            )
            raise OutlookNetworkError(
                "Could not reach Microsoft token endpoint for refresh."
            ) from exc

        if not response.is_success:
            error_detail = ""
            try:
                body = response.json()
                error_detail = (
                    body.get("error_description")
                    or body.get("error")
                    or ""
                )
            except Exception:
                error_detail = response.text[:200]

            logger.error(
                "event=outlook.token_refresh_failed status=%d detail=%s",
                response.status_code, error_detail,
            )
            raise OutlookAuthError(
                f"Microsoft token refresh failed ({response.status_code}): "
                f"{error_detail}. The recruiter must re-authenticate."
            )

        data = response.json()
        new_token: str | None = data.get("access_token")
        if not new_token:
            logger.error(
                "event=outlook.token_refresh_missing_field "
                "reason=access_token_absent_in_response"
            )
            raise OutlookAuthError(
                "Token refresh succeeded but response contained no access_token."
            )

        new_refresh_token: str | None = data.get("refresh_token") or None

        logger.info("event=outlook.token_refresh_success")
        return new_token, new_refresh_token

    # ── Message normalization ─────────────────────────────────────────────────

    def _normalize_message(self, raw: dict[str, Any]) -> dict[str, Any]:
        """
        Convert a raw Graph API message object to the provider-agnostic dict.

        The output shape is **identical** to ``GmailService.get_full_message()``
        so no downstream code changes are needed when switching providers.

        Graph API input fields:
            id, conversationId, subject, from.emailAddress.address,
            toRecipients[0].emailAddress.address, receivedDateTime,
            body.content (text or html), attachments[].{id, name, contentType}

        Args:
            raw: Full Graph API ``message`` resource dict.

        Returns:
            Normalized message dict.
        """
        # ── Sender / recipient ────────────────────────────────────────────────
        from_addr: str = (
            (raw.get("from") or {})
            .get("emailAddress", {})
            .get("address", "")
        )
        to_recipients: list[dict] = raw.get("toRecipients") or []
        to_addr: str = (
            (to_recipients[0].get("emailAddress", {}) if to_recipients else {})
            .get("address", "")
        )

        # ── Date normalization ────────────────────────────────────────────────
        # Graph returns ISO 8601 UTC: "2026-05-01T10:00:00Z"
        # We convert to RFC 2822 format to match GmailService output.
        received_dt_str: str = raw.get("receivedDateTime", "")
        date_str: str = self._iso_to_rfc2822(received_dt_str)

        # ── Body ──────────────────────────────────────────────────────────────
        body_obj: dict[str, Any] = raw.get("body") or {}
        body_content: str        = body_obj.get("content", "")
        body_type: str           = body_obj.get("contentType", "text").lower()

        if body_type == "html":
            body_text = self._strip_html_tags(body_content)
        else:
            body_text = body_content

        # ── Attachments ───────────────────────────────────────────────────────
        # Graph includes attachments inline when $expand=attachments is used.
        # Only include file attachments (skip inline images / reference items).
        raw_attachments: list[dict[str, Any]] = raw.get("attachments") or []
        attachments: list[dict[str, str]] = []

        for att in raw_attachments:
            # Skip inline (embedded) images and reference attachments
            odata_type: str = att.get("@odata.type", "")
            if "#microsoft.graph.fileAttachment" not in odata_type and odata_type:
                continue

            att_id:   str = att.get("id", "")
            att_name: str = att.get("name", "")
            att_mime: str = att.get("contentType", "application/octet-stream")

            # Skip attachments without an ID or a meaningful filename
            if not att_id or not att_name:
                continue

            attachments.append({
                "filename":      att_name,
                "mime_type":     att_mime,
                "attachment_id": att_id,
            })

        return {
            "message_id":  raw.get("id", ""),
            "thread_id":   raw.get("conversationId", ""),
            "subject":     raw.get("subject", "") or "",
            "from":        from_addr,
            "to":          to_addr,
            "date":        date_str,
            "timestamp":   date_str,   # alias for downstream parsers
            "body":        body_text.strip(),
            "attachments": attachments,
        }

    # ── Static utilities ──────────────────────────────────────────────────────

    @staticmethod
    def _iso_to_rfc2822(iso_str: str) -> str:
        """
        Convert an ISO 8601 UTC datetime string to RFC 2822 format.

        Graph API returns ``"2026-05-01T10:00:00Z"``; Gmail returns RFC 2822.
        This normalizes Graph output so downstream consumers always see the
        same format regardless of provider.

        Args:
            iso_str: ISO 8601 string (e.g. ``"2026-05-01T10:00:00Z"``).

        Returns:
            RFC 2822 string (e.g. ``"Thu, 01 May 2026 10:00:00 +0000"``).
            Returns the original string on any parse failure.
        """
        if not iso_str:
            return ""
        try:
            # Handle trailing Z and microseconds
            clean = iso_str.rstrip("Z").split(".")[0]
            dt = datetime.strptime(clean, "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            return formatdate(dt.timestamp(), usegmt=True)
        except (ValueError, AttributeError):
            logger.debug(
                "event=outlook.date_parse_fallback iso_str=%r", iso_str
            )
            return iso_str

    @staticmethod
    def _strip_html_tags(html: str) -> str:
        """
        Remove HTML tags and normalize whitespace.

        Used when Graph returns ``body.contentType == "html"`` and no
        plain-text version is available.  Mirrors ``GmailService._strip_html_tags()``.

        Args:
            html: Raw HTML string.

        Returns:
            Plain-text string.  Empty string if ``html`` is empty.
        """
        import re

        if not html:
            return ""

        # Preserve paragraph breaks at closing block-level tags.
        block_end = re.compile(
            r"</(p|div|li|tr|td|th|h[1-6]|blockquote|pre)\s*>",
            re.IGNORECASE,
        )
        text = block_end.sub("\n", html)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)

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

        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
