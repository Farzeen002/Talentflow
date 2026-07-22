"""
app/api/jobs.py

FastAPI router for Job management and candidate list endpoints.

Routes (all require JWT authentication):
  POST   /jobs                      — Create a job
  GET    /jobs                      — List recruiter's jobs (paginated, archived excluded by default)
  GET    /jobs/importable           — Discover job codes from candidates not yet created as jobs
  GET    /jobs/{job_id}             — Get single job detail
  GET    /jobs/{job_id}/candidates  — Get candidates for a job (filtered or all)
  PATCH  /jobs/{job_id}             — Update job fields (safe + JD-affecting; see endpoint docs)

job_id in all URL paths is the Naukri job code (e.g. "DBA002").
Recruiter isolation is enforced via JWT on every route.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.mongo import get_db
from app.dependencies import get_current_user
from app.models.job import (
    CandidateListResponse,
    ImportableJob,
    JobCreate,
    JobDeleteResponse,
    JobResponse,
    JobUpdate,
)
from app.services.job_service import (
    create_job,
    delete_job,
    get_candidates_for_job,
    get_jobs_for_recruiter,
    get_job_by_id,
    list_importable_jobs,
    trigger_ats_calculation,
    trigger_rerun_ats,
    get_ats_status,
    update_job,
)

router = APIRouter(prefix="/jobs", tags=["Jobs"])

# ── Shared dependency aliases ─────────────────────────────────────────────────
_DB   = Annotated[AsyncIOMotorDatabase, Depends(get_db)]
_User = Annotated[dict, Depends(get_current_user)]


# ══════════════════════════════════════════════════════════════════════════════
# Create Job
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "",
    response_model=JobResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new job posting",
)
async def create_job_endpoint(
    payload:      JobCreate,
    db:           _DB,
    current_user: _User,
) -> JobResponse:
    """
    Create a job posting.  Candidates already stored in MongoDB with a
    matching ``job_id`` are automatically grouped under this job on
    the next candidate query — no backfill required.

    The ``maxNoticePeriodDays`` value sets the dynamic filter threshold
    stored on the job document.  The three fixed screening criteria
    (is_ok_client, is_c2h_ok, has_pf_account) are always enforced by the backend.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await create_job(db, payload, recruiter_id)


# ══════════════════════════════════════════════════════════════════════════════
# List Jobs
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "",
    response_model=dict,
    response_model_by_alias=True,
    summary="List all jobs for the authenticated recruiter",
)
async def list_jobs_endpoint(
    db:           _DB,
    current_user: _User,
    status_filter:    Optional[str] = Query(None,  alias="status",
                                            description="Filter by job status: active | paused | closed"),
    page:             int           = Query(1,      ge=1),
    limit:            int           = Query(20,     ge=1, le=100),
    include_archived: bool          = Query(False,  alias="includeArchived",
                                            description="Include archived jobs in results. Default false."),
) -> dict[str, Any]:
    """
    Returns a paginated list of jobs for the recruiter.

    By default archived jobs are excluded.  Pass ``includeArchived=true``
    to include them (useful for audit or recovering a wrong job code).

    Each job includes ``counts.total`` (all candidates) and
    ``counts.filtered`` (candidates passing filter thresholds).
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await get_jobs_for_recruiter(
        db, recruiter_id,
        status_filter=status_filter,
        page=page, limit=limit,
        include_archived=include_archived,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Importable Jobs (Create Job discovery)
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/importable",
    response_model=list[ImportableJob],
    response_model_by_alias=True,
    summary="List importable jobs discovered from candidate emails",
    description=(
        "Returns Naukri job codes found on this recruiter's ingested candidates "
        "that have not yet been created in the jobs collection.\n\n"
        "Used by the Create Job page to auto-fill Job ID and Job Title. "
        "Does **not** create a job — call ``POST /jobs`` with the selected "
        "values plus remaining fields (JD, notice period, etc.).\n\n"
        "``candidateCount`` is a live informational snapshot for the dropdown "
        "only; it is not persisted and must not drive business logic.\n\n"
        "Scoped to the authenticated recruiter. Excludes job codes that already "
        "exist in jobs (including archived). Blacklist state is ignored — this "
        "is job discovery, not candidate management."
    ),
    responses={
        200: {
            "description": "Importable jobs for the Create Job dropdown.",
            "content": {
                "application/json": {
                    "example": [
                        {
                            "jobId": "DBA003",
                            "jobTitle": "Qa Test Engineer",
                            "candidateCount": 59,
                        },
                        {
                            "jobId": "DBA004",
                            "jobTitle": "Java Developer",
                            "candidateCount": 21,
                        },
                    ]
                }
            },
        },
    },
)
async def list_importable_jobs_endpoint(
    db:           _DB,
    current_user: _User,
) -> list[ImportableJob]:
    """
    Discover importable job codes for the authenticated recruiter.

    Must be registered before ``GET /{job_id}`` so ``importable`` is not
    treated as a Naukri job code path parameter.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await list_importable_jobs(db, recruiter_id)


# ══════════════════════════════════════════════════════════════════════════════
# Get Single Job
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{job_id}",
    response_model=JobResponse,
    response_model_by_alias=True,
    summary="Get a single job by its Naukri job code",
)
async def get_job_endpoint(
    job_id:       str,
    db:           _DB,
    current_user: _User,
) -> JobResponse:
    """
    Fetch full job detail including candidate counts.
    ``job_id`` is the Naukri job code, e.g. ``DBA002``.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await get_job_by_id(db, job_id, recruiter_id)


# ══════════════════════════════════════════════════════════════════════════════
# Job Candidates
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{job_id}/candidates",
    response_model=CandidateListResponse,
    response_model_by_alias=True,
    summary="Get candidates grouped under a job",
)
async def get_job_candidates_endpoint(
    job_id:       str,
    db:           _DB,
    current_user: _User,
    view:  str = Query(
        "filtered",
        description=(
            "'filtered' applies screening criteria; "
            "'all' returns every active candidate for this job; "
            "'blacklisted' returns only blacklisted candidates."
        ),
    ),
    page:  int = Query(1,   ge=1),
    limit: int = Query(20,  ge=1, le=100),
    sort:  str = Query("created_at_desc",
                       description="Sort order: created_at_desc | created_at_asc | name_asc | notice_period_asc | ctc_desc"),
) -> CandidateListResponse:
    """
    Return candidates grouped under this job.

    - ``view=filtered`` (default): only active candidates passing all 4 screening criteria.
    - ``view=all``: every active candidate linked to this ``job_id``.
    - ``view=blacklisted``: only candidates with ``blacklist.isBlacklisted=true``.

    Newly arrived candidates are automatically included — no manual refresh needed.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await get_candidates_for_job(
        db, job_id, recruiter_id,
        view=view, page=page, limit=limit, sort=sort,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Update Job
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/{job_id}",
    response_model=JobResponse,
    response_model_by_alias=True,
    summary="Update recruiter-editable fields on a job",
)
async def update_job_endpoint(
    job_id:       str,
    update:       JobUpdate,
    db:           _DB,
    current_user: _User,
) -> JobResponse:
    """
    Partial update for a job posting.  All fields are optional — supply
    only what needs changing.

    **Safe fields** (take effect immediately, no side effects):
    - ``status``: transition between ``active``, ``paused``, ``closed``.
    - ``location``, ``priority``, ``experience``: display metadata.
    - ``maxNoticePeriodDays``: notice period filter threshold.
    - ``isArchived``: set ``true`` to hide a job (e.g. wrong job code).
      Set ``false`` to restore it.  Archived jobs are excluded from the
      default job list and cannot trigger ATS scoring.

    **JD-affecting fields** (trigger JD re-analysis + ATS invalidation):
    - ``title``: changing triggers JD re-analysis.
    - ``description``: changing triggers JD re-analysis.
      Pass empty string to clear (sets ``jd_analysis.status`` to
      ``not_available`` — no re-analysis enqueued).
    - ``employmentType``: changing triggers JD re-analysis.

    When a JD-affecting field changes:
    - ``jd_analysis`` is reset to ``pending`` and JD analysis is
      automatically re-enqueued (same behaviour as job creation).
    - ``jd_analysis.version`` is NOT reset — existing ATS scores become
      version-stale and are re-scored on the next ``calculate-ats`` call.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await update_job(db, job_id, recruiter_id, update)


# ══════════════════════════════════════════════════════════════════════════════
# Delete Job
# ══════════════════════════════════════════════════════════════════════════════

@router.delete(
    "/{job_id}",
    response_model=JobDeleteResponse,
    response_model_by_alias=True,
    summary="Hard delete an empty job posting",
)
async def delete_job_endpoint(
    job_id:       str,
    db:           _DB,
    current_user: _User,
) -> JobDeleteResponse:
    """
    Permanently delete a job posting.

    **Allowed only if no candidates are linked to this job.**

    This endpoint is designed for the specific scenario where a recruiter
    creates a job with the wrong job code *before any candidates arrive*.
    Once candidates exist, the job cannot be deleted — use
    ``PATCH /jobs/{job_id}`` with ``isArchived: true`` to hide it instead.

    **Blocked if:**
    - Candidates are linked to this job → ``409 Conflict``

    **Does not cascade-delete anything:**
    - Candidates are never touched (none exist by definition)
    - ATS scores cannot exist without candidates (system invariant)
    - ``jd_analysis`` and ``ats_run`` are embedded sub-documents — they
      are removed automatically with the job document.

    Raises:
        HTTPException(404): Job not found or belongs to another recruiter.
        HTTPException(409): Candidates exist — archive instead.
        HTTPException(500): DB failure.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await delete_job(db, job_id, recruiter_id)


# ══════════════════════════════════════════════════════════════════════════════
# ATS Scoring
# ══════════════════════════════════════════════════════════════════════════════

@router.post(
    "/{job_id}/calculate-ats",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger ATS score calculation for filtered candidates",
)
async def calculate_ats_endpoint(
    job_id:       str,
    db:           _DB,
    current_user: _User,
) -> dict:
    """
    Manually trigger incremental ATS score calculation for filtered candidates.

    **Incremental behaviour** (default):
    - Candidates with a valid existing score for the current JD version are **skipped**.
    - Only these candidates are (re-)scored:
      * New candidates (no score record exists)
      * Previously ``failed`` or ``skipped`` candidates (retried)
      * Candidates whose score was computed against an older JD version
      * Candidates stuck in ``processing`` state (crash residue)

    **Pre-conditions**:
    - JD analysis must be completed (``jd_analysis.status == "completed"``)
    - At least one candidate must pass the job's filter criteria
    - No ATS run must currently be in progress (unless it is stale)

    **Behaviour**:
    - The response returns immediately (202) — processing runs in the background.
    - Poll ``GET /jobs/{job_id}/ats-status`` to track progress.
    - To force a full re-run ignoring all existing scores, use
      ``POST /jobs/{job_id}/rerun-ats`` instead.

    **Queue**: ``ats-scoring`` (dedicated RQ worker).
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await trigger_ats_calculation(db, job_id, recruiter_id)


@router.get(
    "/{job_id}/ats-status",
    response_model=dict,
    summary="Get ATS scoring progress for a job",
)
async def get_ats_status_endpoint(
    job_id:       str,
    db:           _DB,
    current_user: _User,
) -> dict:
    """
    Return the current ATS run state for this job.

    Intended for frontend polling while ATS scoring is in progress.
    Recommended poll interval: every 3–5 seconds while
    ``atsRun.status == "processing"``.

    **Response fields**:
    - ``status``: idle | queued | processing | completed | partially_failed | failed
    - ``mode``: incremental | force
    - ``totalCandidates``: total filtered candidates in this run
    - ``processedCandidates``: successfully scored so far
    - ``failedCandidates``: candidates that errored (LLM / storage)
    - ``skippedExistingCandidates``: valid score already existed — skipped
    - ``skippedResumeMissing``: no extracted resume — skipped
    - ``triggeredAt`` / ``completedAt``: run timestamps

    Raises:
        HTTPException(404): Job not found.
        HTTPException(500): DB read failure.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await get_ats_status(db, job_id, recruiter_id)


@router.post(
    "/{job_id}/rerun-ats",
    response_model=dict,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Force full ATS re-run for all filtered candidates",
)
async def rerun_ats_endpoint(
    job_id:       str,
    db:           _DB,
    current_user: _User,
) -> dict:
    """
    Force a complete ATS re-run — scores **all** filtered candidates
    unconditionally, ignoring any existing scores or JD version.

    Use this when:
    - The ATS prompt or scoring logic has been updated (code deploy)
    - The recruiter wants completely fresh scores regardless of history

    This is different from ``POST /jobs/{job_id}/calculate-ats`` which
    skips candidates that already have a valid, up-to-date score.

    **Pre-conditions** (same as calculate-ats):
    - JD analysis must be completed
    - At least one filtered candidate must exist
    - No ATS run currently in progress (unless stale)

    **Queue**: ``ats-scoring`` (dedicated RQ worker).

    Raises:
        HTTPException(404): Job not found.
        HTTPException(409): ATS calculation already in progress.
        HTTPException(422): JD not analysed, or no filtered candidates.
        HTTPException(500): DB or queue failure.
    """
    recruiter_id: str = current_user["recruiter_id"]
    return await trigger_rerun_ats(db, job_id, recruiter_id)
