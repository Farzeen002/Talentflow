"""
app/db/redis.py

Redis client management using redis-py (sync client).

Responsibilities:
  - connect_redis()      : create and validate the connection at startup
  - close_redis()        : tear down the connection at shutdown
  - get_redis_client()   : return the active client (raises if not initialised)
  - get_redis()          : FastAPI dependency wrapper around get_redis_client()
  - is_redis_healthy()   : cheap liveness probe (ping), never raises
  - reconnect_redis()    : attempt to (re-)establish a fresh connection from
                           settings; updates the module-level client on success.
                           Called by the scheduler on every tick when the
                           current client is None or unhealthy.
"""

from __future__ import annotations

import logging

import redis
from redis import Redis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Module-level client ───────────────────────────────────────────────────────
_redis_client: Redis | None = None


# ══════════════════════════════════════════════════════════════════════════════
# Lifecycle — called from FastAPI lifespan
# ══════════════════════════════════════════════════════════════════════════════

def connect_redis() -> None:
    """
    Create and validate the Redis connection at application startup.

    Must be called inside the FastAPI lifespan startup handler before any
    other code attempts to use the client.

    If ``REDIS_URL`` is not configured, logs a warning and leaves the client
    as ``None`` — this is a valid "no-Redis" deployment mode.

    If the URL is configured but the connection fails, logs an error and
    leaves the client as ``None``.  The scheduler will attempt reconnection
    on every ingestion tick via :func:`reconnect_redis`.
    """
    global _redis_client

    url = getattr(settings, "REDIS_URL", None)
    if not url:
        logger.warning(
            "event=redis.not_configured — REDIS_URL is not set. "
            "RQ queue will be disabled."
        )
        _redis_client = None
        return

    try:
        client = redis.from_url(url, decode_responses=True)
        client.ping()
        _redis_client = client
        logger.info("event=redis.connected")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "event=redis.connection_failed detail=%s "
            "— scheduler will attempt reconnection on every ingestion tick.",
            exc,
        )
        _redis_client = None


def close_redis() -> None:
    """
    Close the Redis connection pool at application shutdown.

    Must be called inside the FastAPI lifespan shutdown handler.
    Safe to call when the client is ``None``.
    """
    global _redis_client
    if _redis_client is not None:
        try:
            _redis_client.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=redis.close_error detail=%s", exc)
        finally:
            _redis_client = None
            logger.info("event=redis.closed")


# ══════════════════════════════════════════════════════════════════════════════
# Accessors
# ══════════════════════════════════════════════════════════════════════════════

def get_redis_client() -> Redis:
    """
    Return the active Redis client.

    Raises:
        RuntimeError: If the client has not been initialised (i.e.
            ``connect_redis()`` was never called, or it failed).
    """
    if _redis_client is None:
        raise RuntimeError(
            "Redis client has not been initialised. "
            "Ensure connect_redis() succeeded during application startup."
        )
    return _redis_client


def get_redis() -> Redis:
    """
    FastAPI dependency that returns the active Redis client.

    Usage::

        @router.get("/cache")
        async def read_cache(redis: Redis = Depends(get_redis)):
            value = redis.get("some_key")
            ...
    """
    return get_redis_client()


# ══════════════════════════════════════════════════════════════════════════════
# Health probe + reconnect — called by the scheduler per tick
# ══════════════════════════════════════════════════════════════════════════════

def is_redis_healthy() -> bool:
    """
    Perform a cheap liveness check against the current Redis client.

    Returns ``True`` only when the client exists *and* responds to ``PING``.
    Never raises — all exceptions are swallowed and treated as unhealthy.

    Used by :class:`~app.scheduler.ingestion_scheduler.IngestionScheduler`
    before each ingestion tick to decide whether to rebuild the queue.
    """
    if _redis_client is None:
        return False
    try:
        return bool(_redis_client.ping())
    except Exception:  # noqa: BLE001
        return False


def reconnect_redis() -> bool:
    """
    Attempt to create a fresh Redis connection from the current settings.

    Updates the module-level ``_redis_client`` on success so that subsequent
    calls to :func:`get_redis_client` and :func:`is_redis_healthy` use the
    new connection.

    Returns:
        ``True``  if the new connection is healthy (ping succeeded).
        ``False`` if ``REDIS_URL`` is not configured or the connection failed.

    Never raises — all exceptions are caught and logged at WARNING level.

    Intended to be called by the scheduler on every tick when Redis appears
    to be unavailable, enabling automatic recovery without a process restart.
    """
    global _redis_client

    url = getattr(settings, "REDIS_URL", None)
    if not url:
        return False

    try:
        client = redis.from_url(url, decode_responses=True)
        client.ping()
        _redis_client = client
        logger.info("event=redis.reconnected")
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=redis.reconnect_failed detail=%s", exc)
        _redis_client = None
        return False
