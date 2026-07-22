"""
app/services/storage/__init__.py

Storage layer public API.

Usage
-----
All application code should import from here — never from provider modules
directly::

    from app.services.storage import get_storage, StorageError, UploadResult

    storage = get_storage()
    result  = storage.upload_binary(blob_path, data)

Provider selection
------------------
Controlled by ``STORAGE_PROVIDER`` in .env:

    local  →  LocalStorageService   (development default)
    gcs    →  GCSStorageService     (production)

The singleton is initialised once per process.  Call ``reset_storage()`` in
tests to force re-initialisation with a different root or provider.
"""

from __future__ import annotations

from app.services.storage.base import StorageError, StorageService, UploadResult

__all__ = [
    "StorageService",
    "StorageError",
    "UploadResult",
    "get_storage",
    "reset_storage",
]

_instance: StorageService | None = None


def get_storage() -> StorageService:
    """
    Return the active storage provider singleton.

    Thread-safe for read access; initialisation is idempotent per process.
    Provider is selected from ``settings.STORAGE_PROVIDER``.

    Raises
    ------
    ValueError
        If ``STORAGE_PROVIDER`` is set to an unrecognised value.
    ImportError
        If the GCS provider is selected but ``google-cloud-storage`` is not
        installed.
    """
    global _instance
    if _instance is None:
        from app.config import get_settings

        settings = get_settings()
        provider = settings.STORAGE_PROVIDER.lower()

        if provider == "local":
            from app.services.storage.local import LocalStorageService
            _instance = LocalStorageService(root=settings.LOCAL_STORAGE_ROOT)

        elif provider == "gcs":
            from app.services.storage.gcs import GCSStorageService
            if not settings.GCS_BUCKET_NAME:
                raise ValueError(
                    "STORAGE_PROVIDER=gcs requires GCS_BUCKET_NAME to be set in .env"
                )
            _instance = GCSStorageService(bucket_name=settings.GCS_BUCKET_NAME)

        else:
            raise ValueError(
                f"Unknown STORAGE_PROVIDER: {provider!r}. "
                f"Valid values: 'local', 'gcs'."
            )

    return _instance


def reset_storage() -> None:
    """
    Clear the provider singleton.

    Intended for use in tests only — allows re-initialisation with a
    different root directory or mock provider between test cases.
    """
    global _instance
    _instance = None
