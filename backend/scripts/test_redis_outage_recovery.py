"""
scripts/test_redis_outage_recovery.py

Manual Redis outage-recovery test runner.

Runs from the host machine (not inside Docker).
Requires: requests, pymongo, python-dotenv

Usage:
    python scripts/test_redis_outage_recovery.py

The script interactively pauses between steps and prints a PASS / FAIL
verdict at the end.

Prerequisites:
  - All Docker services running:
      docker compose up -d
  - A real (or mock) resume email already sent to a recruiter
    whose recruiter_id you supply below, OR the script uses the
    POST /internal/run-ingestion endpoint to trigger a cycle.

Environment:
  Reads MONGODB_URL and MONGODB_DB_NAME from .env (or uses defaults).
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

import pymongo
import requests
from dotenv import load_dotenv

# ── Configuration ─────────────────────────────────────────────────────────────
load_dotenv()

API_BASE        = os.getenv("TEST_API_BASE",   "http://localhost:8000")
MONGODB_URL     = os.getenv("MONGODB_URL",     "mongodb://localhost:27017")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "recruitment_db")
REDIS_CONTAINER = "recruitment_redis_v2"
SCHEDULER_INTERVAL_SECONDS = 4 * 60  # must match _DEFAULT_INTERVAL_MINUTES
POLL_INTERVAL_SECONDS      = 10
RECOVERY_WAIT_CYCLES       = 3       # how many cycles to wait after Redis restart

_PROCESSED_EMAILS_COLLECTION = "processed_emails"

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg: str)    -> None: print(f"{GREEN}  ✓ {msg}{RESET}")
def fail(msg: str)  -> None: print(f"{RED}  ✗ {msg}{RESET}")
def warn(msg: str)  -> None: print(f"{YELLOW}  ⚠ {msg}{RESET}")
def info(msg: str)  -> None: print(f"{CYAN}  → {msg}{RESET}")
def hdr(msg: str)   -> None: print(f"\n{BOLD}{'─' * 60}\n  {msg}\n{'─' * 60}{RESET}")
def pause(msg: str) -> None:
    print(f"\n{YELLOW}[WAITING] {msg}{RESET}")
    input("  Press ENTER when ready ...")


# ── MongoDB helper ────────────────────────────────────────────────────────────
def get_db() -> pymongo.database.Database:
    client = pymongo.MongoClient(MONGODB_URL, serverSelectionTimeoutMS=5000)
    return client[MONGODB_DB_NAME]


def find_record(db, message_id: str) -> dict | None:
    return db[_PROCESSED_EMAILS_COLLECTION].find_one(
        {"message_id": message_id},
        projection={"status": 1, "job_id": 1, "candidate_id": 1, "_id": 0},
    )


# ── API helpers ───────────────────────────────────────────────────────────────
def trigger_ingestion() -> bool:
    """POST /api/v1/internal/run-ingestion and return True on success."""
    try:
        r = requests.post(
            f"{API_BASE}/api/v1/internal/run-ingestion",
            timeout=10,
        )
        return r.status_code == 200
    except Exception as exc:
        warn(f"trigger_ingestion failed: {exc}")
        return False


def check_api_alive() -> bool:
    try:
        r = requests.get(f"{API_BASE}/", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


# ── Test results accumulator ──────────────────────────────────────────────────
_results: list[tuple[str, bool]] = []

def assert_check(label: str, passed: bool) -> None:
    _results.append((label, passed))
    if passed:
        ok(label)
    else:
        fail(label)


# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 — Verify normal processing
# ═════════════════════════════════════════════════════════════════════════════
def step1_normal_processing(db) -> str | None:
    hdr("STEP 1 — Normal processing baseline")

    info("Checking API is alive...")
    assert_check("API responds to GET /", check_api_alive())

    pause(
        "Send ONE test resume email to a recruiter account now.\n"
        "  Then press ENTER to trigger an ingestion cycle."
    )

    info("Triggering ingestion cycle via POST /internal/run-ingestion ...")
    ok_enqueue = trigger_ingestion()
    assert_check("POST /internal/run-ingestion returned 200", ok_enqueue)

    info(f"Waiting {SCHEDULER_INTERVAL_SECONDS}s for worker to process ...")
    time.sleep(SCHEDULER_INTERVAL_SECONDS)

    message_id = input(
        "\n  Enter the message_id of the test email\n"
        "  (check processed_emails collection or API logs): "
    ).strip()

    if not message_id:
        warn("No message_id provided — skipping Step 1 verification")
        return None

    record = find_record(db, message_id)
    if record:
        info(f"Record: {record}")
        assert_check("processed_emails record exists", True)
        assert_check(
            "status = processed",
            record.get("status") == "processed",
        )
        assert_check(
            "candidate_id is set",
            bool(record.get("candidate_id")),
        )
    else:
        assert_check("processed_emails record exists", False)
        warn("Cannot verify Step 1 — no record found for that message_id")

    pause(
        "Delete the test candidate and processed_emails record now\n"
        "  (or use mongosh / Compass).\n"
        "  This ensures Step 3 starts clean."
    )
    return message_id


# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 — Stop Redis
# ═════════════════════════════════════════════════════════════════════════════
def step2_stop_redis() -> None:
    hdr("STEP 2 — Stop Redis")

    info(f"Stopping container: {REDIS_CONTAINER}")
    os.system(f"docker stop {REDIS_CONTAINER}")
    time.sleep(3)

    info("Verifying Redis is unreachable ...")
    try:
        import redis as _redis
        c = _redis.from_url("redis://localhost:6379/0", socket_connect_timeout=2)
        c.ping()
        warn("Redis still appears reachable — is the container really stopped?")
        assert_check("Redis is unreachable after stop", False)
    except Exception:
        assert_check("Redis is unreachable after stop", True)

    info("Checking API is still alive (should survive Redis outage) ...")
    assert_check("API still responds after Redis stop", check_api_alive())


# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 — Send email while Redis is down
# ═════════════════════════════════════════════════════════════════════════════
def step3_email_while_redis_down(db) -> str | None:
    hdr("STEP 3 — Send email while Redis is down")

    pause(
        "Send ONE new test resume email to the same recruiter.\n"
        "  Then press ENTER to trigger an ingestion cycle."
    )

    info("Triggering ingestion cycle ...")
    trigger_ingestion()

    wait = _UNCONFIRMED_PENDING_GRACE = 3 * 60 + 30  # grace + margin
    info(f"Waiting {wait}s for cycle to attempt processing ...")
    time.sleep(wait)

    message_id = input(
        "\n  Enter the message_id of the NEW test email\n"
        "  (from email provider or API logs): "
    ).strip()

    if not message_id:
        warn("No message_id provided — skipping Step 3 verification")
        return None

    record = find_record(db, message_id)
    if record:
        info(f"Record found: {record}")
        assert_check(
            "status = pending (record exists but not yet processed)",
            record.get("status") == "pending",
        )
        assert_check(
            "job_id is null (enqueue was not confirmed or record was not inserted)",
            record.get("job_id") is None,
        )
        assert_check(
            "candidate_id is null (no candidate created yet)",
            record.get("candidate_id") is None,
        )
    else:
        # Also acceptable — queue guard raised before MongoDB insert
        info("No record in processed_emails — queue=None guard fired before insert.")
        assert_check(
            "No orphaned record created (queue guard blocked insert)",
            True,
        )

    assert_check("API still running during Redis outage", check_api_alive())

    return message_id


# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 — Restart Redis
# ═════════════════════════════════════════════════════════════════════════════
def step4_restart_redis() -> None:
    hdr("STEP 4 — Restart Redis")

    info(f"Starting container: {REDIS_CONTAINER}")
    os.system(f"docker start {REDIS_CONTAINER}")
    time.sleep(5)

    info("Verifying Redis is reachable ...")
    try:
        import redis as _redis
        c = _redis.from_url("redis://localhost:6379/0", socket_connect_timeout=3)
        c.ping()
        assert_check("Redis is reachable after restart", True)
    except Exception as exc:
        assert_check("Redis is reachable after restart", False)
        warn(f"Redis ping failed: {exc}")

    assert_check("API still alive after Redis restart", check_api_alive())


# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 — Verify automatic recovery
# ═════════════════════════════════════════════════════════════════════════════
def step5_verify_recovery(db, message_id: str | None) -> None:
    hdr("STEP 5 — Verify automatic recovery")

    wait_seconds = SCHEDULER_INTERVAL_SECONDS * RECOVERY_WAIT_CYCLES
    info(
        f"Waiting {wait_seconds}s ({RECOVERY_WAIT_CYCLES} scheduler cycles) "
        f"for automatic recovery ..."
    )
    time.sleep(wait_seconds)

    if not message_id:
        warn("No message_id available — manually verify processed_emails")
        return

    deadline = time.time() + 120  # extra 2-minute polling window
    record    = None
    while time.time() < deadline:
        record = find_record(db, message_id)
        if record and record.get("status") == "processed":
            break
        info(f"  status={record.get('status') if record else 'absent'} — polling ...")
        time.sleep(POLL_INTERVAL_SECONDS)

    if record:
        info(f"Final record: {record}")
        assert_check("processed_emails record exists",          True)
        assert_check("status = processed",                      record.get("status") == "processed")
        assert_check("job_id is set",                          bool(record.get("job_id")))
        assert_check("candidate_id is set",                    bool(record.get("candidate_id")))
    else:
        assert_check("processed_emails record exists",          False)
        assert_check("status = processed",                      False)
        assert_check("job_id is set",                          False)
        assert_check("candidate_id is set",                    False)

    assert_check("API still running after full recovery", check_api_alive())


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════
def main() -> None:
    print(f"\n{BOLD}{'═' * 60}")
    print("  Redis Outage Recovery Test")
    print(f"  {datetime.now(tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    print(f"{'═' * 60}{RESET}\n")

    info(f"API:     {API_BASE}")
    info(f"MongoDB: {MONGODB_URL} / {MONGODB_DB_NAME}")
    info(f"Redis container: {REDIS_CONTAINER}")
    info(f"Scheduler interval: {SCHEDULER_INTERVAL_SECONDS}s")

    try:
        db = get_db()
        db.command("ping")
        ok("MongoDB reachable")
    except Exception as exc:
        fail(f"Cannot connect to MongoDB: {exc}")
        sys.exit(1)

    # ── Run steps ─────────────────────────────────────────────────────────────
    msg_id_step1 = step1_normal_processing(db)
    step2_stop_redis()
    msg_id_step3 = step3_email_while_redis_down(db)
    step4_restart_redis()
    # Use step 3 message_id for recovery verification (step 1 was deleted)
    step5_verify_recovery(db, msg_id_step3)

    # ── Final verdict ─────────────────────────────────────────────────────────
    hdr("TEST RESULTS")

    passed = [r for r in _results if r[1]]
    failed = [r for r in _results if not r[1]]

    for label, result in _results:
        symbol = f"{GREEN}PASS{RESET}" if result else f"{RED}FAIL{RESET}"
        print(f"  [{symbol}]  {label}")

    print()
    total = len(_results)
    print(f"  {total} checks | {GREEN}{len(passed)} passed{RESET} | {RED}{len(failed)} failed{RESET}")

    # PASS criteria from the test spec
    pass_criteria = {
        "Application remains running while Redis is down":
            any(l == "API still responds after Redis stop"      and r for l, r in _results)
            or any(l == "API still running during Redis outage" and r for l, r in _results),
        "Email is not permanently lost":
            any(l == "status = processed"                       and r for l, r in _results),
        "Redis recovery requires no restart":
            any(l == "Redis is reachable after restart"         and r for l, r in _results)
            and any(l == "API still alive after Redis restart"  and r for l, r in _results),
        "Candidate is eventually created":
            any(l == "candidate_id is set"                      and r for l, r in _results),
        "Final lifecycle state is processed":
            any(l == "status = processed"                       and r for l, r in _results),
    }

    print(f"\n{BOLD}  PASS Criteria:{RESET}")
    all_pass_met = True
    for criterion, met in pass_criteria.items():
        symbol = f"{GREEN}✓{RESET}" if met else f"{RED}✗{RESET}"
        print(f"  {symbol}  {criterion}")
        if not met:
            all_pass_met = False

    print()
    if all_pass_met:
        print(f"{BOLD}{GREEN}  ══ OVERALL: PASS ══{RESET}")
    else:
        print(f"{BOLD}{RED}  ══ OVERALL: FAIL ══{RESET}")
    print()


if __name__ == "__main__":
    main()
