"""
app/db/mongo.py

Async MongoDB connection management using Motor.

Provides:
  - A global AsyncIOMotorClient instance
  - A database accessor
  - A FastAPI dependency `get_db()` for route injection
"""

from typing import AsyncGenerator

import motor.motor_asyncio
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

settings = get_settings()

# ── Module-level client (initialised at startup, closed at shutdown) ──────────
_client: AsyncIOMotorClient | None = None


def get_client() -> AsyncIOMotorClient:
    """Return the active Motor client. Raises if not yet initialised."""
    if _client is None:
        raise RuntimeError(
            "MongoDB client has not been initialised. "
            "Ensure connect_db() is called during application startup."
        )
    return _client


async def connect_db() -> None:
    """
    Open the MongoDB connection.
    Must be called inside the FastAPI lifespan startup handler.
    """
    global _client
    _client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URL)
    # Ping to validate the connection early
    await _client.admin.command("ping")


async def close_db() -> None:
    """
    Close the MongoDB connection.
    Must be called inside the FastAPI lifespan shutdown handler.
    """
    global _client
    if _client is not None:
        _client.close()
        _client = None


def get_database() -> AsyncIOMotorDatabase:
    """Return the configured database instance."""
    return get_client()[settings.MONGODB_DB_NAME]


# ── FastAPI dependency ────────────────────────────────────────────────────────
async def get_db() -> AsyncGenerator[AsyncIOMotorDatabase, None]:
    """
    FastAPI dependency that yields the Motor database.

    Usage::

        @router.get("/items")
        async def list_items(db: AsyncIOMotorDatabase = Depends(get_db)):
            ...
    """
    yield get_database()

