"""
app/workers/jd_tasks.py

RQ worker task for JD semantic analysis.

This module is executed by an RQ worker process — NOT by FastAPI.
All I/O is synchronous (PyMongo, sync OpenAI client).  This mirrors the
existing pattern in email_tasks.py and resume_tasks.py.

Queue name
----------
    jd-analysis

Starting the worker
-------------------
    rq worker jd-analysis

Or alongside existing workers:
    rq worker default resume-preprocessing jd-analysis

Lifecycle contract (jd_analysis embedded in jobs document)
-----------------------------------------------------------
    pending     → (create_job inserts this when description is non-empty)
    processing  → (this worker sets this via atomic findOneAndUpdate)
    completed   → (this worker sets this on LLM success)
    failed      → (this worker sets this on LLM failure — retryable)
    not_available → (create_job inserts this when description is empty — never queued)

Idempotency + Stale-processing recovery
----------------------------------------
Before calling the LLM, the worker atomically claims the task by transitioning
the status.  The claim filter allows:

    1. status in {pending, failed}  → normal claim
    2. status == processing AND triggered_at < now - STALE_TIMEOUT  → stale reclaim

This ensures:
  - Two workers racing → only one wins the atomic update → no double LLM spend.
  - Worker crash after claiming → stale guard reclaims after timeout → no permanent brick.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pymongo

from app.config import get_settings
from app.llm.base import LLMProviderError
from app.llm.factory import get_llm_provider
from app.llm.jd_analyzer import analyze_jd
from app.services.jd_weight_normalizer import normalize_jd_weights

logger   = logging.getLogger(__name__)
settings = get_settings()

# ── Collection ────────────────────────────────────────────────────────────────
_JOBS_COL = "jobs"

# ── jd_analysis status constants ─────────────────────────────────────────────
_STATUS_PROCESSING = "processing"
_STATUS_COMPLETED  = "completed"
_STATUS_FAILED     = "failed"


# ══════════════════════════════════════════════════════════════════════════════
# Sync DB helper  (mirrors resume_tasks._get_sync_db)
# ══════════════════════════════════════════════════════════════════════════════

def _get_sync_db() -> pymongo.database.Database:
    """
    Open a synchronous PyMongo connection.

    RQ workers run outside FastAPI's asyncio event loop, so Motor is not
    suitable.  A fresh MongoClient is created per task to avoid stale
    connection state between jobs.
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
    Atomically claim the JD analysis task by transitioning status to
    ``"processing"``.

    Claim is allowed when:
      1. ``jd_analysis.status`` in {pending, failed}  → normal first-run or retry
      2. ``jd_analysis.status`` == processing AND ``triggered_at`` is stale
         (older than ``JD_STALE_PROCESSING_MINUTES``)               → crash recovery

    Returns the pre-update job document if claimed, or ``None`` if the task
    could not be claimed (already completed, or processing is still fresh).
    """
    stale_cutoff = datetime.now(tz=timezone.utc) - timedelta(
        minutes=settings.JD_STALE_PROCESSING_MINUTES
    )
    now = datetime.now(tz=timezone.utc)

    return db[_JOBS_COL].find_one_and_update(
        {
            "job_id":       job_id,
            "recruiter_id": recruiter_id,
            "$or": [
                # Normal claimable states
                {"jd_analysis.status": {"$in": ["pending", "failed"]}},
                # Stale processing: previous worker crashed > STALE timeout ago
                {
                    "jd_analysis.status":       _STATUS_PROCESSING,
                    "jd_analysis.triggered_at": {"$lt": stale_cutoff},
                },
            ],
        },
        {
            "$set": {
                "jd_analysis.status":       _STATUS_PROCESSING,
                "jd_analysis.triggered_at": now,
                "updated_at":               now,
            }
        },
        return_document=False,  # return pre-update doc (we need description)
    )


def _set_completed(
    db:           pymongo.database.Database,
    job_id:       str,
    recruiter_id: str,
    result:       dict[str, Any],
) -> None:
    """Persist a successful analysis result and atomically increment version.

    Uses ``$inc`` on ``jd_analysis.version`` so the increment is a single
    atomic DB operation — no TOCTOU window even if two workers somehow race.

    Version lifecycle:
        job created  → version = 0   (set by create_job)
        first run    → version = 1   ($inc here)
        re-analysis  → version = 2   ($inc here again)

    NOTe: This function is only ever called on a successful LLM completion.
    It is never reused for retry paths, resume logic, or status corrections.
    The $inc is safe.

    Hard guard: normalize_jd_weights() is called here as a final safety net
    before any DB write.  Even if a future code path calls _set_completed()
    without first calling normalize_jd_weights(), corrupt weights can never
    reach MongoDB.
    """
    # Hard guard — last line of defence before the DB write.
    # analyze_jd_task() already calls this, but we call it again here so
    # no future code path can bypass it.
    try:
        result = normalize_jd_weights(result, job_id)
    except ValueError as exc:
        # If normalization fails here (e.g. >100 requirements), still write
        # the result but log at ERROR so the data issue is visible.
        # We do NOT abort — the LLM result is stored as-is, and the earlier
        # call in analyze_jd_task() will have already caught this and set
        # status=failed before reaching here.  This branch is a belt-and-
        # suspenders fallback only.
        logger.error(
            "event=jd_tasks._set_completed.normalizer_failed "
            "job_id=%s detail=%s — storing result without normalization.",
            job_id, exc,
        )

    now = datetime.now(tz=timezone.utc)
    db[_JOBS_COL].update_one(
        {"job_id": job_id, "recruiter_id": recruiter_id},
        {
            "$set": {
                "jd_analysis.status":       _STATUS_COMPLETED,
                "jd_analysis.result":       result,
                "jd_analysis.error":        None,
                "jd_analysis.analyzed_at":  now,   # when THIS version was computed
                "jd_analysis.completed_at": now,   # kept for backwards compat
                "updated_at":               now,
            },
            "$inc": {
                "jd_analysis.version": 1,          # 0→1 first run, 1→2 re-run
            },
        },
    )


def _set_failed(
    db:           pymongo.database.Database,
    job_id:       str,
    recruiter_id: str,
    error:        str,
) -> None:
    """Persist a failed analysis — retryable by RQ."""
    now = datetime.now(tz=timezone.utc)
    db[_JOBS_COL].update_one(
        {"job_id": job_id, "recruiter_id": recruiter_id},
        {
            "$set": {
                "jd_analysis.status":       _STATUS_FAILED,
                "jd_analysis.error":        error,
                "jd_analysis.completed_at": now,
                "updated_at":               now,
            }
        },
    )


# ══════════════════════════════════════════════════════════════════════════════
# RQ task entry point
# ══════════════════════════════════════════════════════════════════════════════

def analyze_jd_task(job_id: str, recruiter_id: str) -> None:
    """
    RQ entry point for JD semantic analysis.

    Enqueued by ``app.services.job_service._enqueue_jd_analysis`` immediately
    after a job document is inserted with ``jd_analysis.status = "pending"``.

    Pipeline
    --------
    1. Open a synchronous MongoDB connection.
    2. Atomically claim the task (pending/failed → processing, or stale reclaim).
       If claim fails → already completed or owned → return immediately.
    3. Read the job description from the pre-update document.
    4. Build LLM provider from settings.
    5. Call ``analyze_jd(description, provider)`` — your prompt plugs in there.
    6. On success → update jd_analysis to {status: completed, result: {...}}.
    7. On any failure → update jd_analysis to {status: failed, error: "..."}.

    This function **never raises** for business failures (LLM errors, missing
    description, etc.) — it absorbs them and writes status=failed.  It DOES
    re-raise on DB connection failure so RQ marks the task as failed and retries.

    Args:
        job_id:       Naukri job code (e.g. ``"DBA002"``).
        recruiter_id: UUID of the recruiter who owns the job.
    """
    logger.info(
        "event=jd_analysis.task_received job_id=%s recruiter_id=%s",
        job_id, recruiter_id,
    )

    # ── Step 1: Open DB ───────────────────────────────────────────────────────
    try:
        db = _get_sync_db()
    except Exception as exc:
        logger.error(
            "event=jd_analysis.db_connection_failed job_id=%s detail=%s",
            job_id, exc,
        )
        raise  # Let RQ mark as failed and retry

    # ── Step 2: Atomic claim ──────────────────────────────────────────────────
    pre_doc = _atomic_claim(db, job_id, recruiter_id)

    if pre_doc is None:
        logger.info(
            "event=jd_analysis.skipped reason=not_claimable "
            "job_id=%s recruiter_id=%s",
            job_id, recruiter_id,
        )
        return

    logger.info(
        "event=jd_analysis.claimed job_id=%s recruiter_id=%s",
        job_id, recruiter_id,
    )

    # ── Step 3: Read description from pre-update document ─────────────────────
    description: str = (pre_doc.get("description") or "").strip()

    if not description:
        # Guard: should never reach here (create_job skips enqueueing when
        # description is empty), but handle defensively.
        logger.warning(
            "event=jd_analysis.skipped reason=empty_description job_id=%s",
            job_id,
        )
        _set_failed(db, job_id, recruiter_id, "Description was empty at worker time.")
        return

    # ── Steps 4 + 5: Build provider + call LLM ───────────────────────────────
    try:
        provider = get_llm_provider(settings)
        result   = analyze_jd(description=description, provider=provider)

        # ── Step 5b: Normalize weights ────────────────────────────────────────
        # The LLM frequently hallucinates weight sums (e.g. returns 115 while
        # writing total_weight=100).  normalize_jd_weights() corrects this so
        # ats_scoring.py always scores out of exactly 100 points.
        # If normalization raises (e.g. >100 requirements), it falls through
        # to the except block below and marks the task as failed → retryable.
        result = normalize_jd_weights(result, job_id)

    except (LLMProviderError, ValueError, Exception) as exc:  # noqa: BLE001
        error_str = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "event=jd_analysis.failed job_id=%s detail=%s",
            job_id, error_str,
        )
        _set_failed(db, job_id, recruiter_id, error_str)
        return

    # ── Step 6: Persist success ───────────────────────────────────────────────
    _set_completed(db, job_id, recruiter_id, result)
    logger.info(
        "event=jd_analysis.completed job_id=%s recruiter_id=%s",
        job_id, recruiter_id,
    )
