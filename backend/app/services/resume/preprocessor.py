"""
app/services/resume/preprocessor.py

Resume preprocessing orchestrator.

Responsibilities
----------------
1. Fetch the candidate document from MongoDB.
2. Validate resume.original metadata (blob_path, MIME type, status).
3. Transition resume.status  →  processing.
4. Download original resume bytes from the storage abstraction layer.
5. Extract plain text via the extractor service (pdfminer.six / python-docx).
6. Upload extracted text to storage as ``extracted.txt``.
7. Update MongoDB resume metadata  →  completed.
8. On any failure: update resume.status  →  failed with last_error.

Design rules
------------
* **Never raises** — all exceptions are caught, logged, and persisted in
  ``resume.processing.last_error`` so the RQ task that called this completes
  normally.  The original resume binary is never modified or deleted on failure.
* **Provider-agnostic** — uses the storage abstraction layer exclusively;
  no direct filesystem or GCS SDK calls.
* **Retry-safe** — processes candidates in ``uploaded`` OR ``failed`` state,
  so a previously failed extraction can be retried by re-enqueueing the task.
* **Separated concerns** — extraction logic lives in ``extractor.py``;
  this module only orchestrates.

Lifecycle transitions
---------------------
::

    uploaded   →  processing  →  completed   (success)
    uploaded   →  processing  →  failed      (any error)
    failed     →  processing  →  completed   (retry succeeded)
    failed     →  processing  →  failed      (retry failed)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pymongo
import pymongo.database

from app.models.candidate import ResumeStatus
from app.services.resume.extractor import (
    ExtractionError,
    ExtractionResult,
    extract_text,
)
from app.services.storage import StorageError, get_storage

logger = logging.getLogger(__name__)

# ── MongoDB collection ────────────────────────────────────────────────────────
_CANDIDATES_COLLECTION = "candidates"

# ── MIME types that the extraction pipeline supports ─────────────────────────
_SUPPORTED_MIME_TYPES: frozenset[str] = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})

# Minutes before a stuck resume ``processing`` status may be reclaimed.
# Mirrors the stale-reclaim pattern in ``app/workers/jd_tasks._atomic_claim``.
_STALE_PROCESSING_MINUTES: int = 30


# ══════════════════════════════════════════════════════════════════════════════
# Atomic claim (mirrors jd_tasks._atomic_claim)
# ══════════════════════════════════════════════════════════════════════════════

def _atomic_claim(
    db:           pymongo.database.Database,
    candidate_id: str,
) -> tuple[dict[str, Any], datetime] | None:
    """
    Atomically claim resume preprocessing by transitioning status to
    ``processing``.

    Claim is allowed when:
      1. ``resume.status`` in {uploaded, failed}  → normal first-run or retry
      2. ``resume.status`` == processing AND ``last_attempt_at`` is stale
         → crash recovery

    Returns ``(pre-update document, attempt_ts)`` if claimed, else ``None``.
    """
    attempt_ts = datetime.now(tz=timezone.utc)
    stale_cutoff = attempt_ts - timedelta(minutes=_STALE_PROCESSING_MINUTES)

    doc: dict[str, Any] | None = db[_CANDIDATES_COLLECTION].find_one_and_update(
        {
            "candidate_id": candidate_id,
            "$or": [
                {"resume.status": {"$in": ["uploaded", "failed"]}},
                {
                    "resume.status":                     ResumeStatus.processing.value,
                    "resume.processing.last_attempt_at": {"$lt": stale_cutoff},
                },
            ],
        },
        {
            "$set": {
                "resume.status":                     ResumeStatus.processing.value,
                "resume.processing.last_attempt_at": attempt_ts,
                "updated_at":                        attempt_ts,
            }
        },
        return_document=False,
    )

    if doc is None:
        return None
    return doc, attempt_ts


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def preprocess_resume(
    *,
    db:           pymongo.database.Database,
    candidate_id: str,
) -> None:
    """
    Run the full resume preprocessing pipeline for one candidate.

    This function **never raises**.  All failures are caught, logged, and
    written to ``resume.processing.last_error`` so the calling RQ worker
    completes normally.

    Args:
        db:           Synchronous PyMongo database handle.
        candidate_id: UUID of the candidate whose resume should be preprocessed.
    """
    logger.info(
        "event=preprocessing.started candidate_id=%s",
        candidate_id,
    )

    # ── Step 1: Atomic claim (uploaded/failed → processing) ─────────────────
    claimed = _atomic_claim(db, candidate_id)
    if claimed is None:
        exists = db[_CANDIDATES_COLLECTION].find_one(
            {"candidate_id": candidate_id},
            projection={"candidate_id": 1, "resume.status": 1, "_id": 0},
        )
        if exists is None:
            logger.error(
                "event=preprocessing.candidate_not_found candidate_id=%s — aborting.",
                candidate_id,
            )
        else:
            logger.warning(
                "event=preprocessing.skipped candidate_id=%s status=%r — "
                "already claimed or not eligible.",
                candidate_id,
                (exists.get("resume") or {}).get("status"),
            )
        return

    candidate, attempt_ts = claimed

    recruiter_id: str            = candidate.get("recruiter_id", "")
    resume:       dict[str, Any] = candidate.get("resume") or {}

    logger.info(
        "event=preprocessing.candidate_fetched candidate_id=%s recruiter_id=%s",
        candidate_id, recruiter_id,
    )
    logger.info(
        "event=preprocessing.status_transition "
        "candidate_id=%s status=processing",
        candidate_id,
    )

    # ── Step 2: Validate resume metadata ──────────────────────────────────
    original:  dict[str, Any] = resume.get("original") or {}
    blob_path: str             = original.get("blob_path") or ""
    mime_type: str             = original.get("file_type") or ""

    if not blob_path:
        _fail(db, candidate_id, "resume.original.blob_path is missing.", attempt_ts)
        return

    if mime_type not in _SUPPORTED_MIME_TYPES:
        _fail(
            db, candidate_id,
            f"Unsupported MIME type for extraction: {mime_type!r}.",
            attempt_ts,
        )
        return

    try:
        storage = get_storage()

        # ── Step 4: Locate + download original resume ──────────────────────
        logger.info(
            "event=preprocessing.locating_resume "
            "candidate_id=%s blob_path=%r",
            candidate_id, blob_path,
        )

        if not storage.exists(blob_path):
            raise StorageError(
                f"Original resume blob not found at blob_path={blob_path!r}"
            )

        logger.info(
            "event=preprocessing.resume_located "
            "candidate_id=%s blob_path=%r",
            candidate_id, blob_path,
        )

        raw_bytes = storage.download_binary(blob_path)

        # ── Step 5: Extract text ───────────────────────────────────────────
        logger.info(
            "diag=RESUME_EXTRACTION_STARTED "
            "event=preprocessing.extraction_started "
            "candidate_id=%s mime_type=%r size_bytes=%d",
            candidate_id, mime_type, len(raw_bytes),
        )

        result: ExtractionResult = extract_text(raw_bytes, mime_type)

        if not result.text:
            raise ValueError(
                "Extracted text is empty — document may be image-only or corrupt."
            )

        logger.info(
            "diag=RESUME_EXTRACTION_COMPLETED "
            "event=preprocessing.extraction_completed "
            "candidate_id=%s extractor=%r extractor_version=%r char_count=%d",
            candidate_id,
            result.extractor,
            result.extractor_version,
            result.char_count,
        )

        # ── Step 6: Upload extracted text ──────────────────────────────────
        text_blob_path = storage.build_extracted_text_blob_path(
            recruiter_id=recruiter_id,
            candidate_id=candidate_id,
        )

        logger.info(
            "diag=EXTRACTED_TEXT_UPLOAD_STARTED "
            "event=preprocessing.upload_text_started "
            "candidate_id=%s text_blob_path=%r char_count=%d",
            candidate_id, text_blob_path, result.char_count,
        )

        upload_result = storage.upload_text(text_blob_path, result.text)

        logger.info(
            "diag=EXTRACTED_TEXT_UPLOAD_COMPLETED "
            "event=preprocessing.upload_text_completed "
            "candidate_id=%s text_blob_path=%r size_bytes=%d",
            candidate_id,
            upload_result.blob_path,
            upload_result.size_bytes,
        )

        # ── Step 7: Update MongoDB → completed ────────────────────────────
        now = datetime.now(tz=timezone.utc)
        db[_CANDIDATES_COLLECTION].update_one(
            {"candidate_id": candidate_id},
            {
                "$set": {
                    "resume.status":                        ResumeStatus.completed.value,
                    "resume.extracted.text_blob_path":      upload_result.blob_path,
                    "resume.extracted.char_count":          result.char_count,
                    "resume.extracted.extracted_at":        now,
                    "resume.extracted.extractor":           result.extractor,
                    "resume.extracted.extractor_version":   result.extractor_version,
                    "resume.extracted.language":            result.language,
                    "resume.processing.last_error":         None,
                    "resume.processing.last_attempt_at":    attempt_ts,
                    "updated_at":                           now,
                },
                "$inc": {"resume.processing.attempts": 1},
            },
        )

        logger.info(
            "diag=RESUME_STATUS_UPDATED_COMPLETED "
            "event=preprocessing.completed "
            "candidate_id=%s status=completed "
            "text_blob_path=%r char_count=%d",
            candidate_id,
            upload_result.blob_path,
            result.char_count,
        )

    except Exception as exc:  # noqa: BLE001
        error_str = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "diag=PREPROCESS_FAILED "
            "event=preprocessing.failed candidate_id=%s detail=%s",
            candidate_id, error_str,
        )
        _fail(db, candidate_id, error_str, attempt_ts)


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════

def _fail(
    db:           pymongo.database.Database,
    candidate_id: str,
    error:        str,
    attempt_ts:   datetime | None = None,
) -> None:
    """
    Persist a failed preprocessing state to the candidate document.

    Always increments ``resume.processing.attempts`` via ``$inc`` so retries
    are tracked without read-modify-write races.
    """
    now = datetime.now(tz=timezone.utc)
    db[_CANDIDATES_COLLECTION].update_one(
        {"candidate_id": candidate_id},
        {
            "$set": {
                "resume.status":                     ResumeStatus.failed.value,
                "resume.processing.last_error":      error,
                "resume.processing.last_attempt_at": attempt_ts or now,
                "updated_at":                        now,
            },
            "$inc": {"resume.processing.attempts": 1},
        },
    )
    logger.warning(
        "event=preprocessing.status_transition "
        "candidate_id=%s status=failed error=%r",
        candidate_id, error,
    )
