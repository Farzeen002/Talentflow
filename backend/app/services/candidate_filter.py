"""
app/services/candidate_filter.py

Shared candidate filter query builder.

This module is the single source of truth for ALL candidate visibility and
filter queries used across the system.  It is intentionally driver-agnostic
at the query-building level:

    build_active_candidate_query()   → base visibility filter (recruiter + not blacklisted)
    build_candidate_filter_query()   → full filter (visibility base + job screening criteria)
    get_filtered_candidates_sync()   → thin sync wrapper (PyMongo, for RQ workers)

The async path (Motor, used by job_service.py) calls these functions directly
and drives its own Motor cursor — no async wrapper lives here because this
module must remain importable by RQ worker processes (no asyncio event loop).

Why a dedicated module?
-----------------------
Both ``job_service.py`` (candidate list API) and ``ats_tasks.py`` (ATS worker)
must apply identical filter criteria.  Any future change to visibility or
screening criteria requires a single edit here — both paths update automatically.

Blacklist exclusion rule
------------------------
``build_active_candidate_query()`` is the **universal visibility gate**.
Every query that returns a list or count of candidates MUST start with this
function.  This ensures that blacklisted candidates are automatically excluded
from:
  - ``GET /jobs/{job_id}/candidates``  (all views and counts)
  - ``GET /jobs/{job_id}/candidates?view=all``
  - ATS LLM scoring pipeline  (no tokens wasted on fake candidates)
  - Job candidate counts shown on every job card

Single-document detail lookups (``GET /candidates/{id}``, resume URL, ATS
score) intentionally do NOT use this helper — the recruiter must be able to
review a blacklisted profile to confirm or undo the decision.

Design rules:
  - No FastAPI objects
  - No Motor (async) imports
  - No business logic beyond query construction
  - No ATS-specific code
"""

from __future__ import annotations

import logging
from typing import Any

import pymongo
import pymongo.database

logger = logging.getLogger(__name__)

# ── Collection name ───────────────────────────────────────────────────────────
_CANDIDATES_COL = "candidates"


# ══════════════════════════════════════════════════════════════════════════════
# Pure query builders — single source of truth
# ══════════════════════════════════════════════════════════════════════════════

def build_active_candidate_query(recruiter_id: str) -> dict[str, Any]:
    """
    Return the base MongoDB filter for **all** candidate visibility queries.

    This is the single source of truth for candidate visibility.  Every query
    that returns a list or count of candidates MUST start with this function.

    A candidate is visible iff:
      1. It belongs to the authenticated recruiter (``recruiter_id`` match).
      2. It is not currently blacklisted (``blacklist.is_blacklisted != True``).

    The ``$ne: True`` operator correctly handles two cases:
      - Documents where ``blacklist.is_blacklisted = False``  → visible.
      - Legacy documents where the ``blacklist`` field is absent  → visible.
        (No backfill migration needed for existing candidates.)

    Args:
        recruiter_id: UUID of the authenticated recruiter (from JWT).

    Returns:
        A partial MongoDB filter dict.  Callers add job-specific criteria
        on top (e.g. ``{**build_active_candidate_query(rid), "job_id": jid}``).
    """
    return {
        "recruiter_id":             recruiter_id,
        "blacklist.is_blacklisted": {"$ne": True},
    }


def build_candidate_filter_query(
    job_id:       str,
    recruiter_id: str,
    filters:      dict[str, Any],
) -> dict[str, Any]:
    """
    Build a MongoDB filter dict for candidates that pass all job thresholds.

    Starts from ``build_active_candidate_query()`` (visibility base) and adds
    the job-specific screening criteria on top.  This is the single source of
    truth consumed by:
      - ``job_service._filtered_query()``  → API candidate list (view=filtered)
      - ``get_filtered_candidates_sync()``  → ATS background worker

    Blacklist exclusion is inherited from the visibility base — blacklisted
    candidates are automatically skipped by the ATS pipeline with zero extra
    code in the worker.

    Null-safety rules (mirrors original behaviour):
      - Boolean equality (``is_ok_client``, ``is_c2h_ok``, ``has_pf_account``)
        naturally excludes null / missing QA fields in MongoDB.
      - ``notice_period_days`` uses ``$ne: None`` to explicitly exclude candidates
        whose notice period was not extracted, plus ``$lte`` for the threshold.

    Args:
        job_id:       Naukri job code (already upper-cased by caller).
        recruiter_id: UUID of the owning recruiter — enforces data isolation.
        filters:      Raw ``job.filters`` dict from MongoDB.  Expected keys:
                        is_ok_client, is_c2h_ok, has_pf_account,
                        max_notice_period_days.

    Returns:
        A dict suitable for ``collection.find()``, ``count_documents()``, etc.
    """
    return {
        **build_active_candidate_query(recruiter_id),   # visibility base (recruiter + not blacklisted)
        "job_id":                  job_id,
        "qa.is_ok_client":         filters["is_ok_client"],
        "qa.is_c2h_ok":            filters["is_c2h_ok"],
        "qa.has_pf_account":       filters["has_pf_account"],
        "qa.notice_period_days":   {
            "$ne":  None,
            "$lte": filters["max_notice_period_days"],
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Sync wrapper — for use inside RQ worker processes
# ══════════════════════════════════════════════════════════════════════════════

def get_filtered_candidates_sync(
    db:           pymongo.database.Database,
    job_id:       str,
    recruiter_id: str,
    filters:      dict[str, Any],
    projection:   dict[str, int],
) -> list[dict[str, Any]]:
    """
    Fetch all filtered candidates using a synchronous PyMongo connection.

    Intended for use inside RQ worker tasks (no asyncio event loop).
    Returns the full result set — no pagination — because background workers
    process every candidate in the batch sequentially.

    Args:
        db:           Synchronous PyMongo database handle (from _get_sync_db()).
        job_id:       Naukri job code (upper-cased).
        recruiter_id: UUID of the owning recruiter.
        filters:      Raw ``job.filters`` dict from the job document.
        projection:   MongoDB projection dict (which fields to include/exclude).

    Returns:
        List of raw candidate dicts matching the filter criteria.
        Empty list if no candidates match.
    """
    query = build_candidate_filter_query(job_id, recruiter_id, filters)
    candidates = list(db[_CANDIDATES_COL].find(query, projection))

    logger.info(
        "event=candidate_filter.sync_fetch job_id=%s recruiter_id=%s count=%d",
        job_id, recruiter_id, len(candidates),
    )
    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# ATS index bootstrap
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_ats_indexes(db: Any) -> None:
    """
    Create required indexes on ``candidate_job_scores`` and ``candidates``.

    Idempotent — safe to call on every application startup.
    Uses Motor (async) because this is called from FastAPI's lifespan handler.

    Indexes created on ``candidate_job_scores``:
      - Compound unique on (candidate_id, job_id):
            Primary key for this collection. Enforces one score record per
            candidate × job pair. Enables idempotent upserts in the worker.
      - (job_id, score DESC):
            Fast retrieval of all scores for a job, pre-sorted by score.
            Used for future ranking/leaderboard views.
      - (job_id, recruiter_id):
            Recruiter-scoped lookups when merging scores into candidate list.

    Indexes created on ``candidates``:
      - Compound (recruiter_id, blacklist.is_blacklisted) sparse:
            Supports ``build_active_candidate_query()`` which is the visibility
            base for every candidate list and count query.  ``sparse=True``
            excludes legacy documents that pre-date the blacklist field —
            no false positives, no index bloat on the existing dataset.

    Args:
        db: Motor AsyncIOMotorDatabase instance.
    """
    from pymongo import ASCENDING, DESCENDING

    # ── candidate_job_scores indexes ──────────────────────────────────────────
    scores_col = db["candidate_job_scores"]

    await scores_col.create_index(
        [("candidate_id", ASCENDING), ("job_id", ASCENDING)],
        unique=True,
        name="uq_candidate_job_score",
    )
    await scores_col.create_index(
        [("job_id", ASCENDING), ("score", DESCENDING)],
        name="idx_scores_job_score",
    )
    await scores_col.create_index(
        [("job_id", ASCENDING), ("recruiter_id", ASCENDING)],
        name="idx_scores_job_recruiter",
    )

    # ── candidates indexes ──────────────────────────────────────────────────
    candidates_col = db["candidates"]

    await candidates_col.create_index(
        [("recruiter_id", ASCENDING), ("blacklist.is_blacklisted", ASCENDING)],
        sparse=True,
        name="idx_candidates_recruiter_blacklist",
    )

    logger.info(
        "MongoDB indexes ensured on 'candidate_job_scores' and 'candidates' collections."
    )
