"""
app/api/candidates.py

FastAPI router for individual candidate detail access and lifecycle management.

Routes (all require JWT authentication):
  GET   /candidates/{candidate_id}/ats-score?jobId=DBA002  — ATS score for a candidate in a job context
  GET   /candidates/{candidate_id}                         — Full candidate detail (returns profile even if blacklisted)
  GET   /candidates/{candidate_id}/resume?action=preview   — Short-lived signed URL for inline PDF preview
  GET   /candidates/{candidate_id}/resume?action=download  — Short-lived signed URL for file download
  PATCH /candidates/{candidate_id}/blacklist               — Soft-blacklist a candidate (fake/invalid)
  PATCH /candidates/{candidate_id}/unblacklist             — Restore a blacklisted candidate

Route ordering
--------------
Sub-path routes (``/ats-score``, ``/blacklist``, ``/unblacklist``) MUST be
defined BEFORE the ``/{candidate_id}`` catch-all route. FastAPI resolves routes
in declaration order — if the catch-all were first, the literal path segments
would be captured as ``candidate_id`` values, returning 404 instead of routing
to the correct endpoint.

Blacklist semantics
-------------------
* Blacklisting is a **soft** operation — no data is deleted.
* Blacklisted candidates are excluded from all list/count queries and from the
  ATS LLM pipeline (no wasted tokens).
* ``GET /candidates/{candidate_id}`` still returns the full profile for blacklisted
  candidates so the recruiter can review the reason.  The response includes a
  ``blacklist`` sub-object with ``isBlacklisted: true``.
* ``PATCH /unblacklist`` reverses the blacklist without erasing audit history.

Candidate lists are accessible via GET /jobs/{job_id}/candidates.
This router provides the single-candidate drill-down view.

Response contracts
------------------
``CandidateDetailResponse``: uses ``response_model_by_alias=True`` so that
  ``email.sender`` serialises as ``"from"`` in JSON output.
``AtsScoreResponse``: flat camelCase shape; no alias override required.

Service layer returns raw MongoDB dicts. Response model factories handle all
projection, camelCase conversion, and field exclusion at the API boundary.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from app.db.mongo import get_db
from app.dependencies import get_current_user
from app.models.candidate_response import (
    AtsScoreBreakdownResponse,
    AtsScoreResponse,
    BlacklistRequest,
    CandidateBlacklistResponse,
    CandidateDetailResponse,
)
from app.models.resume_access import ResumeUrlResponse
from app.services.candidate_service import (
    blacklist_candidate,
    get_candidate_ats_score,
    unblacklist_candidate,
)
from app.services.job_service import get_candidate_by_id
from app.services.resume_service import generate_resume_url

router = APIRouter(prefix="/candidates", tags=["Candidates"])

_DB   = Annotated[Any, Depends(get_db)]
_User = Annotated[dict, Depends(get_current_user)]


# ══════════════════════════════════════════════════════════════════════════════
# ATS score for a candidate in a job context
# ══════════════════════════════════════════════════════════════════════════════
# NOTE: This route is intentionally placed BEFORE /{candidate_id} to prevent
# FastAPI from capturing the literal string "ats-score" as a candidate_id value.

@router.get(
    "/{candidate_id}/ats-score",
    response_model=AtsScoreResponse,
    summary="Get ATS score for a candidate in a job context",
)
async def get_candidate_ats_score_endpoint(
    candidate_id: str,
    db:           _DB,
    current_user: _User,
    job_id: str = Query(
        ...,
        alias="jobId",
        description=(
            "Naukri job code for the ATS context (e.g. 'DBA002'). "
            "Required — ATS scores are always job-specific. "
            "The recruiter must supply the job context explicitly; "
            "no fallback to candidate.job_id is performed."
        ),
    ),
) -> AtsScoreResponse:
    """
    Return the ATS scoring state for a candidate within a specific job context.

    This endpoint is a **pure read** — it performs no LLM computation, triggers
    no background workers, and has no side effects.  It reads the
    ``candidate_job_scores`` collection (written by the ATS batch worker) and
    returns the current scoring state.

    **jobId is mandatory**.  ATS scores are always computed against a specific
    Job Description — a score without job context is meaningless.  There is no
    fallback to ``candidate.job_id``; the frontend must always supply the job
    code from its URL context.

    **Response status values**:

    * ``completed``  — Score available. ``score`` and ``scoreBreakdown`` are
                       populated. ``isStale`` is ``True`` if the JD was
                       re-analysed after scoring (score based on older JD).
    * ``processing`` — ATS batch is currently scoring this candidate. Poll
                       every 5 seconds. Hard cap: stop after 20 polls and
                       direct the recruiter to the job ATS status page.
    * ``failed``     — LLM or scoring error for this candidate specifically.
                       No score. See job-level ATS status for details.
    * ``skipped``    — Candidate had no processable resume. No score. Retrying
                       ATS will not produce a score until the resume pipeline
                       completes for this candidate.
    * ``not_scored`` — No score record exists yet. The ATS batch has not been
                       triggered for this job, or this candidate arrived after
                       the last batch run. Use the [Run ATS] action.

    **Ownership enforcement** (two independent checks):

    1. ``get_candidate_by_id()`` verifies the candidate belongs to the
       authenticated recruiter before any score lookup is attempted. A recruiter
       who guesses another's ``candidate_id`` receives ``404``.
    2. ``get_candidate_ats_score()`` also filters ``candidate_job_scores`` by
       ``recruiter_id``. Defence in depth — score data cannot leak even if
       check 1 were bypassed.

    Raises:
        HTTPException(401): Missing or invalid JWT.
        HTTPException(403): OAuth access revoked.
        HTTPException(404): Candidate not found or belongs to a different recruiter.
        HTTPException(422): ``jobId`` query param missing or blank.
        HTTPException(500): Database read failure.
    """
    recruiter_id: str = current_user["recruiter_id"]

    # Normalise job_id — all job codes are uppercased throughout the system
    job_id = job_id.strip().upper()

    # ── Stage 1: Validate candidate ownership ────────────────────────────────
    # get_candidate_by_id raises 404 if not found or wrong recruiter.
    # We discard the candidate doc — we only need the ownership check here.
    # The ATS score service function performs its own recruiter_id filter
    # on candidate_job_scores as a second independent authorization layer.
    await get_candidate_by_id(db, candidate_id, recruiter_id)

    # ── Stage 2: Fetch ATS score + staleness ─────────────────────────────────
    result: dict[str, Any] = await get_candidate_ats_score(
        db=           db,
        candidate_id= candidate_id,
        job_id=       job_id,
        recruiter_id= recruiter_id,
    )

    # ── Stage 3: Build response ───────────────────────────────────────────────
    # Map score_breakdown (raw dict with snake_case keys) → AtsScoreBreakdownResponse.
    # Returns None when status is not "completed" or breakdown is absent.
    raw_breakdown: dict[str, Any] | None = result.get("score_breakdown")
    breakdown_response: AtsScoreBreakdownResponse | None = (
        AtsScoreBreakdownResponse.from_score_doc(raw_breakdown)
        if raw_breakdown is not None
        else None
    )

    return AtsScoreResponse(
        candidateId=       candidate_id,
        jobId=             result["job_id"],
        score=             result["score"],
        status=            result["status"],
        scoredAt=          result["scored_at"],
        jdAnalysisVersion= result["jd_analysis_version"],
        isStale=           result["is_stale"],
        scoreBreakdown=    breakdown_response,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Candidate detail
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{candidate_id}",
    response_model=CandidateDetailResponse,
    response_model_by_alias=True,
    summary="Get full candidate detail",
)
async def get_candidate_endpoint(
    candidate_id: str,
    db:           _DB,
    current_user: _User,
) -> CandidateDetailResponse:
    """
    Return the frontend-safe candidate profile for a given ``candidate_id``.

    **Recruiter isolation**: the query is scoped by ``recruiter_id`` from the
    JWT — a recruiter guessing a UUID receives ``404``, not another
    recruiter's data.

    **Excluded fields** (handled by the response model, not the DB query):
    - ``recruiter_id``                     — backend isolation key
    - ``raw_email``                        — temporary debug storage, PII-heavy
    - ``email.message_id``                 — internal Gmail API identifier
    - ``resume.original.blob_path``        — internal storage key
    - ``resume.extracted.text_blob_path``  — internal storage key

    **camelCase conversion**: all field names in ``metadata``, ``skills``,
    and ``qa`` are recursively converted from ``snake_case`` to ``camelCase``
    at the response layer — the MongoDB document is unchanged.

    Raises:
        HTTPException(401): Missing or invalid JWT.
        HTTPException(404): Candidate not found or belongs to a different recruiter.
        HTTPException(500): Database read failure.
    """
    recruiter_id: str = current_user["recruiter_id"]
    raw_doc: dict[str, Any] = await get_candidate_by_id(db, candidate_id, recruiter_id)
    return CandidateDetailResponse.from_mongo_doc(raw_doc)


# ══════════════════════════════════════════════════════════════════════════════
# Resume signed URL
# ══════════════════════════════════════════════════════════════════════════════

@router.get(
    "/{candidate_id}/resume",
    response_model=ResumeUrlResponse,
    summary="Get a signed URL to preview or download a candidate's resume",
)
async def get_resume_url_endpoint(
    candidate_id: str,
    db:           _DB,
    current_user: _User,
    action: Literal["preview", "download"] = Query(
        ...,
        description=(
            "``preview``  — returns a signed URL with Content-Disposition: inline. "
            "PDF files open in the browser. DOCX/DOC files are automatically "
            "downgraded to download (browsers cannot render them). "
            "``download`` — returns a signed URL with Content-Disposition: attachment "
            "for all file types."
        ),
    ),
) -> ResumeUrlResponse:
    """
    Return a short-lived GCS signed URL (15 minutes) for accessing a candidate's resume.

    **Security**:
    - JWT ``recruiter_id`` must match the candidate's owning recruiter.
    - A recruiter guessing another candidate's UUID receives ``404``.
    - The GCS bucket remains fully private — no public access is granted.
    - ``Content-Disposition`` and ``Content-Type`` are cryptographically signed
      into the URL and cannot be tampered with by the client.

    **URL behaviour**:
    - URL expires after 15 minutes.
    - Frontend should NOT cache the URL — request a fresh one on every button click.
    - The API server is never in the file-serving data path; the browser fetches
      the file directly from GCS.

    **DOCX/DOC preview downgrade**:
    - If ``action=preview`` is requested for a DOCX or DOC file, the backend
      automatically returns ``action: "download"`` in the response with a
      human-readable ``note``. Frontend should display a toast message.

    Raises:
        HTTPException(401): Missing or invalid JWT.
        HTTPException(403): OAuth access revoked.
        HTTPException(404): Candidate not found, belongs to a different recruiter,
                            or resume has not been uploaded yet.
        HTTPException(422): ``action`` query param missing or invalid value.
        HTTPException(500): GCS signed URL generation failed.
        HTTPException(501): ``STORAGE_PROVIDER=local`` — signed URLs not supported.
    """
    recruiter_id: str = current_user["recruiter_id"]
    result = await generate_resume_url(
        db=           db,
        candidate_id= candidate_id,
        recruiter_id= recruiter_id,
        action=       action,
    )
    return ResumeUrlResponse(**result)


# ══════════════════════════════════════════════════════════════════════════════
# Blacklist / Unblacklist
# NOTE: Both routes are defined BEFORE /{candidate_id} to prevent FastAPI
# from capturing the literal strings "blacklist" / "unblacklist" as candidate_id.
# ══════════════════════════════════════════════════════════════════════════════

@router.patch(
    "/{candidate_id}/blacklist",
    response_model=CandidateBlacklistResponse,
    summary="Blacklist a candidate identified as fake or invalid",
)
async def blacklist_candidate_endpoint(
    candidate_id: str,
    payload:      BlacklistRequest,
    db:           _DB,
    current_user: _User,
) -> CandidateBlacklistResponse:
    """
    Soft-blacklist a candidate identified as fake or invalid.

    **What changes:**
    - ``blacklist.isBlacklisted`` set to ``true``
    - ``blacklist.reason``, ``blacklist.blacklistedAt``, ``blacklist.source`` recorded
    - Candidate is immediately excluded from all job candidate lists, counts,
      and the ATS LLM scoring pipeline (no wasted tokens)
    - ``updated_at`` stamped

    **What does NOT change:**
    - Candidate document is NOT deleted
    - ATS score records are NOT deleted (historical data preserved)
    - GCS resume blobs are NOT deleted
    - ``GET /candidates/{id}`` still returns the profile with a blacklist badge
      so the recruiter can review the reason

    **Atomicity**: the state check and write are a single MongoDB operation.
    Two simultaneous requests for the same candidate: one succeeds (200),
    the other fails (409) — no intermediate state is possible.

    **Request body:**
    ```json
    { "reason": "Fake resume" }
    ```
    ``reason`` is optional but strongly recommended for audit purposes.

    Raises:
        HTTPException(404): Candidate not found or belongs to another recruiter.
        HTTPException(409): Candidate is already blacklisted.
        HTTPException(500): Database write failure.
    """
    recruiter_id: str = current_user["recruiter_id"]
    result = await blacklist_candidate(
        db=           db,
        candidate_id= candidate_id,
        recruiter_id= recruiter_id,
        reason=       payload.reason,
    )
    return CandidateBlacklistResponse(**result)


@router.patch(
    "/{candidate_id}/unblacklist",
    response_model=CandidateBlacklistResponse,
    summary="Restore a blacklisted candidate to active status",
)
async def unblacklist_candidate_endpoint(
    candidate_id: str,
    db:           _DB,
    current_user: _User,
) -> CandidateBlacklistResponse:
    """
    Reverse a blacklist and restore the candidate to active status.

    **What changes:**
    - ``blacklist.isBlacklisted`` set to ``false``
    - ``blacklist.restoredAt`` and recruiter recorded
    - Candidate immediately reappears in all job candidate lists and counts
    - ``updated_at`` stamped

    **Audit history is preserved:**
    - ``blacklist.reason``, ``blacklist.blacklistedAt``, ``blacklist.blacklistedBy``
      are NEVER modified — the original blacklist record always remains visible
      in ``GET /candidates/{id}`` so the recruiter can see what happened.

    **Atomicity**: same as ``/blacklist`` — atomic MongoDB operation.
    Two simultaneous unblacklist requests: one succeeds (200), the other fails (409).

    Raises:
        HTTPException(404): Candidate not found or belongs to another recruiter.
        HTTPException(409): Candidate is not currently blacklisted.
        HTTPException(500): Database write failure.
    """
    recruiter_id: str = current_user["recruiter_id"]
    result = await unblacklist_candidate(
        db=           db,
        candidate_id= candidate_id,
        recruiter_id= recruiter_id,
    )
    return CandidateBlacklistResponse(**result)
