"""
app/services/job_service.py

Service layer for Job management and candidate queries.

Responsibilities:
  - MongoDB index bootstrap for the jobs collection
  - Job CRUD (create, list, get, update filters, update status)
  - Importable job discovery from candidates (GET /jobs/importable)
  - Candidate grouping by job_id (query-time, no mapping table)
  - Candidate filtering using stored job filter thresholds (query-time)
  - Single candidate detail fetch

Design constraints:
  - NO FastAPI request/response objects (those live in app/api/jobs.py)
  - All MongoDB I/O is async (Motor)
  - Recruiter isolation enforced on every query via recruiter_id from JWT
  - Filter thresholds are read from the job document — never hardcoded here
  - Zero changes to the candidate collection schema or ingestion pipeline
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING
from redis import Redis as _Redis
from rq import Queue as _RQQueue

from app.config import get_settings
from app.models.job import (
    CandidateListResponse,
    CandidateSummary,
    ImportableJob,
    JobCounts,
    JobCreate,
    JobFilters,
    JobResponse,
    JobStatus,
    JobUpdate,
)
from app.services.candidate_filter import build_candidate_filter_query, build_active_candidate_query

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── Collection names ──────────────────────────────────────────────────────────
_JOBS_COL       = "jobs"
_CANDIDATES_COL = "candidates"

# ── Candidate sort options ────────────────────────────────────────────────────
_SORT_MAP: dict[str, list[tuple[str, int]]] = {
    "created_at_desc":   [("created_at",              DESCENDING)],
    "created_at_asc":    [("created_at",              ASCENDING)],
    "name_asc":          [("metadata.name",            ASCENDING)],
    "notice_period_asc": [("qa.notice_period_days",    ASCENDING)],
    "ctc_desc":          [("qa.expected_ctc",          DESCENDING)],
}
_DEFAULT_SORT = "created_at_desc"

# ── Lightweight projection for candidate list views ───────────────────────────
_LIST_PROJECTION: dict[str, int] = {
    "candidate_id":           1,
    "metadata.name":          1,
    "metadata.current_role":  1,
    "metadata.current_company": 1,
    "metadata.experience_years": 1,
    "qa.notice_period_days":  1,
    "qa.current_ctc":         1,
    "qa.expected_ctc":        1,
    "resume.status":          1,
    "processing.needs_review": 1,
    "created_at":             1,
    "blacklist":              1,
    "_id":                    0,
}

# ── Projection for candidate detail (exclude large raw text fields) ───────────
_DETAIL_PROJECTION: dict[str, int] = {
    "_id":                  0,
    "raw_email.body":       0,   # exclude large raw HTML body
    "raw_email.clean_text": 0,   # exclude large extracted text
}


# ══════════════════════════════════════════════════════════════════════════════
# Index bootstrap
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_job_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Create required indexes for Job CRUD and importable-job discovery.
    Idempotent — safe to call on every application startup.

    Indexes created on ``jobs``:
      - Compound unique on (job_id, recruiter_id) — primary lookup + dedup
      - recruiter_id — fast per-recruiter job list
      - (status, recruiter_id) — filtered job list views
      - (is_archived, recruiter_id) — archived filter on list views

    Indexes created on ``candidates`` (job discovery / per-job grouping):
      - (recruiter_id, job_id) — importable-jobs aggregation and job candidate counts
    """
    col = db[_JOBS_COL]
    await col.create_index(
        [("job_id", ASCENDING), ("recruiter_id", ASCENDING)],
        unique=True,
        name="uq_job_recruiter",
    )
    await col.create_index(
        [("recruiter_id", ASCENDING)],
        name="idx_jobs_recruiter",
    )
    await col.create_index(
        [("status", ASCENDING), ("recruiter_id", ASCENDING)],
        name="idx_jobs_status_recruiter",
    )
    await col.create_index(
        [("is_archived", ASCENDING), ("recruiter_id", ASCENDING)],
        name="idx_jobs_archived_recruiter",
    )

    # Supports GET /jobs/importable aggregation and existing per-job candidate queries.
    await db[_CANDIDATES_COL].create_index(
        [("recruiter_id", ASCENDING), ("job_id", ASCENDING)],
        name="idx_candidates_recruiter_job",
    )

    logger.info(
        "MongoDB indexes ensured on '%s' and candidates (idx_candidates_recruiter_job).",
        _JOBS_COL,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Private query builders
# ══════════════════════════════════════════════════════════════════════════════

def _base_query(job_id: str, recruiter_id: str) -> dict[str, Any]:
    """
    Return ALL active (non-blacklisted) candidates for a job.

    Spreads ``build_active_candidate_query()`` as the visibility base so that
    blacklisted candidates are excluded from:
      - ``view=all`` candidate lists
      - ``counts.total`` on job cards
      - ``create_job()`` eligibility check
    """
    return {
        **build_active_candidate_query(recruiter_id),
        "job_id": job_id,
    }


def _blacklisted_query(job_id: str, recruiter_id: str) -> dict[str, Any]:
    """
    Return blacklisted candidates for a job.

    Intentionally separate from ``build_active_candidate_query()`` which
    excludes blacklisted candidates from active list/count views.
    """
    return {
        "job_id":                   job_id,
        "recruiter_id":             recruiter_id,
        "blacklist.is_blacklisted": True,
    }


def _filtered_query(job_id: str, recruiter_id: str, filters: JobFilters) -> dict[str, Any]:
    """
    Return only candidates that pass all filter thresholds.

    Delegates to ``candidate_filter.build_candidate_filter_query`` which is the
    single source of truth for this logic.  Both this async path and the ATS
    worker's sync path use the same underlying query — changing criteria in
    ``candidate_filter.py`` updates both automatically.
    """
    return build_candidate_filter_query(
        job_id=       job_id,
        recruiter_id= recruiter_id,
        filters=      filters.model_dump(),
    )


async def _get_counts(
    db: AsyncIOMotorDatabase,
    job_id: str,
    recruiter_id: str,
    filters: JobFilters,
) -> JobCounts:
    """Run two count_documents calls and return JobCounts."""
    total    = await db[_CANDIDATES_COL].count_documents(_base_query(job_id, recruiter_id))
    filtered = await db[_CANDIDATES_COL].count_documents(_filtered_query(job_id, recruiter_id, filters))
    return JobCounts(total=total, filtered=filtered)


def _doc_to_response(doc: dict[str, Any], counts: JobCounts | None = None) -> JobResponse:
    """Convert a raw MongoDB job document to a JobResponse."""
    return JobResponse(
        job_id=          doc["job_id"],
        recruiter_id=    doc["recruiter_id"],
        title=           doc["title"],
        description=     doc.get("description", ""),
        location=        doc.get("location", ""),
        employment_type= doc.get("employment_type", ""),
        priority=        doc.get("priority", "Medium"),
        experience=      doc.get("experience", ""),
        filters=         JobFilters(**doc["filters"]),
        status=          doc["status"],
        is_archived=     doc.get("is_archived", False),
        counts=          counts,
        created_at=      doc["created_at"],
        updated_at=      doc["updated_at"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Job CRUD
# ══════════════════════════════════════════════════════════════════════════════

async def create_job(
    db:           AsyncIOMotorDatabase,
    payload:      JobCreate,
    recruiter_id: str,
) -> JobResponse:
    """
    Create a new job document in MongoDB.

    Validates that at least one candidate exists for this job_id before
    creating the posting. In the NVite flow, emails always arrive before
    the recruiter creates the job — zero candidates almost certainly means
    a typo in the job code (e.g. 'DBA020' instead of 'DBA002').

    Raises:
        HTTPException(422): No candidates found for the given job_id.
        HTTPException(409): Duplicate job_id for this recruiter.
        HTTPException(500): Database write failure.
    """
    # ── Guard: verify candidates exist for this job_id before creating ─────────────
    candidate_count = await db[_CANDIDATES_COL].count_documents({
        "job_id":       payload.job_id,   # already normalized to uppercase by validator
        "recruiter_id": recruiter_id,
    })
    if candidate_count == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"No candidates found for job ID '{payload.job_id}'. "
                "Please verify the job code before creating this posting."
            ),
        )

    now = datetime.now(tz=timezone.utc)

    # ── Build jd_analysis sub-document ──────────────────────────────────────────────────
    _has_description = bool(payload.description and payload.description.strip())
    if _has_description:
        jd_analysis_doc: dict[str, Any] = {
            "status":       "pending",
            "result":       None,
            "error":        None,
            "version":      0,          # starts at 0; first $inc in jd_tasks → 1
            "triggered_at": None,
            "completed_at": None,
        }
    else:
        jd_analysis_doc = {"status": "not_available"}

    doc: dict[str, Any] = {
        "job_id":          payload.job_id,
        "recruiter_id":    recruiter_id,
        "title":           payload.title,
        "description":     payload.description,
        "location":        payload.location,
        "employment_type": payload.employment_type,
        "priority":        payload.priority,
        "experience":      payload.experience,
        "filters": {
            "is_ok_client":           True,    # always enforced
            "is_c2h_ok":              True,    # always enforced
            "has_pf_account":         True,    # always enforced
            "max_notice_period_days": payload.max_notice_period_days,
        },
        "status":       JobStatus.active.value,
        "jd_analysis":  jd_analysis_doc,
        "created_at":   now,
        "updated_at":   now,
    }

    try:
        await db[_JOBS_COL].insert_one(doc)
    except Exception as exc:
        err = str(exc)
        if "duplicate key" in err.lower() or "E11000" in err:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Job '{payload.job_id}' already exists for this recruiter.",
            ) from exc
        logger.exception("DB error creating job job_id=%s: %s", payload.job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create job.",
        ) from exc

    logger.info("Job created: job_id=%s recruiter_id=%s", payload.job_id, recruiter_id)

    # ── Enqueue JD analysis (only when description is present) ───────────────
    if _has_description:
        _enqueue_jd_analysis(payload.job_id, recruiter_id)

    filters = JobFilters(**doc["filters"])
    counts  = await _get_counts(db, payload.job_id, recruiter_id, filters)
    return _doc_to_response(doc, counts)


# ══════════════════════════════════════════════════════════════════════════════
# Importable jobs (Create Job discovery)
# ══════════════════════════════════════════════════════════════════════════════

async def list_importable_jobs(
    db:           AsyncIOMotorDatabase,
    recruiter_id: str,
) -> list[ImportableJob]:
    """
    Discover Naukri job codes present on this recruiter's candidates that have
    not yet been created in the ``jobs`` collection.

    Intended for the Create Job "Import Existing Job" dropdown:
      - ``jobId`` / ``jobTitle`` auto-fill the create form
      - ``candidateCount`` is an informational snapshot only (not business logic)

    Blacklist state is intentionally ignored — this is job discovery, not
    candidate management.  Archived jobs still count as "already created"
    because ``uq_job_recruiter`` blocks a second insert.

    Raises:
        HTTPException(500): Database / aggregation failure.
    """
    pipeline: list[dict[str, Any]] = [
        {
            "$match": {
                "recruiter_id": recruiter_id,
                "job_id":       {"$nin": [None, ""]},
            }
        },
        {"$sort": {"created_at": DESCENDING}},
        {
            "$group": {
                "_id":            "$job_id",
                "jobTitle":       {"$first": "$metadata.job_title"},
                "candidateCount": {"$sum": 1},
                "titleSet":       {"$addToSet": "$metadata.job_title"},
            }
        },
        {
            "$lookup": {
                "from": _JOBS_COL,
                "let":  {"jid": "$_id"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$job_id", "$$jid"]},
                                    {"$eq": ["$recruiter_id", recruiter_id]},
                                ]
                            }
                        }
                    },
                    {"$limit": 1},
                    {"$project": {"_id": 1}},
                ],
                "as": "existing_job",
            }
        },
        {"$match": {"existing_job": {"$size": 0}}},
        {
            "$project": {
                "_id":            0,
                "jobId":          "$_id",
                "jobTitle":       1,
                "candidateCount": 1,
                "titleSet":       1,
            }
        },
        {"$sort": {"candidateCount": DESCENDING, "jobId": ASCENDING}},
    ]

    started = time.perf_counter()
    try:
        cursor = db[_CANDIDATES_COL].aggregate(pipeline)
        rows: list[dict[str, Any]] = await cursor.to_list(length=None)
    except Exception as exc:
        logger.exception(
            "DB error listing importable jobs recruiter_id=%s: %s",
            recruiter_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list importable jobs.",
        ) from exc

    duration_ms = int((time.perf_counter() - started) * 1000)
    results: list[ImportableJob] = []

    for row in rows:
        job_id = row["jobId"]
        raw_title = row.get("jobTitle")
        job_title = (
            raw_title.strip()
            if isinstance(raw_title, str) and raw_title.strip()
            else None
        )

        distinct_titles = {
            t.strip()
            for t in (row.get("titleSet") or [])
            if isinstance(t, str) and t.strip()
        }
        if len(distinct_titles) > 1:
            logger.warning(
                "event=importable_jobs.data_quality reason=multiple_titles "
                "recruiter_id=%s job_id=%s distinct_title_count=%d",
                recruiter_id, job_id, len(distinct_titles),
            )

        if job_title is None:
            logger.warning(
                "event=importable_jobs.data_quality reason=missing_title "
                "recruiter_id=%s job_id=%s",
                recruiter_id, job_id,
            )

        results.append(
            ImportableJob(
                job_id=job_id,
                job_title=job_title,
                candidate_count=int(row.get("candidateCount") or 0),
            )
        )

    logger.info(
        "Importable jobs listed: recruiter_id=%s count=%d duration_ms=%d",
        recruiter_id, len(results), duration_ms,
    )
    return results


async def get_jobs_for_recruiter(
    db:               AsyncIOMotorDatabase,
    recruiter_id:     str,
    *,
    status_filter:    str | None = None,
    page:             int = 1,
    limit:            int = 20,
    include_archived: bool = False,
) -> dict[str, Any]:
    """
    Return a paginated list of jobs for the authenticated recruiter,
    each including total and filtered candidate counts.

    By default archived jobs are excluded from the list.  Pass
    ``include_archived=True`` to include them (audit / recovery view).

    Raises:
        HTTPException(500): Database read failure.
    """
    query: dict[str, Any] = {"recruiter_id": recruiter_id}
    if status_filter:
        query["status"] = status_filter
    if not include_archived:
        # "$ne: True" correctly handles both is_archived=false and missing field
        query["is_archived"] = {"$ne": True}

    skip = (page - 1) * limit

    try:
        total_jobs = await db[_JOBS_COL].count_documents(query)
        cursor = (
            db[_JOBS_COL]
            .find(query, {"_id": 0})
            .sort("created_at", DESCENDING)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
    except Exception as exc:
        logger.exception("DB error listing jobs for recruiter_id=%s: %s", recruiter_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve jobs.",
        ) from exc

    jobs = []
    for doc in docs:
        filters = JobFilters(**doc["filters"])
        counts  = await _get_counts(db, doc["job_id"], recruiter_id, filters)
        jobs.append(_doc_to_response(doc, counts))

    return {"total": total_jobs, "page": page, "limit": limit, "jobs": jobs}


async def get_job_by_id(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
) -> JobResponse:
    """
    Fetch a single job by its Naukri code, scoped to the recruiter.

    Raises:
        HTTPException(404): Job not found.
        HTTPException(500): Database read failure.
    """
    try:
        doc = await db[_JOBS_COL].find_one(
            {"job_id": job_id.upper(), "recruiter_id": recruiter_id},
            {"_id": 0},
        )
    except Exception as exc:
        logger.exception("DB error fetching job_id=%s: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve job.",
        ) from exc

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")

    filters = JobFilters(**doc["filters"])
    counts  = await _get_counts(db, doc["job_id"], recruiter_id, filters)
    return _doc_to_response(doc, counts)


async def update_job(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
    update:       JobUpdate,
) -> JobResponse:
    """
    Update recruiter-editable fields on a job.

    Editable fields fall into two categories:

    **Safe fields** (no downstream impact):
        location, priority, experience, status,
        filters.max_notice_period_days, is_archived

    **JD-affecting fields** (trigger jd_analysis reset + auto re-enqueue):
        title, description, employment_type

    On a JD-affecting change the service:
      1. Resets jd_analysis.status to ``pending`` (so the worker can re-claim).
      2. Clears jd_analysis.result / error / timestamps.
      3. Does NOT touch jd_analysis.version — only ``_set_completed()`` may
         increment it.  Existing ATS scores become version-stale automatically
         and are re-scored on the next ``calculate-ats`` call.
      4. Auto-enqueues JD analysis (same as create_job).

    If description is cleared to an empty string:
      - jd_analysis.status is set to ``not_available``.
      - JD analysis is NOT enqueued.

    Raises:
        HTTPException(404): Job not found.
        HTTPException(500): DB read or write failure.
    """
    job_id = job_id.upper()
    now    = datetime.now(tz=timezone.utc)

    # ── Pre-fetch: validate existence + resolve effective description ────────────
    # We need the current description to decide whether a title/employment_type
    # change should trigger JD re-analysis (only possible if description exists).
    try:
        existing = await db[_JOBS_COL].find_one(
            {"job_id": job_id, "recruiter_id": recruiter_id},
            {"description": 1, "_id": 0},
        )
    except Exception as exc:
        logger.exception("DB error pre-fetching job_id=%s for update: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve job.",
        ) from exc

    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # Effective description = new value if provided, else current value in DB
    effective_description: str = (
        update.description
        if update.description is not None
        else existing.get("description", "")
    )

    # ── JD-dirtiness and description-cleared flags ───────────────────────────
    jd_dirty = any([
        update.title           is not None,
        update.description     is not None,
        update.employment_type is not None,
    ])
    # description_cleared: JD-dirty but effective description is empty
    # (covers: description explicitly set to "", OR title changed on a job
    # that never had a description).  In both cases no LLM call is possible.
    description_cleared = jd_dirty and not (
        effective_description and effective_description.strip()
    )

    # ── Build $set payload ───────────────────────────────────────────────────
    set_fields: dict[str, Any] = {"updated_at": now}

    # Safe fields
    if update.location is not None:
        set_fields["location"] = update.location
    if update.priority is not None:
        set_fields["priority"] = update.priority
    if update.experience is not None:
        set_fields["experience"] = update.experience
    if update.status is not None:
        set_fields["status"] = update.status.value
    if update.max_notice_period_days is not None:
        set_fields["filters.max_notice_period_days"] = update.max_notice_period_days
    if update.is_archived is not None:
        set_fields["is_archived"] = update.is_archived

    # JD-affecting fields
    if update.title is not None:
        set_fields["title"] = update.title
    if update.employment_type is not None:
        set_fields["employment_type"] = update.employment_type
    if update.description is not None:
        set_fields["description"] = update.description

    # JD analysis side effects
    # Dot-notation is used deliberately — jd_analysis.version is intentionally
    # omitted so only _set_completed() can increment it.
    if jd_dirty:
        if description_cleared:
            # Description absent or cleared — no JD analysis possible
            set_fields["jd_analysis.status"]       = "not_available"
            set_fields["jd_analysis.result"]       = None
            set_fields["jd_analysis.error"]        = None
            set_fields["jd_analysis.triggered_at"] = None
            set_fields["jd_analysis.completed_at"] = None
        else:
            # JD content changed — reset to pending so worker can re-claim.
            # _atomic_claim() in jd_tasks.py requires status=pending to claim;
            # without this reset the worker silently skips a completed job.
            set_fields["jd_analysis.status"]       = "pending"
            set_fields["jd_analysis.result"]       = None
            set_fields["jd_analysis.error"]        = None
            set_fields["jd_analysis.triggered_at"] = None
            set_fields["jd_analysis.completed_at"] = None

    # ── Apply update ────────────────────────────────────────────────────────────
    try:
        result = await db[_JOBS_COL].update_one(
            {"job_id": job_id, "recruiter_id": recruiter_id},
            {"$set": set_fields},
        )
    except Exception as exc:
        logger.exception("DB error updating job_id=%s: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update job.",
        ) from exc

    if result.matched_count == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # ── Auto-enqueue JD analysis if content changed and description exists ────
    if jd_dirty and not description_cleared:
        _enqueue_jd_analysis(job_id, recruiter_id)

    updated_field_names = [k for k in set_fields if k != "updated_at"]
    logger.info(
        "Job updated: job_id=%s fields=%s jd_dirty=%s description_cleared=%s",
        job_id, updated_field_names, jd_dirty, description_cleared,
    )
    return await get_job_by_id(db, job_id, recruiter_id)


async def delete_job(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
) -> dict:
    """
    Hard delete a job document — allowed only if no candidates are linked.

    Eligibility rule (single check):
        candidate_count == 0  → delete allowed
        candidate_count > 0   → 409 Conflict (use archive instead)

    ATS scores are intentionally NOT checked separately.  ATS cannot run
    without candidates — the guard in ``_trigger_ats()`` enforces this.
    A job with zero candidates will have zero ATS records by system design.
    Adding a second count_documents on ``candidate_job_scores`` would be
    wasted I/O for a case that cannot occur in normal operation.

    Only the job document itself is deleted.  There is no cascading delete.
    By definition (zero candidates) there is nothing else to clean up —
    ``jd_analysis`` and ``ats_run`` are embedded sub-documents that vanish
    with the job document automatically.

    Raises:
        HTTPException(404): Job not found (or belongs to another recruiter).
        HTTPException(409): Candidates exist — use archive instead.
        HTTPException(500): DB read or write failure.
    """
    job_id = job_id.upper()

    # ── Step 1: Validate existence + recruiter ownership ─────────────────────
    try:
        job_doc = await db[_JOBS_COL].find_one(
            {"job_id": job_id, "recruiter_id": recruiter_id},
            {"job_id": 1, "_id": 0},
        )
    except Exception as exc:
        logger.exception("DB error fetching job_id=%s for delete: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve job.",
        ) from exc

    if job_doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # ── Step 2: Candidate linkage check ──────────────────────────────────────
    try:
        candidate_count = await db[_CANDIDATES_COL].count_documents({
            "job_id":       job_id,
            "recruiter_id": recruiter_id,
        })
    except Exception as exc:
        logger.exception(
            "DB error counting candidates for job_id=%s: %s", job_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to check candidate linkage.",
        ) from exc

    if candidate_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job '{job_id}' cannot be deleted: {candidate_count} candidate(s) "
                "already exist. Use archive instead."
            ),
        )

    # ── Step 3: Hard delete — job document only ───────────────────────────────
    try:
        result = await db[_JOBS_COL].delete_one({
            "job_id":       job_id,
            "recruiter_id": recruiter_id,
        })
    except Exception as exc:
        logger.exception("DB error deleting job_id=%s: %s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete job.",
        ) from exc

    if result.deleted_count == 0:
        # Rare race condition: job deleted between our eligibility check and here
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    logger.info(
        "event=job.hard_deleted job_id=%s recruiter_id=%s",
        job_id, recruiter_id,
    )
    return {
        "success": True,
        "job_id":  job_id,
        "message": "Job deleted successfully.",
    }




# ══════════════════════════════════════════════════════════════════════════════
# JD analysis enqueue helper
# ══════════════════════════════════════════════════════════════════════════════

_JD_ANALYSIS_QUEUE = "jd-analysis"


def _enqueue_jd_analysis(job_id: str, recruiter_id: str) -> None:
    """
    Enqueue a JD analysis task onto the ``jd-analysis`` RQ queue.

    This function mirrors ``email_tasks._enqueue_preprocessing_task`` exactly.
    Failures here (Redis unavailable, misconfigured URL) are logged as
    warnings but NEVER raised — job creation must not be blocked by
    queue unavailability.

    The job document already has ``jd_analysis.status = "pending"`` at this
    point, so if Redis is unavailable the status stays visible as pending
    and can be retried by re-enqueueing manually.

    Args:
        job_id:       Naukri job code (e.g. ``"DBA002"``), already uppercased.
        recruiter_id: UUID of the owning recruiter.
    """
    redis_url = settings.REDIS_URL
    if not redis_url:
        logger.warning(
            "event=jd_analysis.enqueue_skipped "
            "reason=REDIS_URL_not_configured job_id=%s",
            job_id,
        )
        return

    try:
        conn  = _Redis.from_url(redis_url, decode_responses=False)
        queue = _RQQueue(name=_JD_ANALYSIS_QUEUE, connection=conn)
        rq_job = queue.enqueue(
            "app.workers.jd_tasks.analyze_jd_task",
            job_id,
            recruiter_id,
        )
        logger.info(
            "event=jd_analysis.enqueued job_id=%s recruiter_id=%s rq_job_id=%s",
            job_id, recruiter_id, rq_job.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "event=jd_analysis.enqueue_failed job_id=%s detail=%s "
            "— JD analysis will not run automatically.",
            job_id, exc,
        )


# ══════════════════════════════════════════════════════════════════════════════
# ATS scoring enqueue + service functions
# ══════════════════════════════════════════════════════════════════════════════

_ATS_SCORING_QUEUE = "ats-scoring"


def _enqueue_ats_calculation(job_id: str, recruiter_id: str, force: bool) -> str | None:
    """
    Enqueue an ATS scoring task onto the dedicated ``ats-scoring`` RQ queue.

    Failures are logged as warnings and never raised so the API caller
    receives a clean 202 even when Redis is temporarily unavailable.
    The job document already has ``ats_run.status = "queued"`` at this point;
    if Redis is unavailable the recruiter can re-trigger once it recovers.

    Args:
        job_id:       Naukri job code (already uppercased).
        recruiter_id: UUID of the owning recruiter.
        force:        Passed straight through to the worker.
                      True  → ``rerun-ats``       (score all candidates).
                      False → ``calculate-ats``   (incremental, skip valid scores).

    Returns:
        The RQ job ID string on success, or ``None`` on failure.
    """
    redis_url = settings.REDIS_URL
    if not redis_url:
        logger.warning(
            "event=ats_scoring.enqueue_skipped "
            "reason=REDIS_URL_not_configured job_id=%s",
            job_id,
        )
        return None

    try:
        conn  = _Redis.from_url(redis_url, decode_responses=False)
        # Two-layer timeout strategy:
        #   1. Queue(default_timeout=...) — queue-level fallback ceiling.
        #      Protects against any future enqueue call that omits job_timeout.
        #      RQ's own default is 180s — this overrides it for the whole queue.
        #   2. enqueue(job_timeout=...) — per-job explicit value (always wins).
        #      Ensures THIS job gets the correct limit regardless of queue default.
        queue  = _RQQueue(
            name=_ATS_SCORING_QUEUE,
            connection=conn,
            default_timeout=settings.ATS_JOB_TIMEOUT_SECONDS,
        )
        rq_job = queue.enqueue(
            "app.workers.ats_tasks.calculate_ats_task",
            args=(job_id, recruiter_id, force),
            job_timeout=settings.ATS_JOB_TIMEOUT_SECONDS,
        )
        logger.info(
            "event=ats_scoring.enqueued job_id=%s recruiter_id=%s "
            "rq_job_id=%s force=%s",
            job_id, recruiter_id, rq_job.id, force,
        )
        return rq_job.id
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "event=ats_scoring.enqueue_failed job_id=%s detail=%s "
            "— ATS scoring will not run automatically.",
            job_id, exc,
        )
        return None



async def _trigger_ats(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
    *,
    mode:  str,    # "incremental" | "force"
    force: bool,
) -> dict[str, Any]:
    """
    Shared guard + enqueue logic for both ATS trigger endpoints.

    Guards (in order):
      0. Job must be active (not archived, paused, or closed)   → 409
      1. Job must exist for this recruiter                      → 404
      2. ``jd_analysis.status`` must be completed               → 422
      3. At least one filtered candidate must exist             → 422
      4. Atomic state-machine write (find_one_and_update):
         blocks when ``ats_run.status`` is ``queued`` (already
         enqueued) or ``processing`` with a fresh ``triggered_at``
         (worker is active). Raises 409 on conflict.  Replaces
         the old non-atomic Guard 2 + update_one two-step.

    Args:
        mode:  ``"incremental"`` (calculate-ats) or ``"force"`` (rerun-ats).
        force: Passed to the worker — controls per-candidate skip logic.
    """
    job_id = job_id.upper()

    # ── Fetch job doc ─────────────────────────────────────────────────────────
    try:
        job_doc = await db[_JOBS_COL].find_one(
            {"job_id": job_id, "recruiter_id": recruiter_id},
            {"jd_analysis": 1, "ats_run": 1, "filters": 1, "status": 1, "is_archived": 1, "_id": 0},
        )
    except Exception as exc:
        logger.exception("event=ats_scoring.db_error job_id=%s detail=%s", job_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve job.",
        ) from exc

    if job_doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    # ── Guard 0: Job must be active (not archived, paused, or closed) ─────────
    if job_doc.get("is_archived", False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot run ATS scoring on archived job '{job_id}'.",
        )
    _job_status = job_doc.get("status", "active")
    if _job_status in ("paused", "closed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Job '{job_id}' is currently '{_job_status}'. "
                "ATS scoring is only available for active jobs."
            ),
        )

    # ── Guard 1: JD analysis must be completed ─────────────────────────────────
    jd_analysis = job_doc.get("jd_analysis") or {}
    if jd_analysis.get("status") != "completed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "JD analysis must be completed before running ATS scoring. "
                f"Current jd_analysis.status: "
                f"'{jd_analysis.get('status', 'not_available')}'"
            ),
        )

    # Guard 2 (non-atomic ats_run.status check) has been removed.
    # Its logic is now enforced atomically inside the find_one_and_update
    # filter below, which also blocks the previously unguarded "queued" state.

    # ── Guard 3: Must have at least one filtered candidate ────────────────────
    filters_raw = job_doc.get("filters") or {}
    candidate_query = build_candidate_filter_query(job_id, recruiter_id, filters_raw)
    try:
        filtered_count = await db[_CANDIDATES_COL].count_documents(candidate_query)
    except Exception as exc:
        logger.exception(
            "event=ats_scoring.count_error job_id=%s detail=%s", job_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to count filtered candidates.",
        ) from exc

    if filtered_count == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "No filtered candidates found for this job. "
                "Candidates must pass all screening criteria before ATS can run."
            ),
        )

    # ── Atomic state-machine write: idle/terminal/stale → queued ────────────
    #
    # find_one_and_update atomically combines what was previously a non-atomic
    # three-step (find_one → Python guard → update_one):
    #
    #   OLD (TOCTOU-prone):
    #     find_one()         ← snapshot S₀
    #     if status=="processing": raise 409   ← checks S₀ (may be stale)
    #     update_one($set ats_run)             ← unconditional write
    #
    #   NEW (atomic):
    #     find_one_and_update(filter, $set)    ← single MongoDB operation
    #
    # The filter encodes every allowed transition:
    #
    #   ALLOWED (filter matches → write proceeds):
    #     • ats_run absent          — first ever trigger
    #     • status = completed      — re-trigger for new candidates
    #     • status = partially_failed — retry after partial failure
    #     • status = failed         — retry after catastrophic failure
    #     • status = processing AND triggered_at < stale_cutoff
    #                               — crash recovery (worker confirmed dead)
    #
    #   BLOCKED (filter does not match → returns None → 409):
    #     • status = queued         — already enqueued, worker not yet claimed
    #                                 (gap that the old Guard 2 missed entirely)
    #     • status = processing AND triggered_at >= stale_cutoff
    #                               — worker is actively running
    #
    # Because the check and write are one atomic MongoDB document-level
    # operation, two concurrent requests cannot both pass: the second
    # request sees status="queued" (written by the first) and gets None.
    now          = datetime.now(tz=timezone.utc)
    stale_cutoff = now - timedelta(minutes=settings.ATS_STALE_PROCESSING_MINUTES)

    ats_run_doc: dict[str, Any] = {
        "status":                      "queued",
        "mode":                        mode,
        "triggered_at":                now,
        "completed_at":                None,
        "triggered_by":                recruiter_id,
        "rq_job_id":                   None,         # back-filled after enqueue
        "total_candidates":            0,            # set by worker at task start
        "processed_candidates":        0,
        "failed_candidates":           0,
        "skipped_existing_candidates": 0,            # valid score + JD ver matches
        "skipped_resume_missing":      0,            # no extracted resume
        "error":                       None,
    }

    try:
        claimed = await db[_JOBS_COL].find_one_and_update(
            {
                "job_id":       job_id,
                "recruiter_id": recruiter_id,
                "$or": [
                    # First ever trigger — no ats_run sub-document yet
                    {"ats_run": {"$exists": False}},
                    # Terminal states — re-trigger always allowed
                    {"ats_run.status": {"$in": [
                        "completed", "partially_failed", "failed",
                    ]}},
                    # Stale processing — worker crashed, safe to reclaim
                    {
                        "ats_run.status":       "processing",
                        "ats_run.triggered_at": {"$lt": stale_cutoff},
                    },
                ],
            },
            {"$set": {"ats_run": ats_run_doc, "updated_at": now}},
            return_document=False,  # pre-update doc not needed
        )
    except Exception as exc:
        logger.exception(
            "event=ats_scoring.write_queued_failed job_id=%s detail=%s", job_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update job ATS run state.",
        ) from exc

    if claimed is None:
        # The filter did not match — the job is currently in a non-transitionable
        # state: either "queued" (already enqueued, worker not yet claimed) or
        # "processing" with a fresh triggered_at (worker is actively running).
        # Raising 409 here is race-free: the check and write were atomic.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "ATS calculation is already in progress for this job. "
                "Please wait for it to complete or retry after "
                f"{settings.ATS_STALE_PROCESSING_MINUTES} minutes."
            ),
        )

    # ── Enqueue onto ats-scoring queue ────────────────────────────────────────
    rq_job_id = _enqueue_ats_calculation(job_id, recruiter_id, force)

    if rq_job_id:
        try:
            await db[_JOBS_COL].update_one(
                {"job_id": job_id, "recruiter_id": recruiter_id},
                {"$set": {"ats_run.rq_job_id": rq_job_id}},
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "event=ats_scoring.rq_job_id_backfill_failed job_id=%s", job_id
            )

    logger.info(
        "event=ats_scoring.triggered job_id=%s recruiter_id=%s "
        "mode=%s filtered_count=%d rq_job_id=%s",
        job_id, recruiter_id, mode, filtered_count, rq_job_id,
    )

    return {
        "job_id":    job_id,
        "status":    "queued",
        "mode":      mode,
        "message":   (
            f"ATS calculation enqueued for {filtered_count} filtered "
            f"candidate(s). Mode: {mode}."
        ),
        "rq_job_id": rq_job_id,
    }


async def trigger_ats_calculation(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
) -> dict[str, Any]:
    """
    Trigger incremental ATS scoring  (``POST /jobs/{job_id}/calculate-ats``).

    Skips candidates that already have a valid score for the current JD version.
    Only scores: new candidates, failed/skipped/processing candidates, and
    candidates whose stored score is from an older JD version.
    """
    return await _trigger_ats(
        db, job_id, recruiter_id, mode="incremental", force=False
    )


async def trigger_rerun_ats(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
) -> dict[str, Any]:
    """
    Force full ATS re-run  (``POST /jobs/{job_id}/rerun-ats``).

    Scores ALL filtered candidates unconditionally — ignores existing scores,
    JD version, and previous status.  Use when the ATS prompt or scoring
    logic has changed, or the recruiter wants completely fresh results.
    """
    return await _trigger_ats(
        db, job_id, recruiter_id, mode="force", force=True
    )



async def get_ats_status(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
) -> dict[str, Any]:
    """
    Return the current ``ats_run`` state for a job.

    Used by the frontend to poll ATS progress.
    Field names are returned in camelCase to match the existing API convention.

    Raises:
        HTTPException 404: Job not found.
        HTTPException 500: DB read failure.
    """
    job_id = job_id.upper()

    try:
        job_doc = await db[_JOBS_COL].find_one(
            {"job_id": job_id, "recruiter_id": recruiter_id},
            {"ats_run": 1, "_id": 0},
        )
    except Exception as exc:
        logger.exception(
            "event=ats_status.db_error job_id=%s detail=%s", job_id, exc
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve ATS status.",
        ) from exc

    if job_doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job '{job_id}' not found.",
        )

    ats_run = job_doc.get("ats_run") or {}

    return {
        "jobId": job_id,
        "atsRun": {
            "status":                    ats_run.get("status",                        "idle"),
            "mode":                      ats_run.get("mode",                         "incremental"),
            "triggeredAt":               ats_run.get("triggered_at"),
            "completedAt":               ats_run.get("completed_at"),
            "triggeredBy":               ats_run.get("triggered_by"),
            "rqJobId":                   ats_run.get("rq_job_id"),
            "totalCandidates":           ats_run.get("total_candidates",             0),
            "processedCandidates":       ats_run.get("processed_candidates",         0),
            "failedCandidates":          ats_run.get("failed_candidates",            0),
            "skippedExistingCandidates": ats_run.get("skipped_existing_candidates",  0),
            "skippedResumeMissing":      ats_run.get("skipped_resume_missing",       0),
            "error":                     ats_run.get("error"),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Candidate queries
# ══════════════════════════════════════════════════════════════════════════════

async def get_candidates_for_job(
    db:           AsyncIOMotorDatabase,
    job_id:       str,
    recruiter_id: str,
    *,
    view:  str = "filtered",   # "filtered" | "all" | "blacklisted"
    page:  int = 1,
    limit: int = 20,
    sort:  str = _DEFAULT_SORT,
) -> CandidateListResponse:
    """
    Return a paginated list of candidates for a job.

    view="filtered"    → applies stored filter thresholds from the job document.
    view="all"         → returns every active candidate linked to this job_id.
    view="blacklisted" → returns only candidates with ``blacklist.is_blacklisted=True``.

    New candidates arriving after job creation are automatically included
    because filtering is query-time — no backfill or mapping table needed.

    Raises:
        HTTPException(404): Job not found.
        HTTPException(400): Invalid view or sort parameter.
        HTTPException(500): Database read failure.
    """
    if view not in ("filtered", "all", "blacklisted"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="view must be 'filtered', 'all', or 'blacklisted'.",
        )

    sort_spec = _SORT_MAP.get(sort, _SORT_MAP[_DEFAULT_SORT])
    job_id_upper = job_id.upper()

    # Fetch job to get filter thresholds and validate existence
    job_doc = await db[_JOBS_COL].find_one(
        {"job_id": job_id_upper, "recruiter_id": recruiter_id},
        {"filters": 1, "_id": 0},
    )
    if job_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job '{job_id}' not found.")

    filters      = JobFilters(**job_doc["filters"])
    base_q       = _base_query(job_id_upper, recruiter_id)
    blacklisted_q = _blacklisted_query(job_id_upper, recruiter_id)

    if view == "filtered":
        candidate_q = _filtered_query(job_id_upper, recruiter_id, filters)
    elif view == "blacklisted":
        candidate_q = blacklisted_q
    else:
        candidate_q = base_q

    skip = (page - 1) * limit

    try:
        total_count      = await db[_CANDIDATES_COL].count_documents(base_q)
        filtered_count   = await db[_CANDIDATES_COL].count_documents(
            _filtered_query(job_id_upper, recruiter_id, filters)
        )
        blacklisted_count = await db[_CANDIDATES_COL].count_documents(blacklisted_q)
        cursor = (
            db[_CANDIDATES_COL]
            .find(candidate_q, _LIST_PROJECTION)
            .sort(sort_spec)
            .skip(skip)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
    except Exception as exc:
        logger.exception(
            "DB error fetching candidates for job_id=%s view=%s: %s",
            job_id, view, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve candidates.",
        ) from exc

    candidates_raw = docs

    # ── Merge ATS scores from candidate_job_scores ────────────────────────────
    # One $in query for the current page — no N+1 pattern.
    # Scores are absent until ATS has been run; None is the correct default.
    candidate_ids = [d.get("candidate_id", "") for d in candidates_raw]
    ats_lookup: dict[str, dict] = {}

    if candidate_ids:
        try:
            score_cursor = db["candidate_job_scores"].find(
                {
                    "job_id":       job_id.upper(),
                    "candidate_id": {"$in": candidate_ids},
                },
                {
                    "candidate_id": 1,
                    "score":        1,
                    "status":       1,
                    "_id":          0,
                },
            )
            score_docs = await score_cursor.to_list(length=len(candidate_ids))
            ats_lookup = {
                s["candidate_id"]: s
                for s in score_docs
                if s.get("candidate_id")
            }
        except Exception as exc:
            # ATS scores are supplementary — log and continue without them
            logger.warning(
                "event=candidate_list.ats_merge_failed job_id=%s detail=%s "
                "— returning candidates without ATS scores.",
                job_id, exc,
            )

    # ── Build response models ─────────────────────────────────────────────────
    candidates = [
        CandidateSummary.from_mongo_doc(
            d,
            ats_score=  ats_lookup.get(d.get("candidate_id", ""), {}).get("score"),
            ats_status= ats_lookup.get(d.get("candidate_id", ""), {}).get("status"),
        )
        for d in candidates_raw
    ]

    list_total = blacklisted_count if view == "blacklisted" else total_count

    return CandidateListResponse(
        job_id=    job_id_upper,
        view=      view,
        total=     list_total,
        filtered=  filtered_count,
        page=      page,
        limit=     limit,
        candidates=candidates,
    )



async def get_candidate_by_id(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    recruiter_id: str,
) -> dict[str, Any]:
    """
    Fetch a full candidate document by candidate_id, scoped to the recruiter.

    Returns a controlled projection: raw email body and clean_text are excluded
    (too large for API responses; available via storage if needed).

    **Intentional bypass of ``build_active_candidate_query()``**: this function
    uses the raw ownership filter ``{candidate_id, recruiter_id}`` without the
    blacklist exclusion.  Detail lookups (GET /candidates/{id}, resume URL,
    ATS score) return the full profile even for blacklisted candidates so the
    recruiter can review the reason and decide whether to restore them.
    The ``blacklist`` sub-object is always present in the response so the
    frontend can render a 'BLACKLISTED' badge.

    Raises:
        HTTPException(404): Candidate not found or belongs to a different recruiter.
        HTTPException(500): Database read failure.
    """
    try:
        doc = await db[_CANDIDATES_COL].find_one(
            {"candidate_id": candidate_id, "recruiter_id": recruiter_id},
            _DETAIL_PROJECTION,
        )
    except Exception as exc:
        logger.exception("DB error fetching candidate_id=%s: %s", candidate_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve candidate.",
        ) from exc

    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Candidate '{candidate_id}' not found.")

    return doc
