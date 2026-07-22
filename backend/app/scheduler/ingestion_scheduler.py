"""
app/scheduler/ingestion_scheduler.py

Production-grade APScheduler wrapper for the email ingestion pipeline.

Responsibilities:
  - Wrap AsyncIOScheduler lifecycle (start / shutdown)
  - Register the ingestion job at a configurable interval
  - Guard against overlapping runs with an async-safe boolean lock
  - Lazily resolve and rebuild the RQ Queue on every tick so that a Redis
    outage at startup (or mid-run) does not permanently disable ingestion
  - Log every stage: start, skip, success, failure
  - Expose a manual trigger method for the /internal/run-ingestion endpoint

Design constraints:
  - Singleton: one instance created at FastAPI startup, stored on app.state
  - No blocking I/O — scheduler runs in the existing asyncio event loop
  - All state lives on the instance; no module-level globals
  - IngestionService is instantiated fresh per run (keeps dependencies clean)
  - The queue reference is resolved per tick, never cached permanently

FastAPI integration pattern::

    # In lifespan startup:
    scheduler = IngestionScheduler(db=db, queue=queue)
    scheduler.start()
    app.state.scheduler = scheduler

    # In lifespan shutdown:
    app.state.scheduler.shutdown()
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.services.ingestion_service import IngestionService

if TYPE_CHECKING:
    from motor.motor_asyncio import AsyncIOMotorDatabase
    from rq import Queue

logger = logging.getLogger(__name__)

# ── Default interval — overrideable via constructor ───────────────────────────
_DEFAULT_INTERVAL_MINUTES: int = 10


class IngestionScheduler:
    """
    Manages the periodic execution of the email ingestion pipeline.

    The scheduler runs inside the FastAPI process on the same asyncio event
    loop, so no thread or subprocess is needed.  ``AsyncIOScheduler`` fires
    coroutines directly via ``asyncio.ensure_future``.

    Concurrency control
    -------------------
    APScheduler's ``max_instances=1`` prevents the scheduler from submitting
    a second job while one is still queued.  In addition, ``self._is_running``
    guards the *execution* phase: if a previous run is still awaiting DB /
    Gmail I/O when the next tick fires, the new invocation logs a skip and
    returns immediately.  Both guards are required because APScheduler's
    instance check only fires at job *dispatch* time, not at *execution* time.

    Redis recovery
    --------------
    The queue reference passed at construction time (``queue``) may be
    ``None`` if Redis was unavailable at startup.  On every tick,
    :meth:`_get_or_rebuild_queue` probes Redis and attempts to create a fresh
    ``Queue`` instance if the current reference is ``None`` or unhealthy.
    This means ingestion resumes automatically when Redis recovers — no
    process restart is needed.

    Args:
        db:               Motor async database instance.
        queue:            RQ Queue connected to a live Redis server, or
                          ``None`` if Redis was unavailable at startup.
        interval_minutes: How often to run ingestion (default: 4 min).
    """

    def __init__(
        self,
        db:               "AsyncIOMotorDatabase",
        queue:            "Queue | None",
        interval_minutes: int = _DEFAULT_INTERVAL_MINUTES,
    ) -> None:
        self._db             = db
        self._queue          = queue          # may be None; rebuilt lazily per tick
        self._interval_minutes = interval_minutes

        # Execution-phase lock — True while _run_ingestion_job is awaited.
        self._is_running: bool = False

        # Build the scheduler but do not start it yet.
        self._scheduler: AsyncIOScheduler = AsyncIOScheduler(
            job_defaults={"misfire_grace_time": 60},
        )

        self._scheduler.add_job(
            self._run_ingestion_job,
            trigger="interval",
            minutes=self._interval_minutes,
            id="ingestion_job",
            replace_existing=True,
            max_instances=1,    # APScheduler-level dispatch guard
            coalesce=True,      # Collapse missed fires into one execution
        )

    # =========================================================================
    # Lifecycle
    # =========================================================================

    def start(self) -> None:
        """
        Start the APScheduler and begin scheduling ingestion jobs.

        Safe to call multiple times — APScheduler raises
        ``SchedulerAlreadyRunningError`` if started twice; we catch and log it
        instead of crashing the server.
        """
        try:
            self._scheduler.start()
            logger.info(
                "event=scheduler.started interval_minutes=%d",
                self._interval_minutes,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("event=scheduler.start_failed detail=%s", exc)

    def shutdown(self) -> None:
        """
        Stop the scheduler and cancel any pending jobs.

        ``wait=False`` is used so shutdown does not block the asyncio event
        loop during FastAPI's lifespan teardown.
        """
        try:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
                logger.info("event=scheduler.stopped")
        except Exception as exc:  # noqa: BLE001
            logger.error("event=scheduler.stop_failed detail=%s", exc)

    # =========================================================================
    # Public manual trigger
    # =========================================================================

    async def trigger_now(self) -> dict[str, str]:
        """
        Immediately execute one ingestion cycle outside the normal schedule.

        Intended for the ``POST /internal/run-ingestion`` endpoint so
        operators can force a run during testing or after a Redis outage.

        Returns:
            Status dict describing the outcome::

                {"status": "started"}   # job began running
                {"status": "skipped"}   # previous run still in progress
        """
        if self._is_running:
            logger.warning(
                "event=scheduler.manual_trigger_rejected "
                "reason=ingestion_already_in_progress"
            )
            return {"status": "skipped", "reason": "ingestion already in progress"}

        import asyncio  # local import to avoid polluting module namespace
        asyncio.ensure_future(self._run_ingestion_job())
        return {"status": "started"}

    # =========================================================================
    # Internal job
    # =========================================================================

    def _get_or_rebuild_queue(self) -> "Queue | None":
        """
        Return a healthy RQ ``Queue``, attempting reconnection if needed.

        Called at the start of every ingestion tick.  The strategy:

        1. If ``self._queue`` is set and Redis responds to ``PING`` → return
           the existing queue (fast path, no object creation).
        2. Otherwise call :func:`~app.db.redis.reconnect_redis` to create a
           fresh connection.  If successful, construct a new ``Queue`` from
           the new client, store it on ``self._queue``, and return it.
        3. If reconnection also fails → return ``None``.  The
           ``IngestionService`` will raise before touching MongoDB, so no
           orphaned records are created.

        This method is synchronous (no ``await``) because it is called from
        the body of ``_run_ingestion_job`` before the first ``await``.
        ``reconnect_redis()`` performs a synchronous ``PING`` — acceptable
        for a once-per-tick probe.
        """
        from app.db.redis import is_redis_healthy, reconnect_redis, get_redis_client
        from rq import Queue as RQQueue

        # Fast path: existing queue is healthy.
        if self._queue is not None and is_redis_healthy():
            return self._queue

        # Slow path: attempt reconnection.
        logger.warning(
            "event=scheduler.redis_probe_failed — attempting reconnect."
        )
        if reconnect_redis():
            self._queue = RQQueue(connection=get_redis_client())
            logger.info(
                "event=scheduler.redis_reconnected queue_rebuilt=true"
            )
            return self._queue

        # Reconnection also failed.
        logger.error(
            "event=scheduler.redis_unavailable "
            "— ingestion cycle will be skipped. "
            "Messages will be retried when Redis recovers."
        )
        self._queue = None
        return None

    async def _run_ingestion_job(self) -> None:
        """
        Core async job executed by APScheduler on each interval tick.

        Steps:
          1. Check and set the execution-phase lock (``_is_running``).
          2. Probe Redis and rebuild the queue if necessary.
          3. Instantiate a fresh ``IngestionService`` with the resolved queue.
          4. Delegate all work to ``IngestionService.run_ingestion()``.
          5. Always release the lock in ``finally`` — even on exception.

        The method never raises; all exceptions are caught and logged so that
        a single ingestion failure cannot crash the scheduler loop.
        """
        # ── Execution-phase concurrency guard ─────────────────────────────────
        if self._is_running:
            logger.warning(
                "event=scheduler.tick_skipped "
                "reason=previous_run_still_in_progress "
                "— consider increasing the interval or reducing batch size."
            )
            return

        self._is_running = True
        logger.info("event=scheduler.tick_started")

        try:
            # Resolve queue on every tick — recovers from Redis outages
            # without requiring a process restart.
            queue = self._get_or_rebuild_queue()

            service = IngestionService(db=self._db, queue=queue)
            await service.run_ingestion()

            logger.info("event=scheduler.tick_completed")

        except Exception as exc:  # noqa: BLE001
            # Log at ERROR but do NOT re-raise — a crash here would kill the
            # scheduler and stop all future runs.
            logger.exception(
                "event=scheduler.tick_failed detail=%s", exc
            )

        finally:
            # Always release the lock, even if the job raised.
            self._is_running = False
