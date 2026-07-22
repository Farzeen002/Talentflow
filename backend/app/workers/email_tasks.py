"""
app/workers/email_tasks.py

RQ worker task definitions for email processing.

This module is executed by RQ worker processes, NOT by the FastAPI application.
All functions here are synchronous (RQ executes jobs in a thread pool).

Lifecycle contract
------------------
The ``processed_emails`` collection tracks every message through these states:

    pending (job_id=null)  → (ingestion inserted record; enqueue not confirmed)
    pending (job_id=set)   → (ingestion confirmed enqueue; worker not yet started)
    processing             → (this worker sets this at the start of each job)
    processed              → (this worker sets this on success)
    failed                 → (this worker sets this on unrecoverable error)

Each transition is written to MongoDB synchronously via PyMongo (not Motor)
because RQ workers run outside the FastAPI asyncio event loop.

Worker pipeline:
  1. Validate processed_emails record + idempotency guard
     - status=processed      → early return (already done)
     - status=pending, job_id=null → anomaly guard: raises RuntimeError
       (Step 5 confirm-enqueue in ingestion_service likely failed;
        reconciliation will delete the record; message will be re-enqueued)
     - status=processing     → previous worker crashed; re-attempt
     - status=pending, job_id=set → normal path
  2. Transition status → processing (writes processing_at for reconciliation)
  3. Fetch recruiter's encrypted OAuth tokens from MongoDB
  4. Fetch full email message via provider sync bridge
  5. Parse email body via parsing_service (never raises — errors accumulated)
  6. Normalize Q&A dict via normalizers.normalize_qa (type conversion)
  7. Build structured CandidateDocument (parsed=True, no raw body stored)
  8. Insert candidate via candidate_store (dedup on email.message_id)
  9. Transition status → processed (with candidate_id)
 10. On failure: transition status → failed (with error detail)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import pymongo
from redis import Redis as _Redis
from rq import Queue as _RQQueue

from app.config import get_settings
from app.models.candidate import (
    CandidateDocument,
    CandidateEmailMeta,
    CandidateProcessingState,
    CandidateRawEmail,
    ResumeMetadata,
    ResumeOriginal,
    ResumeStatus,
)
from app.services.parsing_service import parse_email_body
from app.services.storage import StorageError, get_storage
from app.utils.normalizers import normalize_qa
from app.workers.candidate_store import (
    ensure_candidate_indexes,
    insert_candidate,
)
from app.workers.email_sync import download_attachment, fetch_full_message

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Collection names ──────────────────────────────────────────────────────────
_PROCESSED_EMAILS_COLLECTION = "processed_emails"
_RECRUITERS_COLLECTION       = "recruiters"
_CANDIDATES_COLLECTION       = "candidates"

# ── Lifecycle status values (mirrored from ingestion_service to avoid
#    circular import — workers run in a separate process) ─────────────────────
_STATUS_PROCESSING = "processing"
_STATUS_PROCESSED  = "processed"
_STATUS_FAILED     = "failed"

# ── Flag: ensure candidate indexes are created once per worker process ────────
_indexes_ensured: bool = False


# ══════════════════════════════════════════════════════════════════════════════
# Sync MongoDB helper (PyMongo — no event loop required)
# ══════════════════════════════════════════════════════════════════════════════

def _get_sync_db() -> pymongo.database.Database:
    """
    Open a synchronous PyMongo connection to the configured MongoDB database.

    RQ workers run outside FastAPI's asyncio event loop, so Motor (async) is
    not suitable here.  PyMongo is already a transitive dependency via Motor
    and requires no additional installation.

    Returns:
        A ``pymongo.database.Database`` instance.

    Raises:
        pymongo.errors.ConnectionFailure: If MongoDB is unreachable.
    """
    client = pymongo.MongoClient(settings.MONGODB_URL)
    return client[settings.MONGODB_DB_NAME]


def _ensure_indexes_once(db: pymongo.database.Database) -> None:
    """
    Bootstrap candidate collection indexes exactly once per worker process.

    Uses a module-level flag to avoid redundant ``createIndex`` calls on
    every job execution.  Idempotent at the MongoDB level regardless.
    """
    global _indexes_ensured
    if not _indexes_ensured:
        ensure_candidate_indexes(db)
        _indexes_ensured = True


def _update_status(
    db:           pymongo.database.Database,
    message_id:   str,
    recruiter_id: str,
    status:       str,
    *,
    candidate_id: str | None = None,
    error:        str | None = None,
) -> None:
    """
    Update the lifecycle status of a ``processed_emails`` document.

    Args:
        db:           Synchronous PyMongo database handle.
        message_id:   Gmail message ID (part of the compound unique key).
        recruiter_id: Recruiter UUID (part of the compound unique key).
        status:       Target status string (``processing``, ``processed``, ``failed``).
        candidate_id: Optional candidate UUID set when status = ``processed``.
        error:        Optional error description set when status = ``failed``.
    """
    now = datetime.now(tz=timezone.utc)
    update_fields: dict[str, Any] = {
        "status":     status,
        "updated_at": now,
    }
    if candidate_id is not None:
        update_fields["candidate_id"] = candidate_id
    if error is not None:
        update_fields["error"] = error
    if status == _STATUS_PROCESSED:
        update_fields["processed_at"] = now
    if status == _STATUS_PROCESSING:
        # Written so Category C reconciliation can detect stale processing
        # records and reset them to pending after _STALE_PROCESSING_MINUTES.
        update_fields["processing_at"] = now

    result = db[_PROCESSED_EMAILS_COLLECTION].update_one(
        {"message_id": message_id, "recruiter_id": recruiter_id},
        {"$set": update_fields},
    )

    if result.matched_count == 0:
        logger.error(
            "Status update failed — no document found for "
            "message_id=%s recruiter_id=%s (target status=%s)",
            message_id, recruiter_id, status,
        )
    else:
        logger.debug(
            "Status updated to %r: message_id=%s recruiter_id=%s",
            status, message_id, recruiter_id,
        )


def _fetch_recruiter_tokens(
    db:           pymongo.database.Database,
    recruiter_id: str,
) -> str:
    """
    Fetch the encrypted OAuth token blob for a recruiter.

    Args:
        db:           Synchronous PyMongo database handle.
        recruiter_id: UUID of the recruiter.

    Returns:
        The ``oauth_tokens_encrypted`` string.

    Raises:
        ValueError: If the recruiter is not found, is not active, or has
                    an empty token blob.
    """
    doc = db[_RECRUITERS_COLLECTION].find_one(
        {"recruiter_id": recruiter_id},
        projection={
            "oauth_tokens_encrypted": 1,
            "oauth_status":           1,
            "_id":                    0,
        },
    )

    if doc is None:
        raise ValueError(
            f"Recruiter not found in DB: recruiter_id={recruiter_id!r}"
        )

    if doc.get("oauth_status") != "active":
        raise ValueError(
            f"Recruiter oauth_status is {doc.get('oauth_status')!r}, "
            f"expected 'active': recruiter_id={recruiter_id!r}"
        )

    blob: str = doc.get("oauth_tokens_encrypted", "")
    if not blob:
        raise ValueError(
            f"Empty oauth_tokens_encrypted for recruiter_id={recruiter_id!r}"
        )

    return blob


def _build_structured_metadata(
    parsed_metadata: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the structured ``metadata`` sub-document for a candidate.

    Populates ``name``, ``email``, and ``phone`` from the parser output if
    present.  All other fields are initialised to ``null`` / empty — they
    will be filled by downstream enrichment stages (Phase 4+).

    Args:
        parsed_metadata: ``parsed["metadata"]`` dict returned by
                         :func:`~app.services.parsing_service.parse_email_body`.

    Returns:
        A flat dict conforming to the candidate metadata schema.
    """
    return {
        "name":               parsed_metadata.get("name"),
        "email":              parsed_metadata.get("email"),
        "job_title":          parsed_metadata.get("job_title"),
        "current_role":       parsed_metadata.get("current_role"),
        "current_company":    parsed_metadata.get("current_company"),
        "experience_years":   parsed_metadata.get("experience_years"),
        "profile_ctc_rupees": parsed_metadata.get("profile_ctc_rupees"),
        "current_location":    parsed_metadata.get("current_location"),
        "profile_notice_days": parsed_metadata.get("profile_notice_days"),
    }


_SENDER_EMAIL_RE = re.compile(r"<([^>@\s]+@[^>\s]+)>")

# MIME types recognised as resume files.
_RESUME_MIME_TYPES = frozenset({
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
})


def _find_resume_attachment(
    attachments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """
    Return the first attachment dict that is a recognisable resume file.

    Requires ``attachment_id`` to be present — parts without it are inline
    images or embedded content (no ``attachmentId`` in the Gmail API payload).
    Matches on MIME type first; filename extension used as fallback.
    """
    for att in attachments:
        if not att.get("attachment_id"):
            continue
        mime     = (att.get("mime_type") or "").lower()
        filename = (att.get("filename") or "").lower()
        if mime in _RESUME_MIME_TYPES or filename.endswith((".pdf", ".doc", ".docx")):
            return att
    return None


def _update_resume_failed(
    db:           pymongo.database.Database,
    candidate_id: str,
    error:        str,
    *,
    attempt_ts:   datetime | None = None,
) -> None:
    """Persist a failed resume state to the candidate document."""
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


def _process_resume_attachment(
    *,
    db:             pymongo.database.Database,
    recruiter_id:   str,
    candidate_id:   str,
    message:        dict[str, Any],
    encrypted_blob: str,
    provider:       str = "gmail",
) -> None:
    """
    Download a resume attachment from the email provider and persist it to storage.

    This function **never raises** — all failures are caught, logged, and
    written to ``resume.processing.last_error`` so the calling worker
    continues to mark the email as ``processed`` regardless.

    Lifecycle transitions on the candidate document::

        pending  →  uploaded   (download + storage write succeeded)
        pending  →  failed     (any error during download or storage)

    Args:
        db:             Synchronous PyMongo handle.
        recruiter_id:   Owning recruiter UUID (blob path + logging).
        candidate_id:   Candidate UUID (blob path + MongoDB update).
        message:        Full message dict; must include ``attachments``
                        list with ``attachment_id`` fields intact.
        encrypted_blob: Fernet-encrypted OAuth tokens for API calls.
        provider:       Email provider identifier (``"gmail"`` or
                        ``"outlook"``).  Forwarded to the sync bridge so
                        the correct service class is used for the download.
    """
    raw_attachments: list[dict[str, Any]] = message.get("attachments") or []

    logger.info(
        "event=resume.detection_started recruiter_id=%s candidate_id=%s "
        "attachments_total=%d",
        recruiter_id, candidate_id, len(raw_attachments),
    )

    resume_att = _find_resume_attachment(raw_attachments)
    if resume_att is None:
        logger.info(
            "event=resume.no_attachment recruiter_id=%s candidate_id=%s "
            "— no resume attachment found, skipping resume pipeline.",
            recruiter_id, candidate_id,
        )
        return

    filename      = resume_att.get("filename", "")
    mime_type     = resume_att.get("mime_type", "")
    attachment_id = resume_att.get("attachment_id", "")
    gmail_msg_id  = message.get("message_id", "")

    logger.info(
        "event=resume.attachment_detected recruiter_id=%s candidate_id=%s "
        "filename=%r mime_type=%r attachment_id=%s",
        recruiter_id, candidate_id, filename, mime_type, attachment_id,
    )

    # ── Validate supported MIME type ───────────────────────────────────────
    if mime_type not in _RESUME_MIME_TYPES:
        reason = f"Unsupported MIME type: {mime_type!r}"
        logger.warning(
            "event=resume.validation_failed recruiter_id=%s candidate_id=%s "
            "reason=%s",
            recruiter_id, candidate_id, reason,
        )
        _update_resume_failed(db, candidate_id, reason)
        return

    logger.info(
        "event=resume.validation_passed recruiter_id=%s candidate_id=%s "
        "filename=%r mime_type=%r",
        recruiter_id, candidate_id, filename, mime_type,
    )

    attempt_ts = datetime.now(tz=timezone.utc)

    try:
        # ── Download from Gmail ────────────────────────────────────────────
        logger.info(
            "event=resume.download_started recruiter_id=%s candidate_id=%s "
            "gmail_message_id=%s attachment_id=%s",
            recruiter_id, candidate_id, gmail_msg_id, attachment_id,
        )

        raw_bytes = download_attachment(
            recruiter_id=recruiter_id,
            message_id=gmail_msg_id,
            attachment_id=attachment_id,
            encrypted_token_blob=encrypted_blob,
            provider=provider,
        )

        if not raw_bytes:
            raise ValueError("Gmail returned an empty attachment (0 bytes).")

        logger.info(
            "event=resume.download_completed recruiter_id=%s candidate_id=%s "
            "size_bytes=%d",
            recruiter_id, candidate_id, len(raw_bytes),
        )

        # ── Build deterministic blob path ──────────────────────────────────
        storage   = get_storage()
        blob_path = storage.build_resume_blob_path(
            recruiter_id=recruiter_id,
            candidate_id=candidate_id,
            filename=filename,
            mime_type=mime_type,
        )

        # ── Upload to storage ──────────────────────────────────────────────
        logger.info(
            "event=resume.upload_started recruiter_id=%s candidate_id=%s "
            "blob_path=%r",
            recruiter_id, candidate_id, blob_path,
        )

        upload_result = storage.upload_binary(blob_path, raw_bytes)

        logger.info(
            "event=resume.upload_completed recruiter_id=%s candidate_id=%s "
            "blob_path=%r size_bytes=%d",
            recruiter_id, candidate_id,
            upload_result.blob_path, upload_result.size_bytes,
        )

        # ── Update MongoDB: resume → uploaded ─────────────────────────────
        now = datetime.now(tz=timezone.utc)
        db[_CANDIDATES_COLLECTION].update_one(
            {"candidate_id": candidate_id},
            {"$set": {
                "resume.status":                     ResumeStatus.uploaded.value,
                "resume.original.blob_path":         upload_result.blob_path,
                "resume.original.filename":          filename,
                "resume.original.file_type":         mime_type,
                "resume.original.size_bytes":        upload_result.size_bytes,
                "resume.original.uploaded_at":       now,
                "resume.processing.attempts":        1,
                "resume.processing.last_attempt_at": now,
                "resume.processing.last_error":      None,
                "updated_at":                        now,
            }},
        )

        logger.info(
            "event=resume.status_updated recruiter_id=%s candidate_id=%s "
            "status=uploaded blob_path=%r",
            recruiter_id, candidate_id, upload_result.blob_path,
        )

        # ── Step 10: Enqueue preprocessing task ───────────────────────────
        _enqueue_preprocessing_task(recruiter_id=recruiter_id, candidate_id=candidate_id)

    except (StorageError, Exception) as exc:  # noqa: BLE001
        error_str = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "event=resume.processing_failed recruiter_id=%s candidate_id=%s "
            "detail=%s",
            recruiter_id, candidate_id, error_str,
        )
        _update_resume_failed(db, candidate_id, error_str, attempt_ts=attempt_ts)


_PREPROCESSING_QUEUE = "resume-preprocessing"


def _enqueue_preprocessing_task(*, recruiter_id: str, candidate_id: str) -> None:
    """
    Enqueue a resume preprocessing job onto the ``resume-preprocessing`` RQ queue.

    Failures here (Redis unavailable, misconfigured URL, etc.) are logged as
    warnings but do NOT propagate — the email ingestion pipeline must not be
    blocked by preprocessing queue availability.  The preprocessing job can
    always be re-enqueued manually or by a reconciliation sweep.

    Args:
        recruiter_id: Passed through to the task for logging context.
        candidate_id: Primary key of the candidate to preprocess.
    """
    logger.info(
        "diag=PREPROCESS_ENQUEUE_STARTED recruiter_id=%s candidate_id=%s "
        "queue=%r",
        recruiter_id, candidate_id, _PREPROCESSING_QUEUE,
    )

    redis_url = settings.REDIS_URL
    if not redis_url:
        logger.warning(
            "diag=PREPROCESS_ENQUEUE_SKIPPED "
            "event=preprocessing.enqueue_skipped candidate_id=%s "
            "reason=REDIS_URL_not_configured",
            candidate_id,
        )
        return

    try:
        conn  = _Redis.from_url(redis_url, decode_responses=False)
        queue = _RQQueue(name=_PREPROCESSING_QUEUE, connection=conn)
        job   = queue.enqueue(
            "app.workers.resume_tasks.preprocess_resume_task",
            recruiter_id,
            candidate_id,
        )
        logger.info(
            "diag=PREPROCESS_ENQUEUE_COMPLETED "
            "event=preprocessing.enqueued recruiter_id=%s candidate_id=%s "
            "queue=%r job_id=%s",
            recruiter_id, candidate_id, _PREPROCESSING_QUEUE, job.id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "event=preprocessing.enqueue_failed recruiter_id=%s candidate_id=%s "
            "detail=%s — preprocessing will not run automatically.",
            recruiter_id, candidate_id, exc,
        )


def _build_resume_metadata(attachments: list[dict[str, str]]) -> ResumeMetadata:
    """
    Initialise resume metadata from email attachment info.

    If a recognised resume file is found, status is set to ``pending``
    (binary not yet downloaded) and the original filename/MIME type are
    captured.  When no attachment is present, status is ``missing``.

    blob_path is left null at this stage — it is set by the resume
    download worker once the file is written to storage.
    """
    for att in attachments:
        mime     = (att.get("mime_type") or "").lower()
        filename = (att.get("filename") or "").lower()
        if mime in _RESUME_MIME_TYPES or filename.endswith((".pdf", ".doc", ".docx")):
            return ResumeMetadata(
                status=ResumeStatus.pending,
                original=ResumeOriginal(
                    filename=att.get("filename"),
                    file_type=att.get("mime_type"),
                ),
            )
    return ResumeMetadata(status=ResumeStatus.missing)


def _extract_sender_email(sender: str) -> str | None:
    """Parse email address from a 'Display Name <addr>' or bare 'addr' string."""
    if not sender:
        return None
    m = _SENDER_EMAIL_RE.search(sender)
    if m:
        return m.group(1).strip()
    # Bare address with no angle brackets
    addr = sender.strip()
    return addr if "@" in addr else None


def _build_candidate_document(
    recruiter_id:  str,
    message:       dict[str, Any],
    parsed:        dict[str, Any],
    normalized_qa: dict[str, Any],
) -> CandidateDocument:
    """
    Build a fully structured :class:`CandidateDocument` from a fetched Gmail
    message and its parsed / normalised output.

    The raw email body is intentionally **not** stored in the candidate
    document — it must be logged at DEBUG level by the caller before this
    function is invoked.  Only attachment metadata (filename + mime_type)
    is retained in ``raw_email`` for reference.

    Args:
        recruiter_id:  UUID of the owning recruiter.
        message:       Structured dict returned by
                       :func:`~app.workers.gmail_sync.fetch_full_message`.
        parsed:        Result dict from
                       :func:`~app.services.parsing_service.parse_email_body`
                       (keys: ``metadata``, ``qa``, ``parse_errors``).
        normalized_qa: Type-converted Q&A dict from
                       :func:`~app.utils.normalizers.normalize_qa`.

    Returns:
        A fully populated :class:`CandidateDocument` ready for insertion.
    """
    parsed_metadata: dict[str, Any] = parsed.get("metadata") or {}
    parse_errors:    list[str]      = parsed.get("parse_errors") or []

    # ── Inject sender email into metadata (Naukri hides it in the body) ──────
    sender_email = _extract_sender_email(message.get("from", ""))
    if sender_email:
        parsed_metadata = {**parsed_metadata, "email": sender_email}

    # ── Structured metadata sub-document ─────────────────────────────────────
    metadata = _build_structured_metadata(parsed_metadata)

    # ── Attachment metadata only (body intentionally omitted) ─────────────────
    attachment_meta: list[dict[str, str]] = [
        {
            "filename":  att.get("filename", ""),
            "mime_type": att.get("mime_type", ""),
        }
        for att in (message.get("attachments") or [])
    ]

    return CandidateDocument(
        recruiter_id=recruiter_id,
        source="nvite",
        job_id=parsed.get("job_id"),
        metadata=metadata,
        skills=parsed.get("skills") or {},
        qa=normalized_qa,
        resume=_build_resume_metadata(attachment_meta),
        email=CandidateEmailMeta(
            message_id=message.get("message_id", ""),
            timestamp=message.get("timestamp", ""),
            subject=message.get("subject", ""),
            sender=message.get("from", ""),
        ),
        processing=CandidateProcessingState(
            parsed=(parsed.get("processing") or {}).get("parsed", True),
            preprocessed=False,
            parser_version="1.0.0",
            parse_errors=parse_errors,
            needs_review=(
                (parsed.get("processing") or {}).get(
                    "needs_review", len(parse_errors) > 0
                )
            ),
        ),
        raw_email=CandidateRawEmail(
            body=       (parsed.get("raw")   or {}).get("body_html",  ""),
            clean_text= (parsed.get("clean") or {}).get("text",      ""),
            attachments=attachment_meta,
        ),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Worker task
# ══════════════════════════════════════════════════════════════════════════════

def process_email(
    recruiter_id: str,
    message_id:   str,
    provider:     str = "gmail",
) -> None:
    """
    RQ entry point for processing a single email message from any provider.

    Invoked by the RQ worker process after being enqueued by
    :class:`~app.services.ingestion_service.IngestionService`.

    The ``provider`` argument controls which email API client is used to
    fetch the message and download attachments.  It defaults to ``"gmail"``
    so that any RQ jobs already in the queue at deployment time (enqueued
    with only 2 positional args, before this argument was added) continue
    to be processed without error.

    Lifecycle transitions performed here:

    1. Fetch the current status from ``processed_emails``.
       - If ``processed``: log and return immediately (idempotent guard).
       - If ``processing``: a previous worker may have crashed; proceed to
         re-attempt (the status will be overwritten).
       - If ``pending`` (expected normal path): proceed.
       - If ``failed``: the ingestion layer skips re-enqueueing failed
         messages, so this branch should not be reached in practice.

    2. Transition status → ``processing``.

    3. Fetch recruiter's encrypted OAuth tokens from MongoDB.

    4. Fetch full email message via the synchronous email_sync bridge.

    5. Parse email body via :func:`~app.services.parsing_service.parse_email_body`.
       Never raises — parse errors are accumulated into ``parse_errors``.

    6. Normalize Q&A values via :func:`~app.utils.normalizers.normalize_qa`.
       Converts raw strings to proper Python types (bool, int, float, str).

    7. Build structured :class:`~app.models.candidate.CandidateDocument`
       (``parsed=True``, ``parser_version="1.0.0"``, no raw body stored).

    8. Insert candidate into MongoDB (dedup via unique index on
       ``email.message_id``).

    9. On success: transition status → ``processed`` with ``candidate_id``.
       On exception: transition status → ``failed``, record error string.

    Args:
        recruiter_id: UUID of the recruiter who owns the email account.
        message_id:   Provider-native message ID to process.
        provider:     Email provider identifier (``"gmail"`` or
                      ``"outlook"``).  Defaults to ``"gmail"``.

    Returns:
        None — RQ ignores return values unless ``result_ttl`` is configured.
    """
    logger.info(
        "event=email.processing_started recruiter_id=%s message_id=%s provider=%s",
        recruiter_id, message_id, provider,
    )

    # ── Open synchronous DB connection ────────────────────────────────────────
    try:
        db = _get_sync_db()
    except Exception as exc:
        logger.error(
            "event=email.processing_failed reason=db_connection_error "
            "recruiter_id=%s message_id=%s detail=%s",
            recruiter_id, message_id, exc,
        )
        # Re-raise so RQ marks the job as failed and can retry it.
        raise

    # ── Ensure candidate indexes (once per worker process) ────────────────────
    _ensure_indexes_once(db)

    # ── Step 1: Fetch processed_emails record + idempotency guard ─────────────
    existing = db[_PROCESSED_EMAILS_COLLECTION].find_one(
        {"message_id": message_id, "recruiter_id": recruiter_id},
        projection={"status": 1, "job_id": 1, "_id": 0},
    )

    if existing is None:
        logger.error(
            "event=email.processing_failed reason=no_lifecycle_record "
            "recruiter_id=%s message_id=%s",
            recruiter_id, message_id,
        )
        raise RuntimeError(
            f"No processed_emails record found for "
            f"message_id={message_id!r} recruiter_id={recruiter_id!r}. "
            f"The ingestion layer should have created it before enqueueing."
        )

    current_status: str = existing.get("status", "")

    if current_status == _STATUS_PROCESSED:
        logger.info(
            "event=email.already_processed recruiter_id=%s message_id=%s "
            "— skipping (idempotent).",
            recruiter_id, message_id,
        )
        return

    # ── job_id=null anomaly guard ─────────────────────────────────────────────
    # A pending record with job_id=null means the ingestion Step 5
    # (update job_id after confirmed enqueue) likely failed.  The worker
    # should not proceed — raise so RQ marks this job as failed.  The
    # Category A reconciliation sweep will delete the record after the
    # grace period and allow the message to be re-enqueued on the next cycle.
    if current_status == "pending" and existing.get("job_id") is None:
        logger.error(
            "event=email.processing_anomaly reason=job_id_null "
            "recruiter_id=%s message_id=%s "
            "— Step 5 (confirm enqueue) likely failed; "
            "reconciliation will clean up and allow retry.",
            recruiter_id, message_id,
        )
        raise RuntimeError(
            f"pending record has job_id=null for message_id={message_id!r}. "
            "Ingestion Step 5 (confirm enqueue) likely failed. "
            "Reconciliation will delete this record after the grace period."
        )

    if current_status == _STATUS_PROCESSING:
        logger.warning(
            "event=email.retry_after_crash recruiter_id=%s message_id=%s "
            "— previous worker may have crashed. Re-attempting.",
            recruiter_id, message_id,
        )

    # ── Step 2: Transition → processing (writes processing_at) ───────────────
    _update_status(db, message_id, recruiter_id, _STATUS_PROCESSING)
    logger.info(
        "event=email.status_updated status=processing "
        "recruiter_id=%s message_id=%s",
        recruiter_id, message_id,
    )

    try:
        # ── Step 3: Fetch recruiter's encrypted tokens ────────────────────────
        encrypted_blob = _fetch_recruiter_tokens(db, recruiter_id)
        logger.debug(
            "Recruiter tokens fetched: recruiter_id=%s", recruiter_id
        )

        # ── Step 4: Fetch full email message via provider sync bridge ─────────
        message: dict[str, Any] = fetch_full_message(
            recruiter_id=recruiter_id,
            message_id=message_id,
            encrypted_token_blob=encrypted_blob,
            provider=provider,
        )
        logger.info(
            "event=email.fetched recruiter_id=%s message_id=%s "
            "subject=%r body_len=%d attachments=%d",
            recruiter_id,
            message_id,
            message.get("subject", ""),
            len(message.get("body", "")),
            len(message.get("attachments", [])),
        )

        # ── Step 5: Parse email body ──────────────────────────────────────────
        body:    str = message.get("body")    or ""
        subject: str = message.get("subject") or ""

        logger.debug(
            "event=parsing.started recruiter_id=%s message_id=%s body_len=%d",
            recruiter_id, message_id, len(body),
        )
        # Log raw body at DEBUG so it is available for diagnostics without
        # being persisted to MongoDB.
        logger.debug(
            "raw_body recruiter_id=%s message_id=%s body=%r",
            recruiter_id, message_id, body[:500],  # truncate to 500 chars
        )

        parsed: dict[str, Any] = parse_email_body(body, subject=subject)

        logger.info(
            "event=parsing.completed recruiter_id=%s message_id=%s "
            "qa_fields=%d metadata_fields=%d parse_errors=%d",
            recruiter_id,
            message_id,
            len(parsed.get("qa", {})),
            len(parsed.get("metadata", {})),
            len(parsed.get("parse_errors", [])),
        )
        if parsed.get("parse_errors"):
            logger.warning(
                "event=parsing.errors recruiter_id=%s message_id=%s errors=%s",
                recruiter_id, message_id, parsed["parse_errors"],
            )

        # ── Step 6: Normalize Q&A values ──────────────────────────────────────
        normalized_qa: dict[str, Any] = normalize_qa(parsed.get("qa") or {})

        logger.info(
            "event=normalization.completed recruiter_id=%s message_id=%s "
            "fields=%d",
            recruiter_id, message_id, len(normalized_qa),
        )

        # ── Step 7 & 8: Build structured candidate document + insert ──────────
        candidate = _build_candidate_document(
            recruiter_id, message, parsed, normalized_qa
        )
        candidate_id = insert_candidate(db, candidate)

        # ── Step 9: Download + store resume attachment ────────────────────
        _process_resume_attachment(
            db=db,
            recruiter_id=recruiter_id,
            candidate_id=candidate_id,
            message=message,
            encrypted_blob=encrypted_blob,
            provider=provider,
        )

        logger.info(
            "event=candidate.created recruiter_id=%s message_id=%s "
            "candidate_id=%s qa_fields=%d needs_review=%s",
            recruiter_id,
            message_id,
            candidate_id,
            len(normalized_qa),
            candidate.processing.needs_review,
        )

        # ── Step 10: Transition → processed ──────────────────────────────────
        _update_status(
            db, message_id, recruiter_id,
            _STATUS_PROCESSED,
            candidate_id=candidate_id,
        )
        logger.info(
            "event=email.processing_completed recruiter_id=%s message_id=%s "
            "candidate_id=%s",
            recruiter_id, message_id, candidate_id,
        )

    except Exception as exc:  # noqa: BLE001
        error_detail = f"{type(exc).__name__}: {exc}"
        logger.exception(
            "event=email.processing_failed recruiter_id=%s message_id=%s "
            "detail=%s",
            recruiter_id, message_id, error_detail,
        )

        # ── Transition → failed ───────────────────────────────────────────────
        _update_status(
            db, message_id, recruiter_id,
            _STATUS_FAILED,
            error=error_detail,
        )
        # Re-raise so RQ records the exception in its job registry.
        raise
