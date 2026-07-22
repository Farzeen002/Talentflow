"""
app/services/candidate_service.py

Candidate domain service.

Responsibilities
----------------
* Soft-blacklist a candidate (mark as fake/invalid) using an atomic
  conditional update that eliminates TOCTOU race conditions.
* Reverse a blacklist (restore) while preserving the full audit trail.
* Fetch the ATS score record for a specific (candidate, job) pair, with
  server-side staleness detection.

Blacklist design
----------------
* ``blacklist_candidate()``   — atomic find_one_and_update with pre-condition
  ``blacklist.is_blacklisted != True``.  Two concurrent calls:
    Request A → filter matches → write → 200
    Request B → filter no longer matches → None → 409
* ``unblacklist_candidate()`` — atomic find_one_and_update with pre-condition
  ``blacklist.is_blacklisted == True``.  Same race-free guarantee.
* History is **append-only**: unblacklisting adds ``restored_at/restored_by``
  instead of nulling out the original blacklist fields.
* ``processed_emails`` is an immutable ingestion ledger — blacklisting appends
  flags, unblacklisting appends restoration timestamps.  Nothing is ever unset.

Separation of concerns
----------------------
* ``resume_service.py``      → Resume URL generation (signing, disposition).
* ``job_service.py``         → Job-scoped candidate list and detail queries.
* ``candidate_service.py``   → Candidate lifecycle operations that span
                               multiple domains (this file).

Design rules
------------
* NO FastAPI request/response objects — those live in app/api/candidates.py
* All MongoDB I/O is async (Motor)
* Recruiter isolation enforced on every query via recruiter_id from JWT
* Atomic conditional writes for all state transitions (no find_one → update_one)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.candidate_response import ATS_STATUS_NOT_SCORED, ATS_STATUS_COMPLETED

logger = logging.getLogger(__name__)

# ── Collection names ──────────────────────────────────────────────────────────
_CANDIDATES_COL       = "candidates"
_SCORES_COL           = "candidate_job_scores"
_JOBS_COL             = "jobs"
_PROCESSED_EMAILS_COL = "processed_emails"

# ── Minimal projection for ATS score fetch ───────────────────────────────────
# llm_evaluation is intentionally excluded: it is a large raw LLM payload
# (per-requirement evidence strings) with no place in a frontend-facing response.
_SCORE_PROJECTION: dict[str, int] = {
    "status":              1,
    "score":               1,
    "scored_at":           1,
    "jd_analysis_version": 1,
    "score_breakdown":     1,
    "_id":                 0,
}

# ── Minimal projection for JD version staleness check ────────────────────────
_JD_VERSION_PROJECTION: dict[str, int] = {
    "jd_analysis.version": 1,
    "_id":                 0,
}


# ══════════════════════════════════════════════════════════════════════════════
# Private helpers — processed_emails ledger (append-only, best-effort)
# ══════════════════════════════════════════════════════════════════════════════

async def _get_message_id(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    recruiter_id: str,
) -> str | None:
    """
    Fetch the email.message_id for a candidate without loading the full doc.

    Returns ``None`` when the candidate has no linked email (shouldn't happen
    in normal flow, but guards against edge cases).
    """
    doc = await db[_CANDIDATES_COL].find_one(
        {"candidate_id": candidate_id, "recruiter_id": recruiter_id},
        {"email.message_id": 1, "_id": 0},
    )
    if doc is None:
        return None
    return (doc.get("email") or {}).get("message_id") or None


async def _flag_processed_email_blacklisted(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    recruiter_id: str,
    reason:       str | None,
    now:          datetime,
) -> None:
    """
    Append blacklist metadata to the ``processed_emails`` ledger.

    Best-effort — failures are logged as warnings and never raised.
    The processed_emails record keeps status=``processed`` (blocking re-ingestion).
    We only ADD flags; existing fields are never modified or removed.

    Fields added:
      - blacklisted_by_recruiter: True
      - blacklisted_at:           UTC timestamp
      - blacklist_reason:         recruiter-supplied reason (may be None)
    """
    message_id = await _get_message_id(db, candidate_id, recruiter_id)
    if not message_id:
        logger.warning(
            "event=blacklist.email_flag_skipped candidate_id=%s "
            "— message_id was null, processed_emails not updated.",
            candidate_id,
        )
        return

    try:
        await db[_PROCESSED_EMAILS_COL].update_one(
            {"message_id": message_id, "recruiter_id": recruiter_id},
            {"$set": {
                "blacklisted_by_recruiter": True,
                "blacklisted_at":           now,
                "blacklist_reason":         reason,
            }},
        )
        logger.info(
            "event=blacklist.email_flagged candidate_id=%s message_id=%s",
            candidate_id, message_id,
        )
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: the ingestion guard (status=processed) is unaffected.
        logger.warning(
            "event=blacklist.email_flag_failed candidate_id=%s "
            "message_id=%s detail=%s — ingestion guard unaffected.",
            candidate_id, message_id, exc,
        )


async def _flag_processed_email_restored(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    recruiter_id: str,
    restored_by:  str,
    now:          datetime,
) -> None:
    """
    Append restoration metadata to the ``processed_emails`` ledger.

    Best-effort — failures are logged as warnings and never raised.
    Does NOT unset ``blacklisted_by_recruiter``, ``blacklisted_at``, or
    ``blacklist_reason`` — the ledger is immutable; history is preserved.

    Fields added:
      - restored_at: UTC timestamp
      - restored_by: recruiter_id who restored the candidate
    """
    message_id = await _get_message_id(db, candidate_id, recruiter_id)
    if not message_id:
        logger.warning(
            "event=unblacklist.email_flag_skipped candidate_id=%s "
            "— message_id was null, processed_emails not updated.",
            candidate_id,
        )
        return

    try:
        await db[_PROCESSED_EMAILS_COL].update_one(
            {"message_id": message_id, "recruiter_id": recruiter_id},
            {"$set": {
                "restored_at": now,
                "restored_by": restored_by,
            }},
        )
        logger.info(
            "event=unblacklist.email_flagged candidate_id=%s message_id=%s",
            candidate_id, message_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "event=unblacklist.email_flag_failed candidate_id=%s "
            "message_id=%s detail=%s.",
            candidate_id, message_id, exc,
        )


# ══════════════════════════════════════════════════════════════════════════════
# Public API — blacklist lifecycle
# ══════════════════════════════════════════════════════════════════════════════

async def blacklist_candidate(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    recruiter_id: str,
    reason:       str | None,
) -> dict[str, Any]:
    """
    Soft-blacklist a candidate identified as fake or invalid.

    Uses a single atomic ``find_one_and_update`` that combines the state check
    and the write into one MongoDB operation — the same pattern used by
    ``job_service._trigger_ats()`` for the ATS queue state machine.

    Atomic guarantee
    ----------------
    Two simultaneous requests for the same candidate:
      Request A → filter matches (is_blacklisted != True) → write → 200
      Request B → filter no longer matches (now True)     → None  → 409
    No intermediate state is possible; no explicit locking needed.

    What changes in MongoDB
    -----------------------
    ``candidates`` collection:
      - blacklist.is_blacklisted = True
      - blacklist.reason         = reason  (may be None)
      - blacklist.blacklisted_at = now (UTC)
      - blacklist.blacklisted_by = recruiter_id
      - blacklist.source         = "recruiter"
      - updated_at               = now

    ``processed_emails`` collection (best-effort, append-only):
      - blacklisted_by_recruiter = True
      - blacklisted_at           = now
      - blacklist_reason         = reason

    What does NOT change
    --------------------
    - No ATS scores are deleted (historical data preserved).
    - No GCS blobs are deleted.
    - The ``processed_emails`` record stays status=``processed`` (blocking
      re-ingestion) — we only append flags.
    - The candidate document itself is NOT deleted.

    Raises
    ------
    HTTPException(404): Candidate not found or belongs to another recruiter.
    HTTPException(409): Candidate is already blacklisted (idempotency guard).
    HTTPException(500): DB write failure.
    """
    now = datetime.now(tz=timezone.utc)

    # ── Atomic conditional write ──────────────────────────────────────────────
    # Filter: candidate must exist + belong to recruiter + NOT already blacklisted.
    # If the filter doesn't match, claimed=None → fallback count distinguishes 404 vs 409.
    try:
        claimed = await db[_CANDIDATES_COL].find_one_and_update(
            {
                "candidate_id":             candidate_id,
                "recruiter_id":             recruiter_id,
                "blacklist.is_blacklisted": {"$ne": True},
            },
            {"$set": {
                "blacklist.is_blacklisted": True,
                "blacklist.reason":         reason,
                "blacklist.blacklisted_at": now,
                "blacklist.blacklisted_by": recruiter_id,
                "blacklist.source":         "recruiter",
                "updated_at":               now,
            }},
            return_document=False,   # pre-update doc not needed
        )
    except Exception as exc:
        logger.exception(
            "event=blacklist.db_error candidate_id=%s detail=%s",
            candidate_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to blacklist candidate.",
        ) from exc

    if claimed is None:
        # Filter didn't match. Distinguish 404 (not found) vs 409 (already blacklisted).
        # This extra query only runs on the unhappy path — no overhead on the normal flow.
        try:
            exists = await db[_CANDIDATES_COL].count_documents(
                {"candidate_id": candidate_id, "recruiter_id": recruiter_id}
            )
        except Exception as exc:
            logger.exception(
                "event=blacklist.existence_check_error candidate_id=%s detail=%s",
                candidate_id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to verify candidate status.",
            ) from exc

        if exists == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Candidate '{candidate_id}' not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate is already blacklisted.",
        )

    logger.info(
        "event=candidate.blacklisted candidate_id=%s recruiter_id=%s reason=%r",
        candidate_id, recruiter_id, reason,
    )

    # ── Flag processed_emails (best-effort, append-only) ─────────────────────
    await _flag_processed_email_blacklisted(db, candidate_id, recruiter_id, reason, now)

    return {
        "success":       True,
        "candidateId":   candidate_id,
        "isBlacklisted": True,
        "reason":        reason,
        "blacklistedAt": now,
        "message":       "Candidate has been blacklisted successfully.",
    }


async def unblacklist_candidate(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    recruiter_id: str,
) -> dict[str, Any]:
    """
    Restore a blacklisted candidate to active status.

    Uses the same atomic ``find_one_and_update`` pattern as
    ``blacklist_candidate()`` with the inverted pre-condition.

    Audit trail preservation
    ------------------------
    Original blacklist fields (``reason``, ``blacklisted_at``, ``blacklisted_by``)
    are **never modified or nulled**.  Unblacklisting only ADDS:
      - blacklist.is_blacklisted = False
      - blacklist.restored_at    = now (UTC)
      - blacklist.restored_by    = recruiter_id

    After blacklist → restore, the document shows:
      blacklist.is_blacklisted = False
      blacklist.reason         = "Fake resume"       ← preserved
      blacklist.blacklisted_at = 2026-06-11T10:30Z   ← preserved
      blacklist.blacklisted_by = recruiter_abc        ← preserved
      blacklist.restored_at    = 2026-06-11T11:00Z   ← appended
      blacklist.restored_by    = recruiter_abc        ← appended

    ``processed_emails`` also gets ``restored_at``/``restored_by`` appended
    without touching the original blacklist flags.

    Raises
    ------
    HTTPException(404): Candidate not found or belongs to another recruiter.
    HTTPException(409): Candidate is not blacklisted.
    HTTPException(500): DB write failure.
    """
    now = datetime.now(tz=timezone.utc)

    # ── Atomic conditional write ──────────────────────────────────────────────
    # Filter: candidate must exist + belong to recruiter + currently blacklisted.
    try:
        claimed = await db[_CANDIDATES_COL].find_one_and_update(
            {
                "candidate_id":             candidate_id,
                "recruiter_id":             recruiter_id,
                "blacklist.is_blacklisted": True,   # must currently be blacklisted
            },
            {"$set": {
                "blacklist.is_blacklisted": False,
                "blacklist.restored_at":    now,
                "blacklist.restored_by":    recruiter_id,
                "updated_at":               now,
                # ← reason / blacklisted_at / blacklisted_by intentionally untouched
            }},
            return_document=False,
        )
    except Exception as exc:
        logger.exception(
            "event=unblacklist.db_error candidate_id=%s detail=%s",
            candidate_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unblacklist candidate.",
        ) from exc

    if claimed is None:
        # Filter didn't match. Distinguish 404 vs 409.
        try:
            exists = await db[_CANDIDATES_COL].count_documents(
                {"candidate_id": candidate_id, "recruiter_id": recruiter_id}
            )
        except Exception as exc:
            logger.exception(
                "event=unblacklist.existence_check_error candidate_id=%s detail=%s",
                candidate_id, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to verify candidate status.",
            ) from exc

        if exists == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Candidate '{candidate_id}' not found.",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Candidate is not currently blacklisted.",
        )

    logger.info(
        "event=candidate.unblacklisted candidate_id=%s recruiter_id=%s",
        candidate_id, recruiter_id,
    )

    # ── Append restoration to processed_emails (best-effort, immutable ledger) ─
    await _flag_processed_email_restored(db, candidate_id, recruiter_id, recruiter_id, now)

    return {
        "success":       True,
        "candidateId":   candidate_id,
        "isBlacklisted": False,
        "reason":        None,
        "blacklistedAt": None,
        "message":       "Candidate has been restored successfully.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# ATS score retrieval
# ══════════════════════════════════════════════════════════════════════════════

async def get_candidate_ats_score(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    job_id:       str,
    recruiter_id: str,
) -> dict[str, Any]:
    """
    Fetch the ATS score for a specific (candidate, job) pair.

    This is a pure read function — it performs no computation, triggers no
    workers, and has no side effects.

    Query sequence (all indexed point lookups):
        1. ``candidate_job_scores`` → fetch score record.
           Filter: ``{candidate_id, job_id, recruiter_id}``
           Index:  ``uq_candidate_job_score (candidate_id, job_id)``
           The ``recruiter_id`` filter is an authorization guard (defence in
           depth); it does not reduce cardinality since ``candidate_id`` is a
           UUID4 (globally unique).

        2. [Only when status == "completed"] ``jobs`` → fetch current
           ``jd_analysis.version`` for staleness detection.
           Filter: ``{job_id, recruiter_id}``
           Index:  ``uq_job_recruiter (job_id, recruiter_id)``

    isStale computation:
        ``is_stale = (score.jd_analysis_version != job.jd_analysis.version)``
        Only meaningful for ``status == "completed"`` records.  For all other
        states ``is_stale`` is ``False`` — there is no score to be stale.
        Guards: if either version is ``None`` (legacy record without version
        field), ``is_stale`` defaults to ``False``.

    not_scored synthetic state:
        When no record exists in ``candidate_job_scores``, the function returns
        a dict with ``status="not_scored"`` rather than raising an exception.
        This keeps the API response shape uniform across all states — the
        endpoint always returns 200 OK and the frontend reads ``status`` to
        decide which UI state to render.

    Args:
        db:           Motor async database handle.
        candidate_id: UUID of the candidate (ownership already validated by
                      the caller via ``get_candidate_by_id`` before this call).
        job_id:       Job code (already uppercased by the API layer).
        recruiter_id: UUID of the authenticated recruiter (from JWT).

    Returns:
        dict with keys matching ``AtsScoreResponse`` fields (snake_case here;
        camelCase conversion happens at the Pydantic model layer):
          candidate_id, job_id, status, score, scored_at,
          jd_analysis_version, is_stale, score_breakdown

    Raises:
        HTTPException(500): On any DB read failure.
    """

    # ── Query 1: Fetch score record ───────────────────────────────────────────
    try:
        score_doc = await db[_SCORES_COL].find_one(
            {
                "candidate_id": candidate_id,
                "job_id":       job_id,
                "recruiter_id": recruiter_id,   # authorization guard
            },
            _SCORE_PROJECTION,
        )
    except Exception as exc:
        logger.exception(
            "event=ats_score.fetch_error "
            "candidate_id=%s job_id=%s detail=%s",
            candidate_id, job_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve ATS score.",
        ) from exc

    # ── No record → synthetic "not_scored" state ──────────────────────────────
    if score_doc is None:
        logger.info(
            "event=ats_score.not_scored candidate_id=%s job_id=%s",
            candidate_id, job_id,
        )
        return {
            "candidate_id":       candidate_id,
            "job_id":             job_id,
            "status":             ATS_STATUS_NOT_SCORED,
            "score":              None,
            "scored_at":          None,
            "jd_analysis_version": None,
            "is_stale":           False,
            "score_breakdown":    None,
        }

    stored_version: int | None = score_doc.get("jd_analysis_version")
    doc_status:     str        = score_doc.get("status", "")
    is_stale:       bool       = False

    # ── Query 2: isStale — only needed for completed scores ───────────────────
    # Skipped for processing/failed/skipped states: no score exists to be stale.
    if doc_status == ATS_STATUS_COMPLETED and stored_version is not None:
        try:
            job_doc = await db[_JOBS_COL].find_one(
                {"job_id": job_id, "recruiter_id": recruiter_id},
                _JD_VERSION_PROJECTION,
            )
        except Exception as exc:
            # Non-fatal: staleness detection is a best-effort signal.
            # A DB error here should not block the score from being returned.
            logger.warning(
                "event=ats_score.jd_version_fetch_error "
                "candidate_id=%s job_id=%s detail=%s "
                "— is_stale defaults to False.",
                candidate_id, job_id, exc,
            )
            job_doc = None

        if job_doc is not None:
            current_jd_version: int | None = (
                (job_doc.get("jd_analysis") or {}).get("version")
            )
            if current_jd_version is not None:
                is_stale = (stored_version != current_jd_version)
                if is_stale:
                    logger.info(
                        "event=ats_score.stale_detected "
                        "candidate_id=%s job_id=%s "
                        "score_version=%s current_version=%s",
                        candidate_id, job_id,
                        stored_version, current_jd_version,
                    )

    logger.info(
        "event=ats_score.fetched "
        "candidate_id=%s job_id=%s status=%s is_stale=%s",
        candidate_id, job_id, doc_status, is_stale,
    )

    return {
        "candidate_id":        candidate_id,
        "job_id":              job_id,
        "status":              doc_status,
        "score":               score_doc.get("score"),
        "scored_at":           score_doc.get("scored_at"),
        "jd_analysis_version": stored_version,
        "is_stale":            is_stale,
        "score_breakdown":     score_doc.get("score_breakdown"),
    }
