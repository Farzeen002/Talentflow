"""
app/main.py

Application entry point.

Responsibilities:
  - Create the FastAPI application instance
  - Register the lifespan context (startup / shutdown hooks)
  - Mount API routers
  - Expose the root health-check endpoint
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.candidates import router as candidates_router
from app.api.internal import router as internal_router
from app.api.jobs import router as jobs_router
from app.api.reports import router as reports_router
from app.config import get_settings
from app.db.mongo import connect_db, close_db, get_database
from app.db.redis import connect_redis, close_redis, get_redis_client
from app.scheduler.ingestion_scheduler import IngestionScheduler
from app.services.auth_service import ensure_indexes
from app.services.candidate_filter import ensure_ats_indexes
from app.services.ingestion_service import ensure_ingestion_indexes
from app.services.job_service import ensure_job_indexes
from app.services.report_service import ensure_daily_report_indexes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

settings = get_settings()


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Manage application-level resources.

    Startup  : open DB connections, initialise clients.
    Shutdown : close all connections gracefully.
    """
    # ── Startup ───────────────────────────────────────────────────────────────
    await connect_db()
    connect_redis()
    db = get_database()
    await ensure_indexes(db)
    await ensure_ingestion_indexes(db)
    await ensure_job_indexes(db)
    await ensure_ats_indexes(db)
    try:
        await ensure_daily_report_indexes(db)
    except Exception:
        logger.exception(
            "event=startup.reports_indexes_failed — "
            "Daily Reports indexes could not be ensured; refusing to continue."
        )
        raise

    # ── Scheduler ─────────────────────────────────────────────────────────────
    # Build an RQ Queue only when Redis is available.  If Redis is down at
    # startup, queue is passed as None.  The scheduler's _get_or_rebuild_queue()
    # will probe Redis and reconstruct the queue on every tick, so ingestion
    # resumes automatically when Redis recovers — no restart required.
    try:
        redis_client = get_redis_client()
        from rq import Queue as RQQueue
        queue = RQQueue(connection=redis_client)
        logger.info("event=startup.rq_queue_initialised")
    except RuntimeError:
        queue = None  # type: ignore[assignment]
        logger.warning(
            "event=startup.redis_unavailable "
            "— RQ queue initialised as None. "
            "The scheduler will attempt reconnection on every ingestion tick; "
            "messages will be enqueued automatically when Redis recovers."
        )

    scheduler = IngestionScheduler(db=db, queue=queue)  # type: ignore[arg-type]
    scheduler.start()
    app.state.scheduler = scheduler

    yield  # Application runs here

    # ── Shutdown ──────────────────────────────────────────────────────────────
    scheduler.shutdown()
    await close_db()
    close_redis()


# ── Application factory ───────────────────────────────────────────────────────
def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    app = FastAPI(
        title="Recruitment Automation API",
        description=(
            "Production-grade backend for the recruitment automation platform. "
            "Handles candidate pipelines, job postings, OAuth, and scheduling."
        ),
        version="1.0.0",
        docs_url="/docs" if settings.APP_ENV != "production" else None,
        redoc_url="/redoc" if settings.APP_ENV != "production" else None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(auth_router,        prefix="/api/v1")
    app.include_router(internal_router,    prefix="/api/v1")
    app.include_router(jobs_router,        prefix="/api/v1")
    app.include_router(candidates_router,  prefix="/api/v1")
    app.include_router(reports_router,     prefix="/api/v1")

    # ── Root endpoint ─────────────────────────────────────────────────────────
    @app.get("/", tags=["Health"], summary="Root health-check")
    async def root() -> dict[str, str]:
        """
        Basic liveness probe.

        Returns the service name, current environment, and status.
        """
        return {
            "service": "Recruitment Automation API",
            "environment": settings.APP_ENV,
            "status": "online",
        }

    @app.get("/health", tags=["Health"], summary="Health check")
    async def health() -> dict[str, str]:
        """Dedicated health endpoint for hosting platform probes."""
        return {
            "service": "Recruitment Automation API",
            "environment": settings.APP_ENV,
            "status": "healthy",
        }

    return app


# ── ASGI application ──────────────────────────────────────────────────────────
app = create_app()
