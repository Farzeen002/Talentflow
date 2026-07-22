"""
app/services/storage/local.py

Local filesystem storage provider.

Active during development (``STORAGE_PROVIDER=local``).  All blobs are written
under ``LOCAL_STORAGE_ROOT``, which is the only value that needs to change when
migrating to GCS — the relative blob paths stored in MongoDB stay identical.

Safety guarantees
-----------------
* **Atomic writes** — data is written to a ``.tmp`` sibling then renamed,
  so a crash mid-write never leaves a corrupt file.
* **Path traversal prevention** — every resolved path is checked to lie
  within the storage root before any I/O is performed.
* **Directory auto-creation** — parent directories are created on first write.
* **UTF-8 text writes** — text is encoded to bytes before writing so the
  on-disk byte count matches ``UploadResult.size_bytes``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from app.services.storage.base import StorageError, StorageService, UploadResult

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Extension helpers
# ══════════════════════════════════════════════════════════════════════════════

# Maps MIME type → canonical file extension for resume files.
_MIME_TO_EXT: dict[str, str] = {
    "application/pdf":                                                          "pdf",
    "application/msword":                                                       "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def _get_resume_ext(filename: str, mime_type: str) -> str:
    """
    Derive the file extension for a resume blob path.

    MIME type takes priority; filename suffix is used as a fallback.
    Falls back to ``"bin"`` when neither yields a known extension.
    """
    ext = _MIME_TO_EXT.get(mime_type.lower(), "")
    if not ext:
        suffix = Path(filename).suffix.lstrip(".").lower()
        ext = suffix if suffix in ("pdf", "doc", "docx") else "bin"
    return ext


# ══════════════════════════════════════════════════════════════════════════════
# Provider
# ══════════════════════════════════════════════════════════════════════════════

class LocalStorageService(StorageService):
    """
    Filesystem-backed storage provider.

    Directory layout::

        <LOCAL_STORAGE_ROOT>/
            resumes/
                <recruiter_id>/
                    <candidate_id>/
                        original.pdf
                        extracted.txt

    This layout is identical to what GCS object keys would look like, so
    migration is a straight key-for-key copy — no path rewriting needed.
    """

    def __init__(self, root: str) -> None:
        """
        Parameters
        ----------
        root:
            Storage root directory. May be absolute or relative.

            * **Absolute path** (strongly recommended): used exactly as
              given — no project root or CWD is prepended.  Use forward
              slashes on Windows: ``D:/mydata/storage``
            * **Relative path**: resolved from the process CWD at the
              moment this constructor runs.  Only suitable for ad-hoc
              development (``./storage``).

        Resolution is performed via ``Path(root).resolve()``.
        Never pass a value already prefixed with a project root or
        ``BASE_DIR`` — doing so with an absolute ``root`` would produce
        a corrupted path on Windows.
        """
        raw_path = Path(root)

        if not raw_path.is_absolute():
            logger.warning(
                "event=storage.local.relative_root "
                "LOCAL_STORAGE_ROOT=%r is a relative path — "
                "resolved from CWD=%r. "
                "Set an absolute path in .env to avoid CWD-dependent behaviour.",
                root, str(Path.cwd()),
            )

        # Path.resolve() handles both cases correctly:
        #   absolute → returned as-is (no BASE_DIR prepended)
        #   relative → resolved from CWD
        # Do NOT manually join with project root, BASE_DIR, or os.getcwd().
        self._root = raw_path.resolve()

        self._root.mkdir(parents=True, exist_ok=True)

        if not os.access(self._root, os.W_OK):
            raise StorageError(
                f"Storage root is not writable: {self._root!r}. "
                f"Check directory permissions."
            )

        logger.info(
            "event=storage.local.ready "
            "configured=%r resolved=%r exists=True writable=True",
            root, str(self._root),
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve(self, blob_path: str) -> Path:
        """
        Resolve a relative blob_path against the storage root.

        Raises
        ------
        StorageError
            If the resolved path escapes the storage root (traversal attempt).
        """
        resolved = (self._root / blob_path).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError:
            raise StorageError(
                f"Path traversal detected — blob_path {blob_path!r} "
                f"resolves outside storage root."
            )
        return resolved

    # ── Write ─────────────────────────────────────────────────────────────────

    def upload_binary(self, blob_path: str, data: bytes) -> UploadResult:
        path = self._resolve(blob_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(
                f"Failed to write binary to {blob_path!r}: {exc}"
            ) from exc
        return UploadResult(blob_path=blob_path, size_bytes=len(data))

    def upload_text(self, blob_path: str, text: str) -> UploadResult:
        path = self._resolve(blob_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = text.encode("utf-8")
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_bytes(encoded)
            tmp.replace(path)
        except OSError as exc:
            tmp.unlink(missing_ok=True)
            raise StorageError(
                f"Failed to write text to {blob_path!r}: {exc}"
            ) from exc
        return UploadResult(blob_path=blob_path, size_bytes=len(encoded))

    # ── Read ──────────────────────────────────────────────────────────────────

    def download_binary(self, blob_path: str) -> bytes:
        path = self._resolve(blob_path)
        if not path.exists():
            raise StorageError(f"Blob not found: {blob_path!r}")
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StorageError(
                f"Failed to read {blob_path!r}: {exc}"
            ) from exc

    # ── Existence / deletion ──────────────────────────────────────────────────

    def exists(self, blob_path: str) -> bool:
        try:
            return self._resolve(blob_path).exists()
        except StorageError:
            return False

    def delete(self, blob_path: str) -> None:
        path = self._resolve(blob_path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise StorageError(
                f"Failed to delete {blob_path!r}: {exc}"
            ) from exc

    # ── Client-facing URL generation ──────────────────────────────────────────

    def get_serving_path(
        self,
        blob_path:    str,
        disposition:  str        = "inline",
        filename:     str | None = None,
        content_type: str | None = None,
    ) -> str:
        """
        Return an absolute filesystem path for local dev access.

        When ``disposition``, ``filename``, or ``content_type`` are supplied,
        this method raises ``StorageError`` because signed URL generation
        requires GCS.  Set ``STORAGE_PROVIDER=gcs`` to use signed URLs.

        ⚠️  Do NOT store the returned path in MongoDB.  It is machine-specific
            and will break on any other machine or after a storage root change.
            Store ``blob_path`` (relative) instead.
        """
        non_default = (
            disposition != "inline"
            or filename is not None
            or content_type is not None
        )
        if non_default:
            raise StorageError(
                "Signed URL generation is not supported in local storage mode. "
                "Set STORAGE_PROVIDER=gcs in .env to use signed URLs."
            )
        return str(self._resolve(blob_path))

    # ── Path builders ─────────────────────────────────────────────────────────

    def build_resume_blob_path(
        self,
        recruiter_id: str,
        candidate_id: str,
        filename:     str,
        mime_type:    str = "",
    ) -> str:
        ext = _get_resume_ext(filename, mime_type)
        return f"resumes/{recruiter_id}/{candidate_id}/original.{ext}"

    def build_extracted_text_blob_path(
        self,
        recruiter_id: str,
        candidate_id: str,
    ) -> str:
        return f"resumes/{recruiter_id}/{candidate_id}/extracted.txt"
