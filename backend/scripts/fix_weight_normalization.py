"""
scripts/fix_weight_normalization.py

One-time migration script: normalize LLM weight sums for existing jobs.

Problem
-------
The JD analyzer LLM frequently returns weights that do not sum to 100 (e.g.
DA002 had weights summing to 115 while "total_weight" was set to 100).
This inflates ATS scores and causes a fake 100/100 ceiling effect.

What this script does
---------------------
1. Finds all jobs where ``jd_analysis.status == "completed"``.
2. For each job, computes the actual sum of all requirement weights.
3. If sum == 100 → prints "OK", skips.
4. If sum != 100 → normalizes via ``normalize_jd_weights()`` (same function
   used by ``jd_tasks.py`` in production — no duplicate logic).
5. Writes the corrected ``jd_analysis.result`` back to MongoDB.
6. Bumps ``jd_analysis.version`` by 1 via ``$inc``.
   This automatically invalidates all existing ATS scores for that job:
   ``ats_tasks.py`` skips candidates only when their stored
   ``jd_analysis_version`` matches the current version.  After the bump,
   every existing score is "stale" and the next ``calculate-ats`` call
   re-scores them correctly.

Usage
-----
    # Safe preview — prints what would change, writes nothing:
    python scripts/fix_weight_normalization.py --dry-run

    # Apply fix to all jobs:
    python scripts/fix_weight_normalization.py

    # Apply fix to a single job only:
    python scripts/fix_weight_normalization.py --job-id DA002

    # Dry-run for a single job:
    python scripts/fix_weight_normalization.py --dry-run --job-id DA002

Environment
-----------
Reads MONGODB_URL and MONGODB_DB_NAME from environment (or .env file if
python-dotenv is installed).

Run order
---------
1. python scripts/fix_weight_normalization.py --dry-run   ← verify output
2. python scripts/fix_weight_normalization.py             ← apply
3. Deploy jd_tasks.py changes (if not already deployed)
4. Recruiters re-trigger calculate-ats on their jobs

IMPORTANT: Run this BEFORE triggering any new ATS runs to avoid scoring
candidates against still-corrupt weights during the deployment window.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from typing import Any

import pymongo

# ── Import the shared normalizer — same code as production ───────────────────
# This is the entire point: one function, used everywhere.
# If you find yourself copying the algorithm here, stop and refactor instead.
from app.services.jd_weight_normalizer import normalize_jd_weights

# ── Optional: load .env if python-dotenv is available ───────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed — rely on real env vars


# ════════════════════════════════════════════════════════════════════════════
# DB connection
# ════════════════════════════════════════════════════════════════════════════

def _get_db() -> pymongo.database.Database:
    url     = os.environ.get("MONGODB_URL")
    db_name = os.environ.get("MONGODB_DB_NAME")

    if not url:
        print("ERROR: MONGODB_URL environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    if not db_name:
        print("ERROR: MONGODB_DB_NAME environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    client = pymongo.MongoClient(url)
    return client[db_name]


# ════════════════════════════════════════════════════════════════════════════
# Core migration logic
# ════════════════════════════════════════════════════════════════════════════

def run_migration(dry_run: bool, job_id_filter: str | None) -> None:
    """
    Scan completed jobs, normalize weight sums, bump jd_analysis.version.

    Args:
        dry_run:       If True, print what would change but write nothing.
        job_id_filter: If set, only process this specific job_id.
    """
    db = _get_db()
    col = db["jobs"]

    # Build query
    query: dict[str, Any] = {"jd_analysis.status": "completed"}
    if job_id_filter:
        query["job_id"] = job_id_filter.upper()

    jobs = list(col.find(query, {"_id": 0}))

    print(f"\n{'[DRY RUN] ' if dry_run else ''}Scanning {len(jobs)} completed job(s)...\n")

    scanned   = 0
    corrected = 0
    ok        = 0
    errors    = 0

    for job in jobs:
        scanned += 1
        job_id       = job.get("job_id", "?")
        recruiter_id = job.get("recruiter_id", "?")
        jd_meta      = job.get("jd_analysis") or {}
        result       = jd_meta.get("result")
        version      = jd_meta.get("version", 0)

        if not result:
            print(f"  SKIP  {job_id} — jd_analysis.result is empty")
            continue

        # Compute actual sum
        critical  = result.get("critical_requirements",  [])
        secondary = result.get("secondary_requirements", [])
        actual_sum = sum(r.get("weight", 0) for r in critical + secondary)

        if actual_sum == 100:
            print(f"  OK    {job_id}  (sum={actual_sum}, version={version})")
            ok += 1
            continue

        # Needs correction
        try:
            corrected_result = normalize_jd_weights(result, job_id)
        except ValueError as exc:
            print(f"  ERROR {job_id}  (sum={actual_sum}) — cannot normalize: {exc}")
            errors += 1
            continue

        new_sum = sum(
            r.get("weight", 0)
            for r in (
                corrected_result.get("critical_requirements",  [])
                + corrected_result.get("secondary_requirements", [])
            )
        )
        new_version = version + 1

        print(
            f"  FIX   {job_id}  "
            f"sum {actual_sum} → {new_sum}  "
            f"version {version} → {new_version}"
            + ("  [DRY RUN — not written]" if dry_run else "")
        )

        if not dry_run:
            now = datetime.now(tz=timezone.utc)
            col.update_one(
                {"job_id": job_id, "recruiter_id": recruiter_id},
                {
                    "$set": {
                        "jd_analysis.result":      corrected_result,
                        "jd_analysis.analyzed_at": now,
                        "updated_at":              now,
                    },
                    "$inc": {
                        # Bumping version makes all existing candidate_job_scores
                        # for this job "version-stale".  ats_tasks.py re-scores
                        # them on the next calculate-ats call automatically.
                        "jd_analysis.version": 1,
                    },
                },
            )

        corrected += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'=' * 55}")
    print(f"{'[DRY RUN] ' if dry_run else ''}Migration complete.")
    print(f"  Scanned:   {scanned}")
    print(f"  Already OK:{ok}")
    print(f"  Corrected: {corrected}" + (" (not written)" if dry_run else ""))
    print(f"  Errors:    {errors}")

    if corrected > 0 and not dry_run:
        print(
            "\nNext step: recruiters should re-trigger calculate-ats on "
            "their jobs to get corrected scores."
        )


# ════════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize JD weight sums for existing completed jobs."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change without writing to MongoDB.",
    )
    parser.add_argument(
        "--job-id",
        metavar="JOB_ID",
        default=None,
        help="Process only this specific job_id (e.g. DA002).",
    )
    args = parser.parse_args()

    run_migration(dry_run=args.dry_run, job_id_filter=args.job_id)


if __name__ == "__main__":
    main()
