"""
app/api/internal.py

Internal/admin API endpoints.

Routes:
  POST /internal/run-ingestion → Manually trigger one ingestion cycle

⚠️  These endpoints are NOT protected by JWT in the current implementation.
    Before deploying to production, add one of:
      - Network-level restriction (private subnet / VPN only)
      - A static shared secret in the Authorization header
      - IP allowlist middleware

All routes are thin — they delegate immediately to the scheduler's
``trigger_now()`` method and return its result verbatim.
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal", tags=["Internal"])


@router.post(
    "/run-ingestion",
    summary="Manually trigger one ingestion cycle",
    description=(
        "Fires the email ingestion pipeline immediately, outside the normal "
        "scheduled interval.  Returns ``{'status': 'started'}`` if the cycle "
        "was launched, or ``{'status': 'skipped'}`` if a run is already in progress."
    ),
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_ingestion_now(request: Request) -> dict[str, Any]:
    """
    Trigger one ingestion cycle on demand.

    The cycle runs in the background (fire-and-forget); this endpoint
    returns as soon as the job is submitted — it does NOT wait for
    ingestion to complete.

    Raises:
        HTTPException(503): Scheduler is not available on app.state
                            (startup incomplete or scheduler failed to start).
    """
    scheduler = getattr(request.app.state, "scheduler", None)

    if scheduler is None:
        logger.error(
            "Manual ingestion trigger failed: scheduler not found on app.state."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Ingestion scheduler is not available. "
                "The application may still be starting up."
            ),
        )

    result = await scheduler.trigger_now()

    logger.info(
        "Manual ingestion trigger requested — outcome: %s", result.get("status")
    )
    return result
