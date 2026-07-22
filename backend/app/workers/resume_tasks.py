"""
app/workers/resume_tasks.py

RQ task definitions for resume preprocessing.

This module is executed by RQ worker processes, NOT by the FastAPI application.
All functions are synchronous (RQ runs jobs in a thread pool).

Queue name
----------
``resume-preprocessing``

Starting the dedicated preprocessing worker::

    rq worker resume-preprocessing

Or via docker-compose::

    docker compose up resume_worker

Pipeline
--------
1. Open a synchronous PyMongo connection (same pattern as email_tasks.py).
2. Delegate entirely to :func:`~app.services.resume.preprocessor.preprocess_resume`.
3. Log task completion.

The preprocessor itself never raises, so this task always exits cleanly.
RQ will only ever mark this job as failed if the DB connection itself fails
(before the preprocessor is invoked).
"""

from __future__ import annotations

import logging

import pymongo

from app.config import get_settings
from app.services.resume.preprocessor import preprocess_resume

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Sync DB helper (mirrors email_tasks._get_sync_db)
# ══════════════════════════════════════════════════════════════════════════════

def _get_sync_db() -> pymongo.database.Database:
    """
    Open a synchronous PyMongo connection.

    RQ workers run outside FastAPI's asyncio event loop, so Motor is not
    suitable here.  A fresh MongoClient is created per task to avoid
    connection state issues between jobs.
    """
    s = get_settings()
    client = pymongo.MongoClient(s.MONGODB_URL)
    return client[s.MONGODB_DB_NAME]


# ══════════════════════════════════════════════════════════════════════════════
# RQ task
# ══════════════════════════════════════════════════════════════════════════════

def preprocess_resume_task(recruiter_id: str, candidate_id: str) -> None:
    """
    RQ entry point for resume preprocessing.

    Enqueued by the email ingestion worker
    (:func:`~app.workers.email_tasks.process_email`) immediately after a
    successful attachment upload transitions the candidate resume lifecycle
    to ``uploaded``.

    This function:
    1. Opens a synchronous MongoDB connection.
    2. Calls :func:`~app.services.resume.preprocessor.preprocess_resume`,
       which orchestrates the full pipeline and **never raises**.
    3. Logs task completion regardless of extraction outcome.

    The extraction outcome (success / failure) is always reflected in the
    candidate document under ``resume.status`` and
    ``resume.processing.last_error``.

    Args:
        recruiter_id: UUID of the owning recruiter (for logging/observability).
        candidate_id: UUID of the candidate whose resume should be preprocessed.
    """
    logger.info(
        "diag=PREPROCESS_TASK_RECEIVED "
        "event=resume_task.started recruiter_id=%s candidate_id=%s",
        recruiter_id, candidate_id,
    )

    try:
        db = _get_sync_db()
    except Exception as exc:
        logger.error(
            "event=resume_task.db_connection_failed "
            "recruiter_id=%s candidate_id=%s detail=%s",
            recruiter_id, candidate_id, exc,
        )
        raise  # Let RQ mark the job as failed so it can be retried.

    logger.info(
        "diag=PREPROCESS_WORKER_STARTED "
        "event=resume_task.db_ready recruiter_id=%s candidate_id=%s",
        recruiter_id, candidate_id,
    )

    preprocess_resume(db=db, candidate_id=candidate_id)

    logger.info(
        "event=resume_task.finished recruiter_id=%s candidate_id=%s",
        recruiter_id, candidate_id,
    )
