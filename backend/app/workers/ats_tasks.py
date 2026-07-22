"""
app/workers/ats_tasks.py

RQ worker task for ATS (Applicant Tracking System) resume scoring.

This module is executed by an RQ worker process — NOT by FastAPI.
All I/O is synchronous (PyMongo, sync storage SDK, sync LLM client).
This mirrors the existing pattern in jd_tasks.py.

Queue name
----------
    ats-scoring

Starting the dedicated worker
------------------------------
    rq worker ats-scoring --url redis://redis:6379/0

Or via docker-compose:
    docker-compose up ats_worker

Lifecycle contract (ats_run embedded in jobs document)
-------------------------------------------------------
    idle           → (default — never queued)
    queued         → (trigger_ats_calculation() sets this before enqueueing)
    processing     → (this worker sets this via atomic findOneAndUpdate)
    completed      → (all candidates done, 0 failures)
    partially_failed → (some candidates failed/skipped, others succeeded)
    failed         → (catastrophic failure — DB down, JD missing, etc.)

Per-candidate status (candidate_job_scores collection)
------------------------------------------------------
    pending    → (initial state when worker starts iterating)
    processing → (worker currently evaluating this candidate)
    completed  → (score computed and stored successfully)
    failed     → (LLM or scoring error for this candidate)
    skipped    → (no extracted resume available)

Idempotency + Stale-processing recovery
----------------------------------------
The worker atomically claims the task by transitioning ats_run.status
from queued → processing. The claim also allows reclaiming stale
processing runs (triggered_at older than ATS_STALE_PROCESSING_MINUTES).

This ensures:
  - Two workers racing → only one wins → no duplicate scoring spend.
  - Worker crash mid-batch → stale guard reclaims after timeout.
  - Re-run upserts over previous scores (deliberate overwrite behaviour).

Per-candidate isolation
-----------------------
The batch NEVER aborts due to one candidate failing.
Errors in individual candidates are caught, logged, and stored as
candidate_job_scores.status = 'failed'. The loop continues.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pymongo
import pymongo.database

from app.config import get_settings
from app.llm.ats_evaluator import evaluate_resume_ats
from app.llm.base import LLMProviderError
from app.llm.factory import get_llm_provider
from app.llm.resume_cleaner import clean_resume_text
from app.services.ats_scoring import compute_ats_score
from app.services.candidate_filter import get_filtered_candidates_sync
from app.services.storage import StorageError, get_storage

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── Collection names ──────────────────────────────────────────────────────────
_JOBS_COL   = "jobs"
_SCORES_COL = "candidate_job_scores"

# ── ats_run status constants ──────────────────────────────────────────────────
_STATUS_QUEUED           = "queued"
_STATUS_PROCESSING       = "processing"
_STATUS_COMPLETED        = "completed"
_STATUS_PARTIALLY_FAILED = "partially_failed"
_STATUS_FAILED           = "failed"

# ── candidate_job_scores status constants ─────────────────────────────────────
_CAND_PROCESSING = "processing"
_CAND_COMPLETED  = "completed"
_CAND_FAILED     = "failed"
_CAND_SKIPPED    = "skipped"

# ── Projection for filtered candidate query ───────────────────────────────────
# Only fetch what the ATS worker needs — no large text fields
_CANDIDATE_PROJECTION: dict[str, int] = {
    "candidate_id":                    1,
    "resume.status":                   1,
    "resume.extracted.text_blob_path": 1,
    "_id":                             0,
}


# ══════════════════════════════════════════════════════════════════════════════
# Sync DB helper  (mirrors jd_tasks._get_sync_db)
# ══════════════════════════════════════════════════════════════════════════════

def _get_sync_db() -> pymongo.database.Database:
    """
    Open a synchronous PyMongo connection.

    RQ workers run outside FastAPI's asyncio event loop, so Motor is not
    suitable.  A fresh MongoClient is created per task invocation.
    """
    client = pymongo.MongoClient(settings.MONGODB_URL)
    return client[settings.MONGODB_DB_NAME]


# ══════════════════════════════════════════════════════════════════════════════
# Atomic claim helper
# ══════════════════════════════════════════════════════════════════════════════

def _atomic_claim(
    db:           pymongo.database.Database,
    job_id:       str,
    recruiter_id: str,
) -> dict[str, Any] | None:
    """
    Atomically claim the ATS scoring task by transitioning
    ``ats_run.status`` from ``queued`` → ``processing``.

    Claim is allowed when:
      1. ``ats_run.status`` in {queued, failed}  → normal run or retry
      2. ``ats_run.status == processing`` AND ``triggered_at`` is stale
         (older than ``ATS_STALE_PROCESSING_MINUTES``)               → crash recovery

    Returns the pre-update job document on success, or ``None`` if the
    task cannot be claimed (already processing + fresh, or absent).
    """
    stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(
        minutes=settings.ATS_STALE_PROCESSING_MINUTES
    )
    now = datetime.now(tz=timezone.utc)

    return db[_JOBS_COL].find_one_and_update(
        {
            "job_id":       job_id,
            "recruiter_id": recruiter_id,
            "$or": [
                # Normal claimable states
                {"ats_run.status": {"$in": [_STATUS_QUEUED, _STATUS_FAILED]}},
                # Stale processing: previous worker crashed > STALE timeout ago
                {
                    "ats_run.status":       _STATUS_PROCESSING,
                    "ats_run.triggered_at": {"$lt": stale_cutoff},
                },
            ],
        },
        {
            "$set": {
                "ats_run.status":       _STATUS_PROCESSING,
                "ats_run.triggered_at": now,
                "updated_at":           now,
            }
        },
        return_document=False,  # return pre-update doc so we can read jd_analysis.result
    )


# ══════════════════════════════════════════════════════════════════════════════
# Job-level status helpers
# ══════════════════════════════════════════════════════════════════════════════

def _set_job_ats_failed(
    db:           pymongo.database.Database,
    job_id:       str,
    recruiter_id: str,
    error:        str,
) -> None:
    """Mark the entire ATS run as failed (catastrophic — cannot continue)."""
    now = datetime.now(tz=timezone.utc)
    db[_JOBS_COL].update_one(
        {"job_id": job_id, "recruiter_id": recruiter_id},
        {
            "$set": {
                "ats_run.status":       _STATUS_FAILED,
                "ats_run.error":        error,
                "ats_run.completed_at": now,
                "updated_at":           now,
            }
        },
    )


def _set_job_ats_finalized(
    db:            pymongo.database.Database,
    job_id:        str,
    recruiter_id:  str,
    failed_count:  int,
) -> None:
    """
    Set the final ats_run status after the candidate loop completes.
    completed if zero failures, partially_failed otherwise.
    """
    now    = datetime.now(tz=timezone.utc)
    status = _STATUS_COMPLETED if failed_count == 0 else _STATUS_PARTIALLY_FAILED
    db[_JOBS_COL].update_one(
        {"job_id": job_id, "recruiter_id": recruiter_id},
        {
            "$set": {
                "ats_run.status":       status,
                "ats_run.completed_at": now,
                "updated_at":           now,
            }
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# Per-candidate score helpers (candidate_job_scores collection)
# ══════════════════════════════════════════════════════════════════════════════

def _upsert_score(
    db:           pymongo.database.Database,
    candidate_id: str,
    job_id:       str,
    recruiter_id: str,
    fields:       dict[str, Any],
) -> None:
    """
    Upsert a candidate_job_scores document.

    Uses the compound unique index (candidate_id, job_id) as the filter.
    Re-running ATS for the same job overwrites the previous score — this
    is the deliberate re-trigger behaviour.
    """
    db[_SCORES_COL].update_one(
        {"candidate_id": candidate_id, "job_id": job_id},
        {
            "$set": {
                "candidate_id": candidate_id,
                "job_id":       job_id,
                "recruiter_id": recruiter_id,
                **fields,
            }
        },
        upsert=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# RQ task entry point
# ══════════════════════════════════════════════════════════════════════════════

def calculate_ats_task(job_id: str, recruiter_id: str, force: bool = False) -> None:
    """
    RQ entry point for ATS scoring.

    Enqueued by ``app.services.job_service.trigger_ats_calculation`` or
    ``trigger_rerun_ats`` immediately after ``ats_run.status = "queued"``.

    Args:
        job_id:       Naukri job code (already uppercased by the service layer).
        recruiter_id: UUID of the recruiter who owns the job.
        force:        If True, score ALL filtered candidates unconditionally
                      (rerun-ats mode).  If False (default), apply incremental
                      skip logic — skip candidates with a valid existing score
                      for the current JD version.

    Pipeline
    --------
    1.  Open a synchronous MongoDB connection.
    2.  Atomically claim the task (queued/failed → processing, or stale reclaim).
        If claim fails → already processing (fresh) or absent → return.
    3.  Read jd_analysis.result + jd_analysis.version from the pre-update doc.
        If result missing → set ats_run.status = failed, return.
    4.  Read job filters from the pre-update document.
    5.  Initialise storage singleton and LLM provider ONCE before the loop.
    6.  Fetch all filtered candidates (sync PyMongo, no pagination).
    7.  Set ats_run.total_candidates = len(candidates).
    7b. Pre-fetch existing score records for this job (single batch query).
    8.  FOR EACH candidate (sequential):
          [incremental only] Check existing score:
            • status==completed AND jd_version matches → skip (skipped_existing)
            • status==processing (crash residue) / failed / skipped /
              version mismatch / no record → score it
          a. Skip guard: no extracted resume → mark skipped_resume_missing, continue.
          b. Mark candidate_job_scores as processing.
          c. Download extracted text from GCS via download_binary().
             StorageError → mark skipped_resume_missing, continue.
          d. clean_resume_text() — ephemeral, never stored.
          e. evaluate_resume_ats() — LLM call, returns match signals.
          f. compute_ats_score() → deterministic weighted sum + post-process.
             Returns (final_score, score_breakdown).
          g. Upsert candidate_job_scores as completed.
          h. $inc ats_run.processed_candidates.
          On any exception in c–g: mark failed, $inc failed_candidates, continue.
    9.  Finalize: set ats_run.status = completed | partially_failed.

    This function NEVER raises for business failures — it absorbs them
    and writes appropriate status to MongoDB. It DOES re-raise on DB
    connection failure so RQ marks the task as failed and can retry.
    """
    logger.info(
        "event=ats_scoring.task_received job_id=%s recruiter_id=%s",
        job_id, recruiter_id,
    )

    # ── Step 1: Open DB ───────────────────────────────────────────────────────
    try:
        db = _get_sync_db()
    except Exception as exc:
        logger.error(
            "event=ats_scoring.db_connection_failed job_id=%s detail=%s",
            job_id, exc,
        )
        raise  # Let RQ mark as failed and retry

    # ── Step 2: Atomic claim ──────────────────────────────────────────────────
    pre_doc = _atomic_claim(db, job_id, recruiter_id)

    if pre_doc is None:
        logger.info(
            "event=ats_scoring.skipped reason=not_claimable "
            "job_id=%s recruiter_id=%s",
            job_id, recruiter_id,
        )
        return

    logger.info(
        "event=ats_scoring.claimed job_id=%s recruiter_id=%s",
        job_id, recruiter_id,
    )

    # ── Step 3: Read jd_analysis result + current version ───────────────────────────
    jd_meta: dict[str, Any]      = pre_doc.get("jd_analysis") or {}
    jd_analysis: dict[str, Any]  = jd_meta.get("result") or {}
    current_jd_version: int      = int(jd_meta.get("version") or 0)
    if not jd_analysis:
        error_msg = "jd_analysis.result is missing or empty on the job document."
        logger.error(
            "event=ats_scoring.failed reason=missing_jd_analysis job_id=%s",
            job_id,
        )
        _set_job_ats_failed(db, job_id, recruiter_id, error_msg)
        return

    # ── Step 4: Read job filters ──────────────────────────────────────────────
    filters_raw: dict[str, Any] = pre_doc.get("filters") or {}
    if not filters_raw:
        error_msg = "Job filters missing on document — cannot build candidate query."
        logger.error(
            "event=ats_scoring.failed reason=missing_filters job_id=%s",
            job_id,
        )
        _set_job_ats_failed(db, job_id, recruiter_id, error_msg)
        return

    # ── Step 5: Initialise storage + LLM provider ONCE before the loop ───────
    try:
        storage  = get_storage()
        provider = get_llm_provider(settings)
    except Exception as exc:
        error_msg = f"Failed to initialise storage or LLM provider: {exc}"
        logger.error(
            "event=ats_scoring.failed reason=init_error job_id=%s detail=%s",
            job_id, exc,
        )
        _set_job_ats_failed(db, job_id, recruiter_id, error_msg)
        return

    # ── Step 6: Fetch all filtered candidates ─────────────────────────────────
    try:
        candidates = get_filtered_candidates_sync(
            db=           db,
            job_id=       job_id,
            recruiter_id= recruiter_id,
            filters=      filters_raw,
            projection=   _CANDIDATE_PROJECTION,
        )
    except Exception as exc:
        error_msg = f"Failed to fetch filtered candidates: {exc}"
        logger.error(
            "event=ats_scoring.failed reason=candidate_fetch_error "
            "job_id=%s detail=%s",
            job_id, exc,
        )
        _set_job_ats_failed(db, job_id, recruiter_id, error_msg)
        return

    total = len(candidates)
    logger.info(
        "event=ats_scoring.candidates_fetched job_id=%s total=%d",
        job_id, total,
    )

    # ── Step 7: Set total_candidates on ats_run ───────────────────────────────
    db[_JOBS_COL].update_one(
        {"job_id": job_id, "recruiter_id": recruiter_id},
        {"$set": {"ats_run.total_candidates": total}},
    )

    if total == 0:
        logger.warning(
            "event=ats_scoring.no_candidates job_id=%s — finalizing as completed.",
            job_id,
        )
        _set_job_ats_finalized(db, job_id, recruiter_id, failed_count=0)
        return

    # ── Step 7b: Pre-fetch existing scores (incremental skip decision) ────────────
    # Single batch query — keyed by candidate_id for O(1) lookup in the loop.
    # In force mode the dict is unused but built cheaply (metadata fields only).
    existing_scores: dict[str, dict] = {}
    if not force:
        try:
            existing_scores = {
                s["candidate_id"]: s
                for s in db["candidate_job_scores"].find(
                    {"job_id": job_id},
                    {"candidate_id": 1, "status": 1,
                     "jd_analysis_version": 1, "_id": 0},
                )
                if s.get("candidate_id")
            }
            logger.info(
                "event=ats_scoring.existing_scores_loaded "
                "job_id=%s count=%d jd_version=%s",
                job_id, len(existing_scores), current_jd_version,
            )
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: treat all candidates as new
            logger.warning(
                "event=ats_scoring.existing_scores_load_failed "
                "job_id=%s detail=%s — treating all as new.",
                job_id, exc,
            )
            existing_scores = {}

    # ── Step 8: Sequential candidate loop ──────────────────────────────────────────
    for idx, candidate in enumerate(candidates, start=1):
        candidate_id: str = candidate.get("candidate_id", "")

        logger.info(
            "event=ats_scoring.candidate_start "
            "job_id=%s candidate=%d/%d candidate_id=%s",
            job_id, idx, total, candidate_id,
        )

        # ── Incremental skip: valid existing score → skip LLM call ────────────────
        # processing = crash residue (no result written) → re-score
        # failed / skipped → retry → re-score
        # completed + version mismatch → stale → re-score
        # completed + version matches → valid → skip
        if not force:
            existing = existing_scores.get(candidate_id)
            if existing is not None:
                existing_status = existing.get("status")
                stored_version  = existing.get("jd_analysis_version")
                is_valid_score  = (
                    existing_status == "completed"
                    and stored_version == current_jd_version
                )
                if is_valid_score:
                    logger.info(
                        "event=ats_scoring.candidate_skipped_existing "
                        "job_id=%s candidate_id=%s jd_version=%s",
                        job_id, candidate_id, current_jd_version,
                    )
                    db[_JOBS_COL].update_one(
                        {"job_id": job_id, "recruiter_id": recruiter_id},
                        {"$inc": {"ats_run.skipped_existing_candidates": 1}},
                    )
                    continue  # do not call LLM
                # Fall through: process this candidate

        # ── a. Skip guard: no extracted resume ────────────────────────────────
        resume      = candidate.get("resume") or {}
        resume_status = resume.get("status", "")
        extracted   = resume.get("extracted") or {}
        text_blob_path: str | None = extracted.get("text_blob_path")

        if resume_status != "completed" or not text_blob_path:
            skip_reason = (
                "Resume extraction not completed."
                if resume_status != "completed"
                else "No extracted text blob path on candidate."
            )
            logger.info(
                "event=ats_scoring.candidate_skipped "
                "job_id=%s candidate_id=%s reason=%r",
                job_id, candidate_id, skip_reason,
            )
            _upsert_score(db, candidate_id, job_id, recruiter_id, {
                "status": _CAND_SKIPPED,
                "score":  None,
                "error":  skip_reason,
                "scored_at": datetime.now(tz=timezone.utc),
            })
            db[_JOBS_COL].update_one(
                {"job_id": job_id, "recruiter_id": recruiter_id},
                {"$inc": {"ats_run.skipped_resume_missing": 1}},
            )
            continue

        # ── b. Mark candidate as processing ───────────────────────────────────
        _upsert_score(db, candidate_id, job_id, recruiter_id, {
            "status": _CAND_PROCESSING,
        })

        # ── c–g: Main evaluation block — isolated per candidate ───────────────
        try:
            # c. Download extracted text from GCS (single call — no exists() check)
            try:
                raw_bytes = storage.download_binary(text_blob_path)
                raw_text  = raw_bytes.decode("utf-8", errors="replace")
            except StorageError as exc:
                skip_reason = f"GCS blob not found or unreadable: {exc}"
                logger.warning(
                    "event=ats_scoring.candidate_skipped "
                    "job_id=%s candidate_id=%s reason=%r",
                    job_id, candidate_id, skip_reason,
                )
                _upsert_score(db, candidate_id, job_id, recruiter_id, {
                    "status": _CAND_SKIPPED,
                    "score":  None,
                    "error":  skip_reason,
                    "scored_at": datetime.now(tz=timezone.utc),
                })
                db[_JOBS_COL].update_one(
                    {"job_id": job_id, "recruiter_id": recruiter_id},
                    {"$inc": {"ats_run.skipped_resume_missing": 1}},
                )
                continue

            # d. Clean text — ephemeral, never stored
            cleaned_text = clean_resume_text(raw_text)

            if not cleaned_text.strip():
                skip_reason = "Resume text was empty after cleaning."
                logger.warning(
                    "event=ats_scoring.candidate_skipped "
                    "job_id=%s candidate_id=%s reason=%r",
                    job_id, candidate_id, skip_reason,
                )
                _upsert_score(db, candidate_id, job_id, recruiter_id, {
                    "status": _CAND_SKIPPED,
                    "score":  None,
                    "error":  skip_reason,
                    "scored_at": datetime.now(tz=timezone.utc),
                })
                db[_JOBS_COL].update_one(
                    {"job_id": job_id, "recruiter_id": recruiter_id},
                    {"$inc": {"ats_run.skipped_resume_missing": 1}},
                )
                continue

            # e. LLM evaluation — returns per-requirement match signals
            llm_eval = evaluate_resume_ats(
                jd_analysis=         jd_analysis,
                cleaned_resume_text= cleaned_text,
                provider=            provider,
            )

            # f. Deterministic score — pure math, no LLM
            score, breakdown = compute_ats_score(llm_eval, jd_analysis)

            # Persist score.
            # IMPORTANT: jd_analysis_version must come from current_jd_version
            # (read from job.jd_analysis.version at step 3), NOT from
            # jd_analysis.get("version") — `jd_analysis` is the LLM result dict
            # and contains no version key.  Using it would always fall back to 1,
            # breaking the incremental skip logic for all jobs where version > 1.
            now = datetime.now(tz=timezone.utc)
            _upsert_score(db, candidate_id, job_id, recruiter_id, {
                "status":               _CAND_COMPLETED,
                "score":                score,
                "llm_evaluation":       llm_eval,
                "score_breakdown":      breakdown,
                "experience_years":     llm_eval.get("experience_years"),
                "roles":                llm_eval.get("roles", []),
                "error":                None,
                "scored_at":            now,
                "jd_analysis_version":  current_jd_version,  # FIX: was jd_analysis.get("version", 1)
            })

            db[_JOBS_COL].update_one(
                {"job_id": job_id, "recruiter_id": recruiter_id},
                {"$inc": {"ats_run.processed_candidates": 1}},
            )

            logger.info(
                "event=ats_scoring.candidate_completed "
                "job_id=%s candidate_id=%s score=%.2f",
                job_id, candidate_id, score,
            )

        except (LLMProviderError, ValueError, Exception) as exc:  # noqa: BLE001
            # Per-candidate failure — log, store, CONTINUE. Never abort batch.
            error_str = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "event=ats_scoring.candidate_failed "
                "job_id=%s candidate_id=%s detail=%s",
                job_id, candidate_id, error_str,
            )
            _upsert_score(db, candidate_id, job_id, recruiter_id, {
                "status": _CAND_FAILED,
                "score":  None,
                "error":  error_str,
                "scored_at": datetime.now(tz=timezone.utc),
            })
            db[_JOBS_COL].update_one(
                {"job_id": job_id, "recruiter_id": recruiter_id},
                {"$inc": {"ats_run.failed_candidates": 1}},
            )
            # CONTINUE to next candidate — batch never aborts
            continue

    # ── Step 9: Finalize ──────────────────────────────────────────────────────
    # Re-read current failed count from DB (accumulated via $inc)
    job_doc = db[_JOBS_COL].find_one(
        {"job_id": job_id, "recruiter_id": recruiter_id},
        {"ats_run.failed_candidates": 1},
    )
    failed_count = (job_doc or {}).get("ats_run", {}).get("failed_candidates", 0)

    _set_job_ats_finalized(db, job_id, recruiter_id, failed_count=failed_count)

    logger.info(
        "event=ats_scoring.task_completed job_id=%s recruiter_id=%s "
        "total=%d failed=%d",
        job_id, recruiter_id, total, failed_count,
    )
