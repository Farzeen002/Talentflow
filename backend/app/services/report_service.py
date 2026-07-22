"""
app/services/report_service.py

Service layer for the Daily Reports module.

Responsibilities:
  - MongoDB index bootstrap for ``daily_reports``
  - Idempotent open/reopen by business key
  - Draft CRUD (entries, lead metrics/items, working recipients)
  - Kind-specific submit validation, freeze, delivery status updates
  - Resend from FAILED using the frozen recipient snapshot

Design constraints:
  - NO FastAPI request/response objects in business logic beyond HTTPException
  - All MongoDB I/O is async (Motor)
  - Recruiter isolation on every query via ``recruiter_id`` from JWT
  - ``job_id`` on recruiter entries is free text — never validated against Jobs
  - Outlook send via :func:`deliver_report_email` (send-only; this module
    owns freeze, status, delivery metadata, and token persistence)
  - Email subject/HTML via :func:`render_report_email` (isolated renderer)

Timestamps:
  - ``submitted_at`` — business submission / freeze time
  - ``delivery.sent_at`` — successful email delivery time only
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from app.config import get_settings
from app.models.recruiter import OAuthStatus, ProviderType, RecruiterDocument
from app.models.report import (
    DailyReportDocument,
    DailyReportListResponse,
    DailyReportResponse,
    DailyReportSummaryResponse,
    LeadMetricsUpdateRequest,
    LeadTextItem,
    LeadTextItemCreateRequest,
    LeadTextItemUpdateRequest,
    RecipientsUpdateRequest,
    RecruiterEntry,
    RecruiterEntryCreateRequest,
    RecruiterEntryUpdateRequest,
    ReportDefaultsResponse,
    ReportDelivery,
    ReportKind,
    ReportOpenRequest,
    ReportRecipients,
    ReportStatus,
    SubmissionStatus,
    empty_lead_payload,
    empty_recruiter_payload,
)
from app.services.auth_service import (
    decrypt_oauth_tokens,
    encrypt_oauth_tokens,
    get_recruiter_by_id,
)
from app.services.report_email_renderer import render_report_email

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Collection names ──────────────────────────────────────────────────────────
_DAILY_REPORTS_COL = "daily_reports"
_RECRUITERS_COL = "recruiters"

_LeadCollection = Literal["key_activities", "challenges_risks", "plan_for_tomorrow"]

_LEAD_COLLECTIONS: frozenset[str] = frozenset(
    {"key_activities", "challenges_risks", "plan_for_tomorrow"}
)

_RECRUITER_ENTRY_MANDATORY: tuple[str, ...] = (
    "job_id",
    "candidate_name",
    "job_name",
    "candidate_contact_number",
    "candidate_email",
    "poc",
    "client",
    "submission_status",
)

_LEAD_METRIC_PATHS: tuple[tuple[str, str], ...] = (
    ("recruitment_summary", "requirements_managed"),
    ("team_profile_review", "profiles_received"),
    ("team_profile_review", "profiles_approved"),
    ("team_profile_review", "profiles_rejected"),
    ("lead_recruitment_delivery", "profiles_submitted"),
    ("lead_recruitment_delivery", "interviews"),
    ("lead_recruitment_delivery", "offers"),
    ("lead_recruitment_delivery", "joinings"),
)


# ══════════════════════════════════════════════════════════════════════════════
# Index bootstrap
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_daily_report_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Create required indexes on the ``daily_reports`` collection.

    Idempotent — safe to call on every application startup (same pattern as
    ``ensure_job_indexes`` / ``ensure_indexes``: MongoDB ``create_index`` is a
    no-op when an identical index already exists).

    On success, verifies the expected index names exist and logs them.
    On failure, logs ``event=reports.indexes_failed`` and re-raises so startup
    does not continue without indexes.

    Indexes
    -------
    uq_daily_report_business_key
        Unique on ``(recruiter_id, report_date, report_kind)``.
    uq_daily_report_id
        Unique on ``report_id``.
    idx_daily_reports_recruiter_status
        List filters by status for a recruiter.
    idx_daily_reports_recruiter_date
        Date-ordered history / lookback queries.
    """
    expected_names = (
        "uq_daily_report_business_key",
        "uq_daily_report_id",
        "idx_daily_reports_recruiter_status",
        "idx_daily_reports_recruiter_date",
    )
    col = db[_DAILY_REPORTS_COL]

    try:
        await col.create_index(
            [
                ("recruiter_id", ASCENDING),
                ("report_date", ASCENDING),
                ("report_kind", ASCENDING),
            ],
            unique=True,
            name="uq_daily_report_business_key",
        )
        await col.create_index(
            [("report_id", ASCENDING)],
            unique=True,
            name="uq_daily_report_id",
        )
        await col.create_index(
            [("recruiter_id", ASCENDING), ("status", ASCENDING)],
            name="idx_daily_reports_recruiter_status",
        )
        await col.create_index(
            [("recruiter_id", ASCENDING), ("report_date", ASCENDING)],
            name="idx_daily_reports_recruiter_date",
        )

        index_info = await col.index_information()
        missing = [name for name in expected_names if name not in index_info]
        if missing:
            raise RuntimeError(
                f"Daily reports indexes missing after create_index: {missing}"
            )

        logger.info(
            "event=reports.indexes_ensured collection=%s verified=%s",
            _DAILY_REPORTS_COL,
            list(expected_names),
        )
    except Exception as exc:
        logger.exception(
            "event=reports.indexes_failed collection=%s error=%s",
            _DAILY_REPORTS_COL,
            exc,
        )
        raise



# ══════════════════════════════════════════════════════════════════════════════
# Delivery hook — Outlook send only (ReportService owns status / delivery docs)
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ReportSendResult:
    """
    Outcome of attempting to send a daily report email.

    Does not mutate report documents. ReportService applies status,
    delivery metadata, and token persistence from this result.

    ``provider_message_id``
        Microsoft Graph ``POST /me/sendMail`` responds with HTTP **202 Accepted**
        and an empty body. Graph does not return a sent message id for this API,
        so ``provider_message_id`` remaining ``null`` is expected — not a bug.
    """

    success: bool
    error: str | None = None
    provider: str = "outlook"
    provider_message_id: str | None = None
    new_access_token: str | None = None
    new_refresh_token: str | None = None


async def deliver_report_email(
    *,
    recruiter: RecruiterDocument,
    to_recipients: list[str],
    cc_recipients: list[str],
    subject: str,
    html_body: str,
) -> ReportSendResult:
    """
    Send mail from the recruiter's connected Outlook mailbox.

    Responsibilities (this function only):
      - Decrypt OAuth tokens
      - Call :meth:`OutlookService.send_mail`
      - Return success/failure + any refreshed tokens (caller must persist them)

    Not responsible for:
      - Report status transitions
      - Delivery metadata persistence
      - Audit fields / ``submitted_at``
      - Writing updated OAuth blobs to MongoDB
    """
    from app.services.outlook_service import (  # noqa: PLC0415
        OutlookService,
        OutlookServiceError,
    )

    tokens = decrypt_oauth_tokens(recruiter.oauth_tokens_encrypted)
    access_token = tokens.get("access_token") or ""
    refresh_token = tokens.get("refresh_token") or ""
    if not access_token or not refresh_token:
        return ReportSendResult(
            success=False,
            error="OAuth tokens are incomplete. Please re-authenticate with Outlook.",
            provider="outlook",
        )

    new_access = access_token
    new_refresh = refresh_token
    send_error: str | None = None
    success = False

    try:
        async with OutlookService(
            access_token=access_token,
            refresh_token=refresh_token,
        ) as svc:
            try:
                await svc.send_mail(
                    subject=subject,
                    html_body=html_body,
                    to_recipients=to_recipients,
                    cc_recipients=cc_recipients,
                )
                success = True
            finally:
                # Always capture post-call tokens (refresh may occur even on failure).
                new_access = svc.get_current_access_token()
                new_refresh = svc.get_current_refresh_token()
    except OutlookServiceError as exc:
        send_error = str(exc)
        logger.warning(
            "event=reports.outlook_send_failed recruiter_id=%s error=%s",
            recruiter.recruiter_id,
            exc,
        )
    except Exception as exc:  # noqa: BLE001
        send_error = f"Unexpected email send failure: {exc}"
        logger.exception(
            "event=reports.outlook_send_unexpected recruiter_id=%s error=%s",
            recruiter.recruiter_id,
            exc,
        )

    token_changed = (
        new_access != access_token or new_refresh != refresh_token
    )
    logger.info(
        "event=reports.outlook_send_done recruiter_id=%s success=%s token_rotated=%s",
        recruiter.recruiter_id,
        success,
        token_changed,
    )
    return ReportSendResult(
        success=success,
        error=None if success else (send_error or "Email delivery failed."),
        provider="outlook",
        # Graph sendMail → HTTP 202 Accepted, empty body: no message id is returned.
        provider_message_id=None,
        new_access_token=new_access if token_changed else None,
        new_refresh_token=new_refresh if token_changed else None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Public service API
# ══════════════════════════════════════════════════════════════════════════════

async def open_report(
    db: AsyncIOMotorDatabase,
    payload: ReportOpenRequest,
    recruiter_id: str,
) -> DailyReportResponse:
    """Idempotent open-or-create for ``(recruiter_id, report_date, report_kind)``."""
    report_kind = _coerce_kind(payload.report_kind)
    report_date = payload.report_date
    _assert_report_date_allowed(report_date)

    recruiter = await get_recruiter_by_id(db, recruiter_id)
    existing = await db[_DAILY_REPORTS_COL].find_one(
        {
            "recruiter_id": recruiter_id,
            "report_date": report_date,
            "report_kind": report_kind,
        }
    )
    if existing is not None:
        status_value = existing.get("status")
        if status_value == ReportStatus.sent.value:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A submitted report already exists for this date and kind. "
                    "Corrections require a future amendment flow."
                ),
            )
        logger.info(
            "event=reports.open_reopened report_id=%s status=%s",
            existing.get("report_id"),
            status_value,
        )
        return DailyReportResponse.from_document(existing)

    now = _utc_now()
    defaults = _defaults_for_kind(report_kind)
    doc = DailyReportDocument(
        recruiter_id=recruiter_id,
        recruiter_name=recruiter.name,
        recruiter_email=recruiter.email,
        report_date=report_date,
        report_kind=report_kind,
        status=ReportStatus.draft,
        recipients=ReportRecipients(to=list(defaults.to), cc=list(defaults.cc)),
        recipients_snapshot=None,
        payload=_empty_payload(report_kind),
        delivery=ReportDelivery(),
        created_at=now,
        updated_at=now,
    )
    try:
        await db[_DAILY_REPORTS_COL].insert_one(doc.to_mongo())
    except Exception as exc:
        # Race: another request created the same business key.
        existing = await db[_DAILY_REPORTS_COL].find_one(
            {
                "recruiter_id": recruiter_id,
                "report_date": report_date,
                "report_kind": report_kind,
            }
        )
        if existing is not None:
            return DailyReportResponse.from_document(existing)
        logger.exception(
            "event=reports.open_insert_failed recruiter_id=%s error=%s",
            recruiter_id,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create daily report.",
        ) from exc

    logger.info(
        "event=reports.open_created report_id=%s kind=%s date=%s",
        doc.report_id,
        report_kind,
        report_date,
    )
    return DailyReportResponse.from_document(doc)


async def get_report(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
) -> DailyReportResponse:
    """Fetch one report owned by the authenticated recruiter."""
    doc = await _require_report(db, report_id, recruiter_id)
    return DailyReportResponse.from_document(doc)


async def list_reports(
    db: AsyncIOMotorDatabase,
    recruiter_id: str,
    *,
    report_date: str | None = None,
    report_kind: str | None = None,
    status_filter: str | None = None,
    page: int = 1,
    limit: int = 20,
) -> DailyReportListResponse:
    """Paginated history for the authenticated recruiter.

    Default sort: ``report_date`` descending, then ``created_at`` descending
    (most recent reports first). Explicit sort API is deferred.
    """
    max_limit = int(settings.REPORT_LIST_MAX_LIMIT)
    if limit < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be >= 1.",
        )
    if limit > max_limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"limit cannot exceed {max_limit}.",
        )

    query: dict[str, Any] = {"recruiter_id": recruiter_id}
    if report_date:
        query["report_date"] = report_date
    if report_kind:
        query["report_kind"] = _coerce_kind(report_kind)
    if status_filter:
        query["status"] = _coerce_status(status_filter)

    total = await db[_DAILY_REPORTS_COL].count_documents(query)
    skip = max(page - 1, 0) * limit
    cursor = (
        db[_DAILY_REPORTS_COL]
        .find(query)
        .sort([("report_date", DESCENDING), ("created_at", DESCENDING)])
        .skip(skip)
        .limit(limit)
    )
    items = [
        DailyReportSummaryResponse.from_document(doc)
        async for doc in cursor
    ]
    return DailyReportListResponse(
        items=items,
        page=page,
        limit=limit,
        total=total,
    )


async def get_report_defaults(report_kind: str) -> ReportDefaultsResponse:
    """Return Settings-backed default To/CC for a report kind."""
    kind = _coerce_kind(report_kind)
    defaults = _defaults_for_kind(kind)
    return ReportDefaultsResponse(
        report_kind=kind,
        to=list(defaults.to),
        cc=list(defaults.cc),
    )


async def update_recipients(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    payload: RecipientsUpdateRequest,
) -> DailyReportResponse:
    """Partial update of working To/CC while draft."""
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)

    recipients = dict(doc.get("recipients") or {"to": [], "cc": []})
    if "to" in payload.model_fields_set and payload.to is not None:
        recipients["to"] = list(payload.to)
    if "cc" in payload.model_fields_set and payload.cc is not None:
        recipients["cc"] = list(payload.cc)

    updated = await _set_fields(
        db,
        report_id,
        recruiter_id,
        {"recipients": recipients},
        expected_status=ReportStatus.draft.value,
    )
    return DailyReportResponse.from_document(updated)


async def add_recruiter_entry(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    payload: RecruiterEntryCreateRequest,
) -> DailyReportResponse:
    """Append one recruiter entry (incomplete fields allowed)."""
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)
    _assert_kind(doc, ReportKind.recruiter.value)

    now = _utc_now()
    entry = RecruiterEntry(
        job_id=payload.job_id,
        candidate_name=payload.candidate_name,
        job_name=payload.job_name,
        candidate_contact_number=payload.candidate_contact_number,
        candidate_email=str(payload.candidate_email) if payload.candidate_email else None,
        poc=payload.poc,
        client=payload.client,
        submission_status=payload.submission_status,
        remarks=payload.remarks,
        created_at=now,
        updated_at=now,
    ).model_dump(mode="python")

    updated = await db[_DAILY_REPORTS_COL].find_one_and_update(
        {
            "report_id": report_id,
            "recruiter_id": recruiter_id,
            "status": ReportStatus.draft.value,
            "report_kind": ReportKind.recruiter.value,
        },
        {
            "$push": {"payload.entries": entry},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report is not an editable recruiter draft.",
        )
    return DailyReportResponse.from_document(updated)


async def update_recruiter_entry(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    entry_id: str,
    payload: RecruiterEntryUpdateRequest,
) -> DailyReportResponse:
    """Partial update for one recruiter entry (omit unchanged; null clears)."""
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)
    _assert_kind(doc, ReportKind.recruiter.value)

    entries = list((doc.get("payload") or {}).get("entries") or [])
    idx = next((i for i, e in enumerate(entries) if e.get("entry_id") == entry_id), None)
    if idx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry '{entry_id}' not found.",
        )

    entry = dict(entries[idx])
    for field_name in payload.model_fields_set:
        value = getattr(payload, field_name)
        if field_name == "candidate_email" and value is not None:
            value = str(value)
        if field_name == "submission_status" and value is not None:
            value = value.value if isinstance(value, SubmissionStatus) else value
        entry[field_name] = value
    entry["updated_at"] = _utc_now()
    entries[idx] = entry

    payload_doc = dict(doc.get("payload") or {})
    payload_doc["entries"] = entries
    updated = await _set_fields(
        db,
        report_id,
        recruiter_id,
        {"payload": payload_doc},
        expected_status=ReportStatus.draft.value,
    )
    return DailyReportResponse.from_document(updated)


async def delete_recruiter_entry(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    entry_id: str,
) -> DailyReportResponse:
    """Remove one recruiter entry from a draft."""
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)
    _assert_kind(doc, ReportKind.recruiter.value)

    updated = await db[_DAILY_REPORTS_COL].find_one_and_update(
        {
            "report_id": report_id,
            "recruiter_id": recruiter_id,
            "status": ReportStatus.draft.value,
            "report_kind": ReportKind.recruiter.value,
        },
        {
            "$pull": {"payload.entries": {"entry_id": entry_id}},
            "$set": {"updated_at": _utc_now()},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report is not an editable recruiter draft.",
        )
    remaining = (updated.get("payload") or {}).get("entries") or []
    if not any(e.get("entry_id") == entry_id for e in remaining):
        # Either deleted or never existed — if never existed, entries unchanged length may still look OK.
        # Re-check prior presence:
        prior = (doc.get("payload") or {}).get("entries") or []
        if not any(e.get("entry_id") == entry_id for e in prior):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Entry '{entry_id}' not found.",
            )
    return DailyReportResponse.from_document(updated)


async def update_lead_metrics(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    payload: LeadMetricsUpdateRequest,
) -> DailyReportResponse:
    """Partial update of lead numerical sections."""
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)
    _assert_kind(doc, ReportKind.lead.value)

    report_payload = dict(doc.get("payload") or {})
    section_map = {
        "recruitment_summary": "recruitment_summary",
        "team_profile_review": "team_profile_review",
        "lead_recruitment_delivery": "lead_recruitment_delivery",
    }
    for field_name, section_key in section_map.items():
        if field_name not in payload.model_fields_set:
            continue
        section_model = getattr(payload, field_name)
        if section_model is None:
            continue
        current = dict(report_payload.get(section_key) or {})
        for metric in section_model.model_fields_set:
            current[metric] = getattr(section_model, metric)
        report_payload[section_key] = current

    updated = await _set_fields(
        db,
        report_id,
        recruiter_id,
        {"payload": report_payload},
        expected_status=ReportStatus.draft.value,
    )
    return DailyReportResponse.from_document(updated)


async def add_lead_text_item(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    collection: _LeadCollection,
    payload: LeadTextItemCreateRequest,
) -> DailyReportResponse:
    """Append one text item to a lead collection."""
    _assert_lead_collection(collection)
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)
    _assert_kind(doc, ReportKind.lead.value)

    now = _utc_now()
    item = LeadTextItem(text=payload.text, created_at=now, updated_at=now).model_dump(
        mode="python"
    )
    updated = await db[_DAILY_REPORTS_COL].find_one_and_update(
        {
            "report_id": report_id,
            "recruiter_id": recruiter_id,
            "status": ReportStatus.draft.value,
            "report_kind": ReportKind.lead.value,
        },
        {
            "$push": {f"payload.{collection}": item},
            "$set": {"updated_at": now},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report is not an editable lead draft.",
        )
    return DailyReportResponse.from_document(updated)


async def update_lead_text_item(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    collection: _LeadCollection,
    item_id: str,
    payload: LeadTextItemUpdateRequest,
) -> DailyReportResponse:
    """Update text for one lead collection item."""
    _assert_lead_collection(collection)
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)
    _assert_kind(doc, ReportKind.lead.value)

    report_payload = dict(doc.get("payload") or {})
    items = list(report_payload.get(collection) or [])
    idx = next((i for i, it in enumerate(items) if it.get("item_id") == item_id), None)
    if idx is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found.",
        )
    items[idx] = {
        **items[idx],
        "text": payload.text,
        "updated_at": _utc_now(),
    }
    report_payload[collection] = items
    updated = await _set_fields(
        db,
        report_id,
        recruiter_id,
        {"payload": report_payload},
        expected_status=ReportStatus.draft.value,
    )
    return DailyReportResponse.from_document(updated)


async def delete_lead_text_item(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    collection: _LeadCollection,
    item_id: str,
) -> DailyReportResponse:
    """Delete one lead collection item."""
    _assert_lead_collection(collection)
    doc = await _require_report(db, report_id, recruiter_id)
    _assert_draft(doc)
    _assert_kind(doc, ReportKind.lead.value)

    prior = list((doc.get("payload") or {}).get(collection) or [])
    if not any(it.get("item_id") == item_id for it in prior):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' not found.",
        )

    updated = await db[_DAILY_REPORTS_COL].find_one_and_update(
        {
            "report_id": report_id,
            "recruiter_id": recruiter_id,
            "status": ReportStatus.draft.value,
            "report_kind": ReportKind.lead.value,
        },
        {
            "$pull": {f"payload.{collection}": {"item_id": item_id}},
            "$set": {"updated_at": _utc_now()},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report is not an editable lead draft.",
        )
    return DailyReportResponse.from_document(updated)


async def submit_report(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
) -> DailyReportResponse:
    """
    Validate, freeze, attempt email send, return report (always business-complete).

    HTTP layer always returns 200 with this body; ``status`` may be ``sent`` or
    ``failed`` depending on delivery.
    """
    doc = await _require_report(db, report_id, recruiter_id)
    current_status = doc.get("status")
    if current_status == ReportStatus.sent.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report already submitted.",
        )
    if current_status == ReportStatus.failed.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report already frozen in failed state. Use resend.",
        )
    if current_status != ReportStatus.draft.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot submit report in status '{current_status}'.",
        )

    _validate_for_submit(doc)
    recruiter = await get_recruiter_by_id(db, recruiter_id)
    _assert_mailbox_ready(recruiter)

    now = _utc_now()
    recipients = doc.get("recipients") or {"to": [], "cc": []}
    snapshot = {
        "to": list(recipients.get("to") or []),
        "cc": list(recipients.get("cc") or []),
    }

    # Atomic claim — concurrent double-submit safe:
    # Only one request can match status="draft" and transition away from it.
    # find_one_and_update is atomic; the loser gets claimed is None → 409.
    # Freeze writes recipients_snapshot + submitted_at in the same update.
    # Status is set to "failed" until send succeeds (then upgraded to "sent").
    claimed = await db[_DAILY_REPORTS_COL].find_one_and_update(
        {
            "report_id": report_id,
            "recruiter_id": recruiter_id,
            "status": ReportStatus.draft.value,
        },
        {
            "$set": {
                "recipients_snapshot": snapshot,
                "submitted_at": now,
                "status": ReportStatus.failed.value,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if claimed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report could not be submitted (concurrent update).",
        )

    return await _attempt_delivery(db, recruiter, claimed, is_resend=False)


async def resend_report(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
) -> DailyReportResponse:
    """Resend a FAILED report using the immutable frozen snapshot."""
    doc = await _require_report(db, report_id, recruiter_id)
    if doc.get("status") != ReportStatus.failed.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only failed reports can be resent.",
        )
    if not doc.get("recipients_snapshot"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Frozen recipient snapshot is missing; cannot resend.",
        )

    recruiter = await get_recruiter_by_id(db, recruiter_id)
    _assert_mailbox_ready(recruiter)
    return await _attempt_delivery(db, recruiter, doc, is_resend=True)


# ══════════════════════════════════════════════════════════════════════════════
# Delivery orchestration
# ══════════════════════════════════════════════════════════════════════════════

async def _attempt_delivery(
    db: AsyncIOMotorDatabase,
    recruiter: RecruiterDocument,
    report: dict[str, Any],
    *,
    is_resend: bool,
) -> DailyReportResponse:
    """
    Render email content, invoke Outlook send (delivery only), then persist
    delivery metadata + report status in ReportService.
    """
    subject, html_body = render_report_email(report)
    snapshot = report.get("recipients_snapshot") or report.get("recipients") or {}
    to_recipients = list(snapshot.get("to") or [])
    cc_recipients = list(snapshot.get("cc") or [])
    now = _utc_now()

    # Delivery layer: send only — no report DB writes inside.
    send_result = await deliver_report_email(
        recruiter=recruiter,
        to_recipients=to_recipients,
        cc_recipients=cc_recipients,
        subject=subject,
        html_body=html_body,
    )

    # Persist rotated OAuth credentials BEFORE delivery status writes so the
    # request always leaves Mongo with the latest encrypted tokens when refresh
    # occurred (including failed sends that still refreshed on 401).
    if send_result.new_access_token and send_result.new_refresh_token:
        await _persist_refreshed_tokens(
            db,
            recruiter_id=recruiter.recruiter_id,
            access_token=send_result.new_access_token,
            refresh_token=send_result.new_refresh_token,
        )

    attempt_count = int((report.get("delivery") or {}).get("attempt_count") or 0) + 1
    delivery_set: dict[str, Any] = {
        "delivery.attempt_count": attempt_count,
        "delivery.last_attempt_at": now,
        "delivery.provider": send_result.provider,
        "updated_at": now,
    }

    if send_result.success:
        delivery_set.update(
            {
                "status": ReportStatus.sent.value,
                "delivery.sent_at": now,
                "delivery.failed_at": None,
                "delivery.last_error": None,
                "delivery.provider_message_id": send_result.provider_message_id,
            }
        )
        logger.info(
            "event=reports.mail_send_ok report_id=%s resend=%s",
            report.get("report_id"),
            is_resend,
        )
    else:
        delivery_set.update(
            {
                "status": ReportStatus.failed.value,
                "delivery.failed_at": now,
                "delivery.last_error": send_result.error or "Email delivery failed.",
            }
        )
        logger.warning(
            "event=reports.mail_send_fail report_id=%s resend=%s error=%s",
            report.get("report_id"),
            is_resend,
            send_result.error,
        )

    updated = await db[_DAILY_REPORTS_COL].find_one_and_update(
        {
            "report_id": report["report_id"],
            "recruiter_id": recruiter.recruiter_id,
        },
        {"$set": delivery_set},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist delivery result.",
        )
    return DailyReportResponse.from_document(updated)


# ══════════════════════════════════════════════════════════════════════════════
# Validation helpers
# ══════════════════════════════════════════════════════════════════════════════

def _validate_for_submit(doc: dict[str, Any]) -> None:
    kind = doc.get("report_kind")
    recipients = doc.get("recipients") or {}
    to_list = [e for e in (recipients.get("to") or []) if str(e).strip()]
    if not to_list:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one To recipient is required.",
        )
    for addr in to_list + list(recipients.get("cc") or []):
        if addr and "@" not in str(addr):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid recipient email: {addr}",
            )

    if kind == ReportKind.recruiter.value:
        _validate_recruiter_payload(doc.get("payload") or {})
    elif kind == ReportKind.lead.value:
        _validate_lead_payload(doc.get("payload") or {})
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown report_kind '{kind}'.",
        )


def _validate_recruiter_payload(payload: dict[str, Any]) -> None:
    entries = payload.get("entries") or []
    if not entries:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one candidate entry is required.",
        )
    valid_statuses = {s.value for s in SubmissionStatus}
    for i, entry in enumerate(entries):
        for field in _RECRUITER_ENTRY_MANDATORY:
            value = entry.get(field)
            if value is None or (isinstance(value, str) and not str(value).strip()):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Entry {i + 1}: '{field}' is required.",
                )
        status_value = entry.get("submission_status")
        if status_value not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Entry {i + 1}: invalid submissionStatus.",
            )


def _validate_lead_payload(payload: dict[str, Any]) -> None:
    for section, field in _LEAD_METRIC_PATHS:
        section_doc = payload.get(section) or {}
        if section_doc.get(field) is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Lead metric '{section}.{field}' is required (zero is allowed).",
            )
    if not (payload.get("key_activities") or []):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one Key Activity is required.",
        )
    if not (payload.get("plan_for_tomorrow") or []):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one Plan for Tomorrow item is required.",
        )


def _assert_mailbox_ready(recruiter: RecruiterDocument) -> None:
    oauth_status = (
        recruiter.oauth_status.value
        if isinstance(recruiter.oauth_status, OAuthStatus)
        else recruiter.oauth_status
    )
    if oauth_status != OAuthStatus.active.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Recruiter mailbox is not active. Please re-authenticate.",
        )
    provider = (
        recruiter.provider.value
        if isinstance(recruiter.provider, ProviderType)
        else recruiter.provider
    )
    if provider != ProviderType.outlook.value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Phase 1 daily report email requires an Outlook-connected mailbox.",
        )
    if not recruiter.oauth_tokens_encrypted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Recruiter OAuth tokens are missing. Please re-authenticate.",
        )


# ══════════════════════════════════════════════════════════════════════════════
# Shared internals
# ══════════════════════════════════════════════════════════════════════════════

async def _require_report(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
) -> dict[str, Any]:
    doc = await db[_DAILY_REPORTS_COL].find_one(
        {"report_id": report_id, "recruiter_id": recruiter_id}
    )
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report '{report_id}' not found.",
        )
    return doc


async def _set_fields(
    db: AsyncIOMotorDatabase,
    report_id: str,
    recruiter_id: str,
    fields: dict[str, Any],
    *,
    expected_status: str,
) -> dict[str, Any]:
    set_doc = {**fields, "updated_at": _utc_now()}
    updated = await db[_DAILY_REPORTS_COL].find_one_and_update(
        {
            "report_id": report_id,
            "recruiter_id": recruiter_id,
            "status": expected_status,
        },
        {"$set": set_doc},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Report is not editable in its current state.",
        )
    return updated


async def _persist_refreshed_tokens(
    db: AsyncIOMotorDatabase,
    *,
    recruiter_id: str,
    access_token: str,
    refresh_token: str,
) -> None:
    try:
        blob = encrypt_oauth_tokens(access_token, refresh_token)
        await db[_RECRUITERS_COL].update_one(
            {"recruiter_id": recruiter_id},
            {
                "$set": {
                    "oauth_tokens_encrypted": blob,
                    "updated_at": _utc_now(),
                }
            },
        )
        logger.info(
            "event=reports.tokens_persisted recruiter_id=%s",
            recruiter_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "event=reports.token_persist_failed recruiter_id=%s error=%s",
            recruiter_id,
            exc,
        )


def _assert_draft(doc: dict[str, Any]) -> None:
    if doc.get("status") != ReportStatus.draft.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only draft reports can be modified.",
        )


def _assert_kind(doc: dict[str, Any], expected: str) -> None:
    if doc.get("report_kind") != expected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"This operation requires report_kind '{expected}'.",
        )


def _assert_lead_collection(collection: str) -> None:
    if collection not in _LEAD_COLLECTIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown lead collection '{collection}'.",
        )


def _coerce_kind(value: str | ReportKind) -> str:
    if isinstance(value, ReportKind):
        return value.value
    try:
        return ReportKind(value).value
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid reportKind '{value}'.",
        ) from exc


def _coerce_status(value: str) -> str:
    try:
        return ReportStatus(value).value
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status '{value}'.",
        ) from exc


def _empty_payload(report_kind: str) -> dict[str, Any]:
    if report_kind == ReportKind.lead.value:
        return empty_lead_payload()
    return empty_recruiter_payload()


def _defaults_for_kind(report_kind: str) -> ReportRecipients:
    if report_kind == ReportKind.lead.value:
        return ReportRecipients(
            to=_parse_email_list(settings.REPORT_LEAD_DEFAULT_TO),
            cc=_parse_email_list(settings.REPORT_LEAD_DEFAULT_CC),
        )
    return ReportRecipients(
        to=_parse_email_list(settings.REPORT_RECRUITER_DEFAULT_TO),
        cc=_parse_email_list(settings.REPORT_RECRUITER_DEFAULT_CC),
    )


def _parse_email_list(raw: str) -> list[str]:
    if not raw or not str(raw).strip():
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def _assert_report_date_allowed(report_date: str) -> None:
    tz = ZoneInfo(settings.REPORT_TZ)
    today = datetime.now(tz).date()
    try:
        target = date.fromisoformat(report_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reportDate must be a valid YYYY-MM-DD date.",
        ) from exc
    lookback = max(int(settings.REPORT_DATE_LOOKBACK_DAYS), 0)
    earliest = today - timedelta(days=lookback)
    if target > today:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reportDate cannot be in the future.",
        )
    if target < earliest:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"reportDate is outside the allowed lookback of "
                f"{lookback} calendar day(s)."
            ),
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
