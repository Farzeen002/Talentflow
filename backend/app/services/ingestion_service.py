"""
app/services/ingestion_service.py

Production-grade email ingestion orchestrator.

Responsibilities:
  - Fetch all active recruiters from MongoDB
  - Decrypt their stored OAuth tokens
  - Use GmailService / OutlookService to pull new messages (batch-limited)
  - Deduplicate against the ``processed_emails`` collection
  - Enqueue new message IDs into an RQ queue for Phase-3 workers
  - Persist refreshed access tokens back to the DB when the provider
    silently obtains a new one during a 401-refresh cycle
  - Reconcile stale lifecycle records at the start of every cycle
  - Continue gracefully when individual recruiters or messages fail

Design constraints:
  - NO FastAPI request/response objects
  - NO inline email parsing (delegated entirely to email_tasks.py)
  - Fully async for all MongoDB I/O; RQ enqueue is synchronous (RQ limitation)
  - One recruiter failure must never abort the remaining batch
  - MongoDB is the sole source of truth for ingestion lifecycle tracking

Lifecycle ordering guarantee:
  Insert-first: the ``processed_emails`` record is always written to MongoDB
  BEFORE the RQ job is created.  This keeps all dedup logic inside MongoDB
  and prevents duplicate enqueues entirely.  A failed enqueue leaves a
  ``(pending, job_id=null)`` record that is recognised and cleaned up by
  reconciliation on the next cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING
from rq import Queue
from rq.job import Job as RQJob

from app.models.recruiter import OAuthStatus
from app.services.auth_service import decrypt_oauth_tokens, encrypt_oauth_tokens
from app.services.gmail_service import GmailService
from app.services.outlook_service import (
    OutlookFolderNotFoundError,
    OutlookService,
)
from app.services.provider_errors import (
    EmailProviderAuthError,
    EmailProviderRateLimitError,
)

logger = logging.getLogger(__name__)

# ── Collection names ──────────────────────────────────────────────────────────
_RECRUITERS_COLLECTION       = "recruiters"
_PROCESSED_EMAILS_COLLECTION = "processed_emails"

# ── Ingestion batch size ──────────────────────────────────────────────────────
# Keep small to avoid long-running coroutines and stay within provider rate limits.
_BATCH_SIZE: int = 20

# ── Reconciliation TTLs ───────────────────────────────────────────────────────
# Grace period before Category A (pending, job_id=null) records are deleted.
# Guards against a race between Steps 3 (enqueue) and 4 (update job_id) and
# a concurrent reconciliation sweep.  3 minutes is far beyond the time these
# two sequential writes could take under any realistic load.
_UNCONFIRMED_PENDING_GRACE_MINUTES: int = 3

# Age threshold before a confirmed-pending (job_id set) record is inspected.
# 10 minutes is conservative — a healthy worker picks up a job within seconds.
_CONFIRMED_PENDING_INSPECT_MINUTES: int = 10

# Age threshold before a ``processing`` record is considered stale and reset.
# Must be longer than the worker's ``job_timeout`` (default: 300 s = 5 min).
# 30 minutes leaves plenty of margin.
_STALE_PROCESSING_MINUTES: int = 30

# Maximum Category B records to inspect per reconciliation sweep.
# Bounds the Redis round-trip time regardless of queue depth.
_CATEGORY_B_BATCH_LIMIT: int = 200


# ── Processed email lifecycle statuses ───────────────────────────────────────

class ProcessedEmailStatus(str, Enum):
    """
    Lifecycle states for a message tracked in ``processed_emails``.

    Transitions::

        (absent)
            │
            │  insert_one()  ← MongoDB write — always first
            ▼
          pending
           ├─ job_id=null   ← enqueue not yet confirmed
           │    │
           │    ├── enqueue() succeeds → update job_id, enqueued_at
           │    │                           ↓
           │    │                     pending, job_id=set
           │    │
           │    └── enqueue() fails → stays (pending, job_id=null)
           │                → reconciliation deletes after grace period
           │                → next cycle: dedup finds nothing → retries
           │
           └─ job_id=set    ← RQ job confirmed in queue
                │
                ▼  worker picks up job
              processing    ← worker actively executing
                │
                ├── success → processed   (terminal)
                └── failure → failed      (terminal — manual reset required)

    Notes:
        ``job_id=null`` means *enqueue was not confirmed in MongoDB*.  In the
        normal failure path (Step 3 raised) no RQ job exists.  In the rare
        edge case where Step 3 succeeded but Step 4 (update job_id) failed, a
        live RQ job may exist — the worker detects this anomaly and raises so
        that RQ records the job as failed, and reconciliation then cleans up
        the record.

        ``failed`` records are terminal and require manual operator
        intervention (delete or reset the status field).  They are never
        touched by reconciliation because failures indicate a data or parsing
        error that should not be silently retried.
    """
    pending    = "pending"
    processing = "processing"
    processed  = "processed"
    failed     = "failed"


def _is_delta_accounted_for(existing: dict[str, Any]) -> bool:
    """
    Return True when a ``processed_emails`` record safely allows delta progress.

    Accounted states: ``processed``, ``processing``, or ``pending`` with a
    confirmed ``job_id``.  ``failed`` and ``pending`` without ``job_id`` are
    not accounted and must be repaired before advancing ``@odata.deltaLink``.
    """
    status = existing.get("status")
    if status == ProcessedEmailStatus.processed.value:
        return True
    if status == ProcessedEmailStatus.processing.value:
        return True
    if status == ProcessedEmailStatus.pending.value and existing.get("job_id"):
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Index bootstrap (call once at startup)
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_ingestion_indexes(db: AsyncIOMotorDatabase) -> None:
    """
    Create required indexes on the ``processed_emails`` collection.

    Idempotent — safe to call on every application startup.

    Indexes created:
      - Compound unique index on ``(message_id, recruiter_id)`` — the
        deduplication key that prevents double-processing.
      - Index on ``(status, job_id, created_at)`` — supports the
        Category A reconciliation query efficiently.
      - Index on ``(status, enqueued_at)`` — supports the Category B query.
      - Index on ``(status, processing_at)`` — supports the Category C query.

    Args:
        db: Motor database instance.
    """
    col = db[_PROCESSED_EMAILS_COLLECTION]

    await col.create_index(
        [("message_id", ASCENDING), ("recruiter_id", ASCENDING)],
        unique=True,
        name="uq_message_recruiter",
    )
    await col.create_index(
        [("status", ASCENDING), ("job_id", ASCENDING), ("created_at", ASCENDING)],
        name="idx_reconcile_category_a",
    )
    await col.create_index(
        [("status", ASCENDING), ("enqueued_at", ASCENDING)],
        name="idx_reconcile_category_b",
    )
    await col.create_index(
        [("status", ASCENDING), ("processing_at", ASCENDING)],
        name="idx_reconcile_category_c",
    )

    logger.info(
        "event=ingestion.indexes_ensured collection=%s",
        _PROCESSED_EMAILS_COLLECTION,
    )


# ══════════════════════════════════════════════════════════════════════════════
# IngestionService
# ══════════════════════════════════════════════════════════════════════════════

class IngestionService:
    """
    Orchestrates the full email ingestion pipeline for all active recruiters.

    Dependencies are injected at construction time so the class is fully
    testable without a running FastAPI application.

    Args:
        db:    Async Motor database instance.
        queue: RQ ``Queue`` instance connected to a live Redis server, or
               ``None`` if Redis is currently unavailable.  When ``None``,
               ``_handle_message()`` raises before touching MongoDB, so no
               orphaned records are created.

    Usage::

        from app.db.mongo import get_database
        from app.db.redis import get_redis_client
        from rq import Queue

        db    = get_database()
        redis = get_redis_client()
        queue = Queue(connection=redis)

        service = IngestionService(db=db, queue=queue)
        await service.run_ingestion()
    """

    def __init__(self, db: AsyncIOMotorDatabase, queue: Queue | None) -> None:
        self._db    = db
        self._queue = queue

    # =========================================================================
    # Public entry point
    # =========================================================================

    async def run_ingestion(self) -> None:
        """
        Execute one full ingestion cycle across all active recruiters.

        Starts with a reconciliation sweep to clean up any stale lifecycle
        records left from previous failed cycles before processing new messages.

        Flow per recruiter:
          1. Decrypt stored OAuth tokens.
          2. Open a provider context and fetch the latest message batch.
          3. For each message reference: check deduplication, mark as
             pending, enqueue the worker job, confirm the enqueue.
          4. If provider service silently refreshed the access token, re-encrypt
             and persist the new token blob back to MongoDB.

        Isolation guarantee:
          Any exception from a single recruiter is caught and logged; the
          loop continues with the remaining recruiters.
        """
        logger.info("event=ingestion.cycle_started")

        # Reconciliation always runs first — even if there are no recruiters.
        # Stale records from previous failed cycles must be cleaned regardless.
        await self._reconcile_stale_records()

        recruiters = await self._fetch_active_recruiters()

        if not recruiters:
            logger.info("event=ingestion.cycle_complete no_recruiters=true")
            return

        logger.info(
            "event=ingestion.processing_recruiters count=%d", len(recruiters)
        )

        for recruiter in recruiters:
            recruiter_id: str = recruiter.get("recruiter_id", "<unknown>")
            try:
                await self._process_recruiter(recruiter)
            except EmailProviderAuthError as exc:
                logger.error(
                    "event=ingestion.auth_failure recruiter_id=%s "
                    "— recruiter must re-authenticate. detail=%s",
                    recruiter_id, exc,
                )
            except EmailProviderRateLimitError as exc:
                logger.warning(
                    "event=ingestion.rate_limit recruiter_id=%s "
                    "— skipping batch. detail=%s",
                    recruiter_id, exc,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "event=ingestion.recruiter_error recruiter_id=%s "
                    "— continuing with remaining recruiters. detail=%s",
                    recruiter_id, exc,
                )

        logger.info("event=ingestion.cycle_complete")

    # =========================================================================
    # Reconciliation
    # =========================================================================

    async def _reconcile_stale_records(self) -> None:
        """
        Detect and recover broken or stuck lifecycle records.

        Runs unconditionally at the start of every ingestion cycle, before
        any new messages are processed.

        Three categories of records are handled:

        Category A — ``pending``, ``job_id=null``, older than grace period
        ------------------------------------------------------------------
        The lifecycle record exists but the enqueue was never confirmed in
        MongoDB.  In the normal case this means Step 3 (enqueue) raised and
        no RQ job exists.  In the rare edge case where Step 3 succeeded but
        Step 4 (update job_id) failed, a live RQ job may exist — but it will
        fail in the worker (job_id=null guard) and be recorded in RQ's
        failed registry.  Either way, deleting the record after the grace
        period is safe.

        The grace period (``_UNCONFIRMED_PENDING_GRACE_MINUTES``, default 3)
        prevents a race between the sequential Steps 3 and 4 and this sweep:
        without it, reconciliation could delete the record between those two
        writes, causing the worker to find no lifecycle record.

        Action: ``delete_many`` — next cycle re-inserts and re-enqueues.

        Category B — ``pending``, ``job_id=set``, older than inspect threshold
        -----------------------------------------------------------------------
        The lifecycle record has a confirmed job_id but has been pending
        longer than expected.

        BACKLOG SAFETY: do NOT delete based on time alone.  A slow or
        overloaded worker queue means a valid pending record can sit for
        hours.  Deleting it would cause a duplicate enqueue.

        Action: fetch all job statuses in a single Redis pipeline
        (``RQJob.fetch_many``).  Records whose jobs are still alive
        (queued/started/deferred/scheduled) are skipped.  Records whose jobs
        are terminal (finished/failed) or missing (evicted from Redis) are
        deleted so the next cycle can re-enqueue.

        Category C — ``processing``, old ``processing_at``
        ---------------------------------------------------
        A worker started but never completed — most likely killed mid-job
        (SIGKILL, OOM, deployment restart).  After a conservative TTL
        (``_STALE_PROCESSING_MINUTES``, default 30) the record is reset to
        ``pending`` so a new worker job can be dispatched.

        The worker's idempotency guards (``status=processed`` early-return
        and ``insert_candidate`` DuplicateKeyError recovery) handle any
        concurrent execution safely.
        """
        now = datetime.now(tz=timezone.utc)

        # ── Category A: pending + job_id=null + past grace period ────────────
        cat_a_cutoff = now - timedelta(minutes=_UNCONFIRMED_PENDING_GRACE_MINUTES)
        cat_a_result = await self._db[_PROCESSED_EMAILS_COLLECTION].delete_many({
            "status":     ProcessedEmailStatus.pending.value,
            "job_id":     None,
            "created_at": {"$lt": cat_a_cutoff},
        })
        if cat_a_result.deleted_count > 0:
            logger.warning(
                "event=ingestion.reconcile_category_a "
                "deleted=%d grace_minutes=%d "
                "— enqueue was not confirmed; messages will be re-claimed.",
                cat_a_result.deleted_count,
                _UNCONFIRMED_PENDING_GRACE_MINUTES,
            )

        # ── Category B: pending + job_id=set + old enqueued_at ────────────────
        cat_b_cutoff = now - timedelta(minutes=_CONFIRMED_PENDING_INSPECT_MINUTES)
        cat_b_records = await self._db[_PROCESSED_EMAILS_COLLECTION].find(
            {
                "status":      ProcessedEmailStatus.pending.value,
                "job_id":      {"$ne": None},
                "enqueued_at": {"$lt": cat_b_cutoff},
            },
            projection={
                "message_id":  1,
                "recruiter_id": 1,
                "job_id":      1,
                "_id":         0,
            },
        ).to_list(length=_CATEGORY_B_BATCH_LIMIT)

        if cat_b_records:
            await self._reconcile_category_b(cat_b_records)

        # ── Category C: processing + old processing_at → reset to pending ─────
        cat_c_cutoff = now - timedelta(minutes=_STALE_PROCESSING_MINUTES)
        cat_c_result = await self._db[_PROCESSED_EMAILS_COLLECTION].update_many(
            {
                "status":        ProcessedEmailStatus.processing.value,
                "processing_at": {"$lt": cat_c_cutoff},
            },
            {"$set": {
                "status":     ProcessedEmailStatus.pending.value,
                "error":      "reset_by_reconciliation: worker exceeded processing TTL",
                "updated_at": now,
            }},
        )
        if cat_c_result.modified_count > 0:
            logger.warning(
                "event=ingestion.reconcile_category_c "
                "reset=%d cutoff_minutes=%d "
                "— stale processing records reset to pending for retry.",
                cat_c_result.modified_count,
                _STALE_PROCESSING_MINUTES,
            )

    async def _reconcile_category_b(self, records: list[dict[str, Any]]) -> None:
        """
        Batch-inspect RQ job statuses for Category B pending records.

        Uses ``RQJob.fetch_many()`` to retrieve all job statuses in a single
        Redis pipeline round-trip, avoiding O(N) individual round-trips.

        For each record:
        - Job alive (queued/started/deferred/scheduled) → skip.
        - Job terminal (finished/failed) or missing (None) → delete record;
          next ingestion cycle will re-insert and re-enqueue.

        If the queue is ``None`` (Redis unavailable), the sweep is skipped
        entirely — Category B records will be re-inspected next cycle.

        Args:
            records: List of ``{message_id, recruiter_id, job_id}`` dicts,
                     already capped at ``_CATEGORY_B_BATCH_LIMIT``.
        """
        if self._queue is None:
            logger.warning(
                "event=ingestion.reconcile_category_b_skipped "
                "reason=queue_unavailable count=%d "
                "— will retry next cycle.",
                len(records),
            )
            return

        _ALIVE_STATUSES = frozenset({"queued", "started", "deferred", "scheduled"})

        job_ids      = [r["job_id"] for r in records]
        record_by_id = {r["job_id"]: r for r in records}

        # ── Single Redis round-trip for all job statuses ───────────────────────
        try:
            fetched_jobs: list[RQJob | None] = RQJob.fetch_many(
                job_ids,
                connection=self._queue.connection,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=ingestion.reconcile_category_b_fetch_error detail=%s "
                "— skipping Category B this cycle.",
                exc,
            )
            return

        to_delete: list[dict[str, Any]] = []

        for job_id, job in zip(job_ids, fetched_jobs):
            record = record_by_id[job_id]

            if job is None:
                # Not found in Redis — evicted under memory pressure or manually cleared.
                logger.warning(
                    "event=ingestion.reconcile_job_not_found "
                    "job_id=%s message_id=%s recruiter_id=%s "
                    "— job evicted from Redis; will allow re-enqueue.",
                    job_id, record["message_id"], record["recruiter_id"],
                )
                to_delete.append(record)
                continue

            try:
                job_status = job.get_status()
            except Exception as exc:  # noqa: BLE001
                # Status probe failed for this specific job — skip conservatively.
                logger.warning(
                    "event=ingestion.reconcile_job_status_error "
                    "job_id=%s detail=%s — skipping (conservative).",
                    job_id, exc,
                )
                continue

            if job_status in _ALIVE_STATUSES:
                logger.debug(
                    "event=ingestion.reconcile_job_alive "
                    "job_id=%s status=%s message_id=%s — worker is slow, skipping.",
                    job_id, job_status, record["message_id"],
                )
                continue

            # Terminal status: finished or failed.
            logger.warning(
                "event=ingestion.reconcile_job_terminal "
                "job_id=%s status=%s message_id=%s recruiter_id=%s "
                "— deleting pending record to allow re-enqueue.",
                job_id, job_status, record["message_id"], record["recruiter_id"],
            )
            to_delete.append(record)

        # ── Delete records whose jobs are terminal or missing ──────────────────
        if to_delete:
            for r in to_delete:
                await self._db[_PROCESSED_EMAILS_COLLECTION].delete_one({
                    "message_id":   r["message_id"],
                    "recruiter_id": r["recruiter_id"],
                    "status":       ProcessedEmailStatus.pending.value,
                    # Safety guard: never accidentally delete a job_id=null record here.
                    "job_id":       {"$ne": None},
                })
            logger.warning(
                "event=ingestion.reconcile_category_b_deleted "
                "deleted=%d — messages will be re-enqueued next cycle.",
                len(to_delete),
            )

    # =========================================================================
    # Private helpers
    # =========================================================================

    # ── Step 1: DB queries ────────────────────────────────────────────────────

    async def _fetch_active_recruiters(self) -> list[dict[str, Any]]:
        """
        Return all recruiter documents whose ``oauth_status`` is ``active``.

        Projection includes only the fields required for ingestion, keeping
        the cursor payload small.

        Returns:
            List of raw recruiter dicts (may be empty).
        """
        cursor = self._db[_RECRUITERS_COLLECTION].find(
            {"oauth_status": OAuthStatus.active.value},
            projection={
                "recruiter_id":           1,
                "email":                  1,
                "oauth_tokens_encrypted": 1,
                "provider":               1,
                "outlook_sync":           1,
                "_id":                    0,
            },
        )
        recruiters: list[dict[str, Any]] = await cursor.to_list(length=None)
        logger.debug(
            "event=ingestion.recruiters_fetched count=%d", len(recruiters)
        )
        return recruiters

    # ── Step 2: Per-recruiter pipeline ───────────────────────────────────────

    async def _process_recruiter(self, recruiter: dict[str, Any]) -> None:
        """
        Run the full ingestion pipeline for a single recruiter.

        Args:
            recruiter: Raw recruiter document dict from MongoDB.

        Raises:
            EmailProviderAuthError:      Propagated so the outer loop skips recruiter.
            EmailProviderRateLimitError: Propagated so the outer loop logs + skips.
            Exception:                   Any other error propagated for outer logging.
        """
        recruiter_id: str  = recruiter["recruiter_id"]
        email:        str  = recruiter.get("email", "<unknown>")
        provider_name: str = recruiter.get("provider", "gmail")

        logger.info(
            "event=ingestion.recruiter_started "
            "recruiter_id=%s email=%s provider=%s",
            recruiter_id, email, provider_name,
        )

        # ── Decrypt stored OAuth tokens ───────────────────────────────────────
        try:
            tokens: dict[str, str | None] = decrypt_oauth_tokens(
                recruiter["oauth_tokens_encrypted"]
            )
        except (ValueError, KeyError) as exc:
            logger.error(
                "event=ingestion.token_decrypt_failed "
                "recruiter_id=%s — skipping. detail=%s",
                recruiter_id, exc,
            )
            return

        access_token:  str | None = tokens.get("access_token")
        refresh_token: str | None = tokens.get("refresh_token")

        if not access_token or not refresh_token:
            logger.error(
                "event=ingestion.incomplete_tokens recruiter_id=%s "
                "access_token_present=%s refresh_token_present=%s — skipping.",
                recruiter_id, bool(access_token), bool(refresh_token),
            )
            return

        if provider_name not in ("gmail", "outlook"):
            logger.error(
                "event=ingestion.unknown_provider provider=%r "
                "recruiter_id=%s — skipping.",
                provider_name, recruiter_id,
            )
            return

        if provider_name == "outlook":
            await self._process_recruiter_outlook(
                recruiter=recruiter,
                recruiter_id=recruiter_id,
                email=email,
                access_token=access_token,
                refresh_token=refresh_token,
            )
            return

        # ── Select provider service (Gmail) ───────────────────────────────────
        svc_ctx = GmailService(
            access_token=access_token,
            refresh_token=refresh_token,
        )

        # ── Fetch messages and enqueue ────────────────────────────────────────
        original_access_token  = access_token
        original_refresh_token = refresh_token

        async with svc_ctx as svc:
            message_ids: list[str] = await svc.list_new_messages(
                max_results=_BATCH_SIZE,
            )

            logger.info(
                "event=ingestion.messages_fetched "
                "recruiter_id=%s provider=%s count=%d",
                recruiter_id, provider_name, len(message_ids),
            )

            queued  = 0
            skipped = 0
            failed  = 0

            for message_id in message_ids:
                try:
                    was_queued = await self._handle_message(
                        recruiter_id=recruiter_id,
                        message_id=message_id,
                        provider=provider_name,
                    )
                    if was_queued:
                        queued += 1
                    else:
                        skipped += 1
                except Exception as exc:  # noqa: BLE001
                    failed += 1
                    logger.warning(
                        "event=ingestion.message_error "
                        "message_id=%s recruiter_id=%s "
                        "— continuing with remaining messages. detail=%s",
                        message_id, recruiter_id, exc,
                    )

            logger.info(
                "event=ingestion.recruiter_complete "
                "recruiter_id=%s provider=%s "
                "queued=%d skipped=%d failed=%d",
                recruiter_id, provider_name, queued, skipped, failed,
            )

            # ── Capture current tokens before the context manager closes ──────
            current_access_token  = svc.get_current_access_token()
            current_refresh_token = svc.get_current_refresh_token()

        # Context manager closed; persist if either token changed.
        if (
            current_access_token  != original_access_token
            or current_refresh_token != original_refresh_token
        ):
            await self._persist_refreshed_token(
                recruiter_id=recruiter_id,
                new_access_token=current_access_token,
                new_refresh_token=current_refresh_token,
            )

    async def _process_recruiter_outlook(
        self,
        *,
        recruiter:      dict[str, Any],
        recruiter_id:   str,
        email:          str,
        access_token:   str,
        refresh_token:  str,
    ) -> None:
        """
        Run Microsoft Graph delta discovery for one Outlook recruiter.

        Completes one full delta round (all ``@odata.nextLink`` pages), enqueues
        every discovered message ID, and persists the new ``@odata.deltaLink``
        only when every ID is accounted for in ``processed_emails``.

        Any failure during enqueue aborts the round without saving the new
        delta link so Graph replays the same changes on the next cycle.
        """
        outlook_sync: dict[str, Any] = recruiter.get("outlook_sync") or {}
        delta_link: str | None = outlook_sync.get("delta_link")
        folder_id:  str | None = outlook_sync.get("folder_id")

        original_access_token  = access_token
        original_refresh_token = refresh_token

        try:
            async with OutlookService(
                access_token=access_token,
                refresh_token=refresh_token,
            ) as svc:
                delta_result = await svc.sync_nvite_folder_delta(
                    delta_link=delta_link,
                    folder_id=folder_id,
                )

                logger.info(
                    "event=ingestion.outlook_delta_fetched "
                    "recruiter_id=%s count=%d bootstrap=%s",
                    recruiter_id,
                    len(delta_result.message_ids),
                    delta_link is None,
                )

                queued = 0
                skipped = 0

                for message_id in delta_result.message_ids:
                    was_queued = await self._handle_message(
                        recruiter_id=recruiter_id,
                        message_id=message_id,
                        provider="outlook",
                    )
                    if was_queued:
                        queued += 1
                    else:
                        skipped += 1

                await self._persist_outlook_sync(
                    recruiter_id=recruiter_id,
                    delta_link=delta_result.delta_link,
                    folder_id=delta_result.folder_id,
                )

                logger.info(
                    "event=ingestion.recruiter_complete "
                    "recruiter_id=%s provider=outlook "
                    "queued=%d skipped=%d delta_saved=true",
                    recruiter_id, queued, skipped,
                )

                current_access_token  = svc.get_current_access_token()
                current_refresh_token = svc.get_current_refresh_token()

        except OutlookFolderNotFoundError as exc:
            await self._clear_outlook_sync(recruiter_id)
            logger.error(
                "event=ingestion.outlook_folder_missing "
                "recruiter_id=%s email=%s detail=%s",
                recruiter_id, email, exc,
            )
            return

        if (
            current_access_token  != original_access_token
            or current_refresh_token != original_refresh_token
        ):
            await self._persist_refreshed_token(
                recruiter_id=recruiter_id,
                new_access_token=current_access_token,
                new_refresh_token=current_refresh_token,
            )

    async def _persist_outlook_sync(
        self,
        *,
        recruiter_id: str,
        delta_link:   str,
        folder_id:    str,
    ) -> None:
        """Persist the completed delta checkpoint on the recruiter document."""
        await self._db[_RECRUITERS_COLLECTION].update_one(
            {"recruiter_id": recruiter_id},
            {"$set": {
                "outlook_sync": {
                    "delta_link": delta_link,
                    "folder_id":  folder_id,
                },
                "updated_at": datetime.now(tz=timezone.utc),
            }},
        )
        logger.info(
            "event=ingestion.outlook_delta_saved recruiter_id=%s folder_id=%r",
            recruiter_id, folder_id,
        )

    async def _clear_outlook_sync(self, recruiter_id: str) -> None:
        """Clear cached Outlook delta state (folder missing or manual reset)."""
        await self._db[_RECRUITERS_COLLECTION].update_one(
            {"recruiter_id": recruiter_id},
            {"$set": {
                "outlook_sync": {
                    "delta_link": None,
                    "folder_id":  None,
                },
                "updated_at": datetime.now(tz=timezone.utc),
            }},
        )
        logger.warning(
            "event=ingestion.outlook_sync_cleared recruiter_id=%s",
            recruiter_id,
        )

    # ── Step 3: Deduplication + enqueue ──────────────────────────────────────

    async def _handle_message(
        self,
        *,
        recruiter_id: str,
        message_id:   str,
        provider:     str,
    ) -> bool:
        """
        Record a new message lifecycle and enqueue a worker job.

        MongoDB is the sole source of truth.  All dedup decisions are made
        against MongoDB.  Redis (RQ) is write-only from this method's
        perspective.

        Four-step flow
        --------------
        Step 1 — Guard:
            Raise immediately if ``self._queue`` is ``None``.  This prevents
            writing a lifecycle record that cannot be enqueued, conserving
            the cleanliness of the "Category A (job_id=null)" reconciliation
            path.  When the queue is ``None`` the scheduler already knows
            Redis is unavailable and the entire cycle should be deferred.

        Step 2 — Dedup check:
            Query ``processed_emails`` for an existing record on
            ``(message_id, recruiter_id)``.  Any existing record (any status)
            means the message is already claimed — return ``False``.

        Step 3 — Insert lifecycle record (pending, job_id=null):
            Write to MongoDB BEFORE the enqueue.  ``job_id=null`` means
            *enqueue not yet confirmed*.  A DuplicateKeyError here is a
            harmless race condition — another concurrent cycle claimed it first.

        Step 4 — Enqueue:
            Call ``self._queue.enqueue()``.  If this raises, the record stays
            at ``(pending, job_id=null)``.  Reconciliation will delete it after
            ``_UNCONFIRMED_PENDING_GRACE_MINUTES``.  The next cycle finds no
            record, re-inserts, and retries.  No duplicate enqueue is possible
            because the MongoDB record blocks the dedup check in Step 2.

        Step 5 — Confirm enqueue (update job_id):
            Update the record with ``job_id`` and ``enqueued_at``.  The record
            now reads ``(pending, job_id=set)`` — recognisably healthy.  If
            this update fails (extremely unlikely), the record stays
            ``job_id=null`` and reconciliation handles recovery.

        Why insert-first is correct
        ---------------------------
        Insert-first keeps the dedup gate entirely in MongoDB.  The unique
        compound index on ``(message_id, recruiter_id)`` guarantees that only
        one ingestion cycle can claim any given message.  Once the record
        exists, every subsequent cycle skips the message in Step 2 — no
        coordination with Redis is needed.

        Why enqueue-first is wrong
        --------------------------
        If enqueue succeeds before the MongoDB insert and the insert then
        fails, the dedup gate is not set.  The next cycle finds nothing and
        enqueues again — two RQ jobs for the same message.  This is a design
        defect even when downstream idempotency prevents a duplicate candidate.

        Args:
            recruiter_id: UUID of the owning recruiter.
            message_id:   Provider-native message ID.
            provider:     Email provider string (``"gmail"`` or ``"outlook"``).

        Returns:
            ``True``  — job was enqueued (or re-enqueued) successfully.
            ``False`` — message already accounted for; skipped safely.

        Raises:
            RuntimeError: ``self._queue`` is ``None`` (Redis unavailable).
            Exception:    Enqueue or MongoDB error — propagated to caller.
                          Outlook delta rounds must not catch these per message.
        """
        if self._queue is None:
            raise RuntimeError(
                "RQ queue is None — Redis is unavailable. "
                f"Cannot process message_id={message_id!r}. "
                "The scheduler will attempt reconnection on the next tick."
            )

        existing = await self._db[_PROCESSED_EMAILS_COLLECTION].find_one(
            {"message_id": message_id, "recruiter_id": recruiter_id},
            projection={"status": 1, "job_id": 1, "_id": 0},
        )

        if existing:
            if _is_delta_accounted_for(existing):
                logger.debug(
                    "event=ingestion.message_skipped "
                    "status=%r job_id=%r message_id=%s recruiter_id=%s",
                    existing.get("status"), existing.get("job_id"),
                    message_id, recruiter_id,
                )
                return False

            if existing.get("status") == ProcessedEmailStatus.failed.value:
                await self._db[_PROCESSED_EMAILS_COLLECTION].delete_one(
                    {"message_id": message_id, "recruiter_id": recruiter_id},
                )
                logger.info(
                    "event=ingestion.failed_record_recovered "
                    "message_id=%s recruiter_id=%s",
                    message_id, recruiter_id,
                )
            elif (
                existing.get("status") == ProcessedEmailStatus.pending.value
                and not existing.get("job_id")
            ):
                return await self._repair_pending_enqueue(
                    recruiter_id=recruiter_id,
                    message_id=message_id,
                    provider=provider,
                )

        now = datetime.now(tz=timezone.utc)
        try:
            await self._db[_PROCESSED_EMAILS_COLLECTION].insert_one({
                "message_id":    message_id,
                "recruiter_id":  recruiter_id,
                "provider":      provider,
                "status":        ProcessedEmailStatus.pending.value,
                "job_id":        None,
                "enqueued_at":   None,
                "processing_at": None,
                "processed_at":  None,
                "candidate_id":  None,
                "error":         None,
                "created_at":    now,
                "updated_at":    now,
            })
        except Exception as exc:
            err_str = str(exc)
            if "duplicate key" in err_str.lower() or "E11000" in err_str:
                logger.debug(
                    "event=ingestion.race_condition_skip "
                    "message_id=%s recruiter_id=%s",
                    message_id, recruiter_id,
                )
                return False
            raise

        logger.debug(
            "event=ingestion.lifecycle_inserted "
            "status=pending job_id=null "
            "message_id=%s recruiter_id=%s",
            message_id, recruiter_id,
        )

        return await self._confirm_enqueue(
            recruiter_id=recruiter_id,
            message_id=message_id,
            provider=provider,
        )

    async def _repair_pending_enqueue(
        self,
        *,
        recruiter_id: str,
        message_id:   str,
        provider:     str,
    ) -> bool:
        """
        Complete enqueue for a ``pending`` record whose ``job_id`` is null.

        Used when a prior cycle inserted the lifecycle record but failed before
        confirming the RQ job — required before advancing ``@odata.deltaLink``.
        """
        logger.info(
            "event=ingestion.pending_repair_started "
            "message_id=%s recruiter_id=%s",
            message_id, recruiter_id,
        )
        return await self._confirm_enqueue(
            recruiter_id=recruiter_id,
            message_id=message_id,
            provider=provider,
        )

    async def _confirm_enqueue(
        self,
        *,
        recruiter_id: str,
        message_id:   str,
        provider:     str,
    ) -> bool:
        """Enqueue the RQ job and write ``job_id`` + ``enqueued_at`` to MongoDB."""
        if self._queue is None:
            raise RuntimeError(
                "RQ queue is None — Redis is unavailable. "
                f"Cannot enqueue message_id={message_id!r}."
            )

        try:
            job = self._queue.enqueue(
                "app.workers.email_tasks.process_email",
                recruiter_id,
                message_id,
                provider,
                job_timeout=300,
                result_ttl=3600,
            )
        except Exception as exc:
            logger.error(
                "event=ingestion.enqueue_failed "
                "message_id=%s recruiter_id=%s detail=%s",
                message_id, recruiter_id, exc,
            )
            raise

        await self._db[_PROCESSED_EMAILS_COLLECTION].update_one(
            {"message_id": message_id, "recruiter_id": recruiter_id},
            {"$set": {
                "job_id":      job.id,
                "enqueued_at": datetime.now(tz=timezone.utc),
                "updated_at":  datetime.now(tz=timezone.utc),
            }},
        )

        logger.info(
            "event=ingestion.message_enqueued "
            "message_id=%s recruiter_id=%s provider=%s job_id=%s",
            message_id, recruiter_id, provider, job.id,
        )
        return True

    # ── Token persistence ─────────────────────────────────────────────────────

    async def _persist_refreshed_token(
        self,
        *,
        recruiter_id:      str,
        new_access_token:  str,
        new_refresh_token: str,
    ) -> None:
        """
        Re-encrypt and persist updated OAuth tokens to MongoDB.

        Called when either the access token or the refresh token (or both)
        has changed after a silent 401-refresh cycle.  For Microsoft/Outlook,
        the refresh token is rotated on every call; for Google/Gmail it stays
        the same, but both fields are always written together.

        Args:
            recruiter_id:      UUID of the recruiter to update.
            new_access_token:  The new access token from the provider.
            new_refresh_token: The current refresh token (may be rotated).
        """
        try:
            new_encrypted_blob = encrypt_oauth_tokens(
                new_access_token, new_refresh_token
            )
            result = await self._db[_RECRUITERS_COLLECTION].update_one(
                {"recruiter_id": recruiter_id},
                {
                    "$set": {
                        "oauth_tokens_encrypted": new_encrypted_blob,
                        "updated_at":             datetime.now(tz=timezone.utc),
                    }
                },
            )
            if result.matched_count == 0:
                logger.error(
                    "event=ingestion.token_persist_failed "
                    "recruiter_id=%s — document not found in DB.",
                    recruiter_id,
                )
            else:
                logger.info(
                    "event=ingestion.tokens_persisted recruiter_id=%s",
                    recruiter_id,
                )
        except Exception as exc:  # noqa: BLE001
            # Non-fatal: the token is still valid in-memory; the provider will
            # issue a new one on the next refresh cycle.
            logger.error(
                "event=ingestion.token_persist_error "
                "recruiter_id=%s detail=%s",
                recruiter_id, exc,
            )
