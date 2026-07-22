"""
scripts/migrate_provider_field.py

Phase 0 — Database migration: backfill ``provider`` field on all existing
recruiter documents.

Every existing recruiter was connected via Gmail, so the correct default is
``"gmail"``.  This script uses ``$setOnInsert``-style ``updateMany`` with
``{ $exists: false }`` so it is fully idempotent — safe to run on a live
production database and to re-run at any time without side effects.

Usage
-----
Run from the project root (same directory as Dockerfile / docker-compose.yml):

    # With the .env file already populated:
    python -m scripts.migrate_provider_field

    # Or inside the Docker container:
    docker compose exec backend python -m scripts.migrate_provider_field

Exit codes
----------
0 — Migration succeeded (or was already complete).
1 — Fatal error; see output for details.

Verification
------------
After running, confirm the result with:

    db.recruiters.find({ provider: { $exists: false } }).count()

The result must be 0 before proceeding to Phase 1 deployment.
"""

from __future__ import annotations

import logging
import sys

import pymongo
from pymongo.errors import ConnectionFailure, OperationFailure

from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

_COLLECTION = "recruiters"
_PROVIDER_DEFAULT = "gmail"


def run_migration() -> int:
    """
    Execute the provider field backfill migration.

    Returns:
        0 on success, 1 on any fatal error.
    """
    settings = get_settings()

    logger.info("=" * 60)
    logger.info("Phase 0 — Recruiter provider field migration")
    logger.info("Target DB:  %s", settings.MONGODB_URL)
    logger.info("Database:   %s", settings.MONGODB_DB_NAME)
    logger.info("=" * 60)

    # ── Connect ───────────────────────────────────────────────────────────────
    try:
        client: pymongo.MongoClient = pymongo.MongoClient(
            settings.MONGODB_URL,
            serverSelectionTimeoutMS=5_000,
        )
        # Force a connection to validate the URI
        client.admin.command("ping")
    except ConnectionFailure as exc:
        logger.error("Cannot connect to MongoDB: %s", exc)
        return 1

    db = client[settings.MONGODB_DB_NAME]
    collection = db[_COLLECTION]

    # ── Pre-migration audit ───────────────────────────────────────────────────
    try:
        total_recruiters: int = collection.count_documents({})
        missing_provider: int = collection.count_documents(
            {"provider": {"$exists": False}}
        )
    except OperationFailure as exc:
        logger.error("Failed to count documents: %s", exc)
        client.close()
        return 1

    logger.info("Total recruiter documents:   %d", total_recruiters)
    logger.info("Missing provider field:       %d", missing_provider)

    if missing_provider == 0:
        logger.info(
            "All recruiter documents already have the provider field. "
            "Nothing to migrate. ✓"
        )
        client.close()
        return 0

    # ── Apply migration ───────────────────────────────────────────────────────
    logger.info(
        "Applying: db.recruiters.updateMany("
        "{ provider: { $exists: false } }, "
        '{ $set: { provider: "%s" } })',
        _PROVIDER_DEFAULT,
    )

    try:
        result = collection.update_many(
            filter={"provider": {"$exists": False}},
            update={"$set": {"provider": _PROVIDER_DEFAULT}},
        )
    except OperationFailure as exc:
        logger.error("updateMany failed: %s", exc)
        client.close()
        return 1

    logger.info(
        "updateMany complete — matched=%d modified=%d",
        result.matched_count,
        result.modified_count,
    )

    # ── Post-migration verification ───────────────────────────────────────────
    try:
        remaining: int = collection.count_documents(
            {"provider": {"$exists": False}}
        )
    except OperationFailure as exc:
        logger.error("Post-migration count failed: %s", exc)
        client.close()
        return 1

    if remaining != 0:
        logger.error(
            "Verification FAILED — %d document(s) still missing the provider "
            "field. Investigate before deploying Phase 1.",
            remaining,
        )
        client.close()
        return 1

    logger.info("Verification PASSED — 0 documents missing provider field. ✓")
    logger.info("=" * 60)
    logger.info("Phase 0 migration complete. Safe to deploy Phase 1.")
    logger.info("=" * 60)

    client.close()
    return 0


if __name__ == "__main__":
    sys.exit(run_migration())
