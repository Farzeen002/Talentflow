"""
app/api/reports.py

FastAPI router for the Daily Reports module.

Routes (all require JWT authentication):
  POST   /reports/open
  GET    /reports/defaults?reportKind=
  GET    /reports                         — paginated history (Phase 1 MVP)
  GET    /reports/{report_id}
  PATCH  /reports/{report_id}/recipients
  POST   /reports/{report_id}/entries
  PATCH  /reports/{report_id}/entries/{entry_id}
  DELETE /reports/{report_id}/entries/{entry_id}
  PATCH  /reports/{report_id}/lead/metrics
  POST   /reports/{report_id}/lead/key-activities
  PATCH  /reports/{report_id}/lead/key-activities/{item_id}
  DELETE /reports/{report_id}/lead/key-activities/{item_id}
  POST   /reports/{report_id}/lead/challenges-risks
  PATCH  /reports/{report_id}/lead/challenges-risks/{item_id}
  DELETE /reports/{report_id}/lead/challenges-risks/{item_id}
  POST   /reports/{report_id}/lead/plan-for-tomorrow
  PATCH  /reports/{report_id}/lead/plan-for-tomorrow/{item_id}
  DELETE /reports/{report_id}/lead/plan-for-tomorrow/{item_id}
  POST   /reports/{report_id}/submit      — always HTTP 200 + report body
  POST   /reports/{report_id}/resend      — always HTTP 200 + report body

Route ordering
--------------
1. Static paths first: ``/open``, ``/defaults``, ``GET ""`` (list).
2. Nested ``/{report_id}/...`` resources next (recipients, entries, lead, submit).
3. Bare ``GET /{report_id}`` last — never before static or nested paths.

Behaviour notes
---------------
* ``POST /open`` is idempotent open-or-create and always returns HTTP 200.
* Submit/resend return HTTP 200 even when ``status`` is ``failed`` after a
  freeze + mail failure (business submission completed; delivery did not).
* ``submittedAt`` = freeze time; ``delivery.sentAt`` = successful mail time.
* Graph ``sendMail`` returns 202 with no message id — ``providerMessageId``
  remaining null is expected.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.mongo import get_db
from app.dependencies import get_current_user
from app.models.report import (
    DailyReportListResponse,
    DailyReportResponse,
    LeadMetricsUpdateRequest,
    LeadTextItemCreateRequest,
    LeadTextItemUpdateRequest,
    RecipientsUpdateRequest,
    RecruiterEntryCreateRequest,
    RecruiterEntryUpdateRequest,
    ReportDefaultsResponse,
    ReportOpenRequest,
)
from app.services import report_service

router = APIRouter(prefix="/reports", tags=["Reports"])

_DB = Annotated[AsyncIOMotorDatabase, Depends(get_db)]
_User = Annotated[dict, Depends(get_current_user)]

_LeadPathCollection = Literal[
    "key-activities",
    "challenges-risks",
    "plan-for-tomorrow",
]

_LEAD_PATH_TO_FIELD: dict[str, str] = {
    "key-activities": "key_activities",
    "challenges-risks": "challenges_risks",
    "plan-for-tomorrow": "plan_for_tomorrow",
}


# ══════════════════════════════════════════════════════════════════════════════
# Open / defaults / list  (literal paths before /{report_id})
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/open",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Open or create a daily report draft (idempotent)",
)
async def open_report_endpoint(
    payload: ReportOpenRequest,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """
    Idempotent open-or-create for ``(recruiterId, reportDate, reportKind)``.

    Always returns HTTP 200 whether a new draft was created or an existing
    draft/failed report was reopened. Returns 409 if a SENT report already
    exists for the business key.
    """
    return await report_service.open_report(
        db, payload, current_user["recruiter_id"]
    )


@router.get(
    "/defaults",
    response_model=ReportDefaultsResponse,
    response_model_by_alias=True,
    summary="Get default To/CC recipients for a report kind",
)
async def get_defaults_endpoint(
    db: _DB,
    current_user: _User,
    report_kind: str = Query(..., alias="reportKind"),
) -> ReportDefaultsResponse:
    """Return Settings-backed default recipients for the given report kind."""
    _ = db  # auth + DI parity with other routes; no DB read for defaults
    _ = current_user
    return await report_service.get_report_defaults(report_kind)


@router.get(
    "",
    response_model=DailyReportListResponse,
    response_model_by_alias=True,
    summary="List daily reports for the authenticated recruiter",
)
async def list_reports_endpoint(
    db: _DB,
    current_user: _User,
    report_date: Optional[str] = Query(None, alias="reportDate"),
    report_kind: Optional[str] = Query(None, alias="reportKind"),
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1),
) -> DailyReportListResponse:
    """
    Paginated report history. Default sort: reportDate desc, createdAt desc.

    Maximum ``limit`` is configured via ``REPORT_LIST_MAX_LIMIT`` (Settings).
    """
    return await report_service.list_reports(
        db,
        current_user["recruiter_id"],
        report_date=report_date,
        report_kind=report_kind,
        status_filter=status_filter,
        page=page,
        limit=limit,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Recipients / entries / lead / submit  (all under /{report_id}/...)
# Bare GET /{report_id} is registered LAST so it cannot shadow nested paths.
# Static /open and /defaults are registered above (before any {report_id}).
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/{report_id}/recipients",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Update working To/CC recipients (draft only)",
)
async def update_recipients_endpoint(
    report_id: str,
    payload: RecipientsUpdateRequest,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """Partial update: omit a field to leave it unchanged; provided lists replace wholly."""
    return await report_service.update_recipients(
        db, report_id, current_user["recruiter_id"], payload
    )


# ══════════════════════════════════════════════════════════════════════════════
# Recruiter entries
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{report_id}/entries",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Add a recruiter candidate entry (draft)",
)
async def add_entry_endpoint(
    report_id: str,
    payload: RecruiterEntryCreateRequest,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """Persists immediately. Incomplete fields are allowed until submit."""
    return await report_service.add_recruiter_entry(
        db, report_id, current_user["recruiter_id"], payload
    )


@router.patch(
    "/{report_id}/entries/{entry_id}",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Partial-update a recruiter entry (draft)",
)
async def update_entry_endpoint(
    report_id: str,
    entry_id: str,
    payload: RecruiterEntryUpdateRequest,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """Omitted fields unchanged; explicit null clears a draft field."""
    return await report_service.update_recruiter_entry(
        db, report_id, current_user["recruiter_id"], entry_id, payload
    )


@router.delete(
    "/{report_id}/entries/{entry_id}",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Delete a recruiter entry (draft)",
)
async def delete_entry_endpoint(
    report_id: str,
    entry_id: str,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    return await report_service.delete_recruiter_entry(
        db, report_id, current_user["recruiter_id"], entry_id
    )


# ══════════════════════════════════════════════════════════════════════════════
# Lead payload
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/{report_id}/lead/metrics",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Partial-update lead numerical metrics (draft)",
)
async def update_lead_metrics_endpoint(
    report_id: str,
    payload: LeadMetricsUpdateRequest,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """
    Partial nested patch. Use ``0`` for explicit zero; ``null`` clears a metric
    back to incomplete; omit a key to leave it unchanged.
    """
    return await report_service.update_lead_metrics(
        db, report_id, current_user["recruiter_id"], payload
    )


@router.post(
    "/{report_id}/lead/{collection}",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Add a lead text item (draft)",
)
async def add_lead_item_endpoint(
    report_id: str,
    collection: _LeadPathCollection,
    payload: LeadTextItemCreateRequest,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    field = _LEAD_PATH_TO_FIELD[collection]
    return await report_service.add_lead_text_item(
        db,
        report_id,
        current_user["recruiter_id"],
        field,  # type: ignore[arg-type]
        payload,
    )


@router.patch(
    "/{report_id}/lead/{collection}/{item_id}",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Update a lead text item (draft)",
)
async def update_lead_item_endpoint(
    report_id: str,
    collection: _LeadPathCollection,
    item_id: str,
    payload: LeadTextItemUpdateRequest,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    field = _LEAD_PATH_TO_FIELD[collection]
    return await report_service.update_lead_text_item(
        db,
        report_id,
        current_user["recruiter_id"],
        field,  # type: ignore[arg-type]
        item_id,
        payload,
    )


@router.delete(
    "/{report_id}/lead/{collection}/{item_id}",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Delete a lead text item (draft)",
)
async def delete_lead_item_endpoint(
    report_id: str,
    collection: _LeadPathCollection,
    item_id: str,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    field = _LEAD_PATH_TO_FIELD[collection]
    return await report_service.delete_lead_text_item(
        db,
        report_id,
        current_user["recruiter_id"],
        field,  # type: ignore[arg-type]
        item_id,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Submit / resend
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{report_id}/submit",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Submit daily report (validate, freeze, send email)",
)
async def submit_report_endpoint(
    report_id: str,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """
    Always returns HTTP 200 with the updated report.

    If email delivery fails after freeze, ``status`` is ``failed`` in the body
    (not HTTP 502). ``submittedAt`` is set at freeze; ``delivery.sentAt`` only
    after successful Graph sendMail (202 Accepted, no message id).
    """
    return await report_service.submit_report(
        db, report_id, current_user["recruiter_id"]
    )


@router.post(
    "/{report_id}/resend",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Resend a failed daily report (frozen snapshot)",
)
async def resend_report_endpoint(
    report_id: str,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """
    Always returns HTTP 200. Uses immutable payload + recipient snapshot.
    Recipients cannot be changed on resend.
    """
    return await report_service.resend_report(
        db, report_id, current_user["recruiter_id"]
    )


@router.get(
    "/{report_id}",
    response_model=DailyReportResponse,
    response_model_by_alias=True,
    summary="Get a daily report by id",
)
async def get_report_endpoint(
    report_id: str,
    db: _DB,
    current_user: _User,
) -> DailyReportResponse:
    """
    Registered after static paths (``/open``, ``/defaults``) and after all
    ``/{report_id}/...`` nested routes to avoid FastAPI path conflicts.
    """
    return await report_service.get_report(
        db, report_id, current_user["recruiter_id"]
    )
