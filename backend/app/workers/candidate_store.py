"""
app/workers/candidate_store.py

Synchronous MongoDB service for candidate creation and index management.

This module runs inside the RQ worker process (no asyncio event loop).
All MongoDB operations use PyMongo (synchronous), never Motor.

Responsibilities:
  - ensure_candidate_indexes():  Bootstrap the unique index on email.message_id
  - find_candidate_by_message(): Idempotency check before insert
  - insert_candidate():          Single-document insert with duplicate detection

Design rules:
  - No FastAPI objects
  - No async code
  - No business logic — pure storage layer
  - Caller is responsible for constructing valid CandidateDocument instances
"""

from __future__ import annotations

import logging
from typing import Any

import pymongo
import pymongo.errors

from app.models.candidate import CandidateDocument

logger = logging.getLogger(__name__)

# ── Collection name ───────────────────────────────────────────────────────────
_CANDIDATES_COLLECTION = "candidates"


# ══════════════════════════════════════════════════════════════════════════════
# Index bootstrap
# ══════════════════════════════════════════════════════════════════════════════

def ensure_candidate_indexes(db: pymongo.database.Database) -> None:
    """
    Create required indexes on the ``candidates`` collection.

    Idempotent — safe to call on every worker startup.

    Indexes created:
      - Unique index on ``candidate_id``: primary key for point lookups on
        GET /candidates/{candidate_id} and ATS score fetch. Enforces the
        UUID4 uniqueness invariant at the DB layer.
      - Unique index on ``email.message_id``: prevents duplicate candidate
        documents for the same Gmail message ID across any recruiter.
      - Index on ``recruiter_id``: accelerates per-recruiter queries.
      - Index on ``processing.parsed``: allows the parsing queue to efficiently
        fetch unprocessed candidates.

    Args:
        db: Synchronous PyMongo database handle.
    """
    col = db[_CANDIDATES_COLLECTION]

    # Primary key index — UUID4 is globally unique by construction.
    # Enables O(1) point lookup for GET /candidates/{candidate_id} and the
    # ATS score ownership check. Also enforces the uniqueness invariant at
    # the DB layer as a safety net.
    col.create_index(
        [("candidate_id", pymongo.ASCENDING)],
        unique=True,
        name="uq_candidate_id",
    )
    col.create_index(
        [("email.message_id", pymongo.ASCENDING)],
        unique=True,
        name="uq_email_message_id",
    )
    col.create_index(
        [("recruiter_id", pymongo.ASCENDING)],
        name="idx_recruiter_id",
    )
    col.create_index(
        [("processing.parsed", pymongo.ASCENDING)],
        name="idx_processing_parsed",
    )

    logger.info(
        "Candidate collection indexes ensured on '%s'.", _CANDIDATES_COLLECTION
    )


# ══════════════════════════════════════════════════════════════════════════════
# Query helpers
# ══════════════════════════════════════════════════════════════════════════════

def find_candidate_by_message_id(
    db:         pymongo.database.Database,
    message_id: str,
) -> dict[str, Any] | None:
    """
    Return an existing candidate document whose ``email.message_id`` matches.

    Used for the idempotency check before insertion.

    Args:
        db:         Synchronous PyMongo database handle.
        message_id: Gmail API message ID.

    Returns:
        The first matching document (projection: ``candidate_id`` only),
        or ``None`` if no match exists.
    """
    return db[_CANDIDATES_COLLECTION].find_one(
        {"email.message_id": message_id},
        projection={"candidate_id": 1, "_id": 0},
    )


# ══════════════════════════════════════════════════════════════════════════════
# Write operations
# ══════════════════════════════════════════════════════════════════════════════

def insert_candidate(
    db:        pymongo.database.Database,
    candidate: CandidateDocument,
) -> str:
    """
    Insert a new candidate document into MongoDB.

    Idempotency:
        If a document with the same ``email.message_id`` already exists
        (unique index violation), this function returns the existing
        ``candidate_id`` rather than raising an exception.  This is the
        only safe way to handle RQ job retries without manual dedup logic
        at the call site.

    Args:
        db:        Synchronous PyMongo database handle.
        candidate: Fully-populated :class:`~app.models.candidate.CandidateDocument`.

    Returns:
        The ``candidate_id`` of the inserted (or already-existing) document.

    Raises:
        pymongo.errors.PyMongoError: For any error other than a duplicate key.
    """
    doc = candidate.to_mongo_dict()

    try:
        db[_CANDIDATES_COLLECTION].insert_one(doc)
        logger.info(
            "Candidate inserted: candidate_id=%s message_id=%s",
            candidate.candidate_id,
            candidate.email.message_id,
        )
        return candidate.candidate_id

    except pymongo.errors.DuplicateKeyError:
        # The unique index on email.message_id fired — document already exists.
        # Fetch and return the existing candidate_id so the caller can update
        # processed_emails with the correct reference.
        logger.warning(
            "Duplicate candidate for message_id=%s — fetching existing candidate_id.",
            candidate.email.message_id,
        )
        existing = find_candidate_by_message_id(db, candidate.email.message_id)
        if existing:
            return existing["candidate_id"]

        # Edge case: index fired but find_one returned nothing (very unlikely).
        # Propagate so the worker marks the job as failed rather than silently
        # returning an empty string.
        raise RuntimeError(
            f"DuplicateKeyError for message_id={candidate.email.message_id!r} "
            "but no existing document found — index may be inconsistent."
        )
