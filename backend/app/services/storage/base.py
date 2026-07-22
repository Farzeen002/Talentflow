"""
app/services/storage/base.py

Provider-agnostic storage interface.

All storage operations flow through this contract.  Business logic (resume
processing, workers, API handlers) imports only from this module — never from
provider-specific modules.  Swapping the backend (local → GCS → S3) requires
only a config change.

Blob paths
----------
Every method that accepts or returns a ``blob_path`` uses *relative* keys,
e.g. ``"resumes/<recruiter_id>/<candidate_id>/original.pdf"``.

The storage root (local directory or GCS bucket) is an implementation detail
of the provider and is never exposed to callers.  This is exactly what gets
stored in MongoDB.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


# ══════════════════════════════════════════════════════════════════════════════
# Data types
# ══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class UploadResult:
    """
    Returned by every upload operation.

    Attributes
    ----------
    blob_path:
        Relative storage key — store this value in MongoDB.
    size_bytes:
        Exact byte count written to the backend.
    """

    blob_path:  str
    size_bytes: int


class StorageError(Exception):
    """
    Raised for all storage-level failures.

    Wraps filesystem ``OSError``, GCS SDK exceptions, or any provider-specific
    error so callers only need to catch one type.
    """


# ══════════════════════════════════════════════════════════════════════════════
# Abstract interface
# ══════════════════════════════════════════════════════════════════════════════

class StorageService(ABC):
    """
    Provider-agnostic storage contract.

    Implement this class for each backend (local filesystem, GCS, S3, …).
    The factory in ``app/services/storage/__init__.py`` selects the active
    provider via ``STORAGE_PROVIDER`` in config.

    Path conventions
    ----------------
    * All ``blob_path`` parameters are relative keys (no leading slash).
    * ``build_resume_blob_path`` and ``build_extracted_text_blob_path`` are
      the canonical helpers for constructing those keys — always use them
      instead of building paths manually in business logic.
    """

    # ── Write ─────────────────────────────────────────────────────────────────

    @abstractmethod
    def upload_binary(self, blob_path: str, data: bytes) -> UploadResult:
        """
        Write raw binary data (e.g. a PDF) to ``blob_path``.

        Overwrites any existing content at that path.
        Creates intermediate directories/prefixes automatically.

        Raises
        ------
        StorageError
            On any write failure.
        """

    @abstractmethod
    def upload_text(self, blob_path: str, text: str) -> UploadResult:
        """
        Write a UTF-8 text string (e.g. extracted resume text) to ``blob_path``.

        Raises
        ------
        StorageError
            On any write failure.
        """

    # ── Read ──────────────────────────────────────────────────────────────────

    @abstractmethod
    def download_binary(self, blob_path: str) -> bytes:
        """
        Read and return the raw bytes at ``blob_path``.

        Raises
        ------
        StorageError
            If the blob does not exist or cannot be read.
        """

    # ── Existence / deletion ──────────────────────────────────────────────────

    @abstractmethod
    def exists(self, blob_path: str) -> bool:
        """Return ``True`` if ``blob_path`` exists in the backend."""

    @abstractmethod
    def delete(self, blob_path: str) -> None:
        """
        Remove ``blob_path`` from storage.

        Silent no-op if the path does not exist.

        Raises
        ------
        StorageError
            On any deletion failure other than file-not-found.
        """

    # ── Client-facing URL generation ──────────────────────────────────────────

    @abstractmethod
    def get_serving_path(
        self,
        blob_path:    str,
        disposition:  str        = "inline",
        filename:     str | None = None,
        content_type: str | None = None,
    ) -> str:
        """
        Return a URL suitable for serving a blob directly to a client.

        Parameters
        ----------
        blob_path:
            Relative storage key (e.g. ``"resumes/<r>/<c>/original.pdf"``).
        disposition:
            ``"inline"``     — browser should render the file in-place (PDF preview).
            ``"attachment"`` — browser should download the file.
            Defaults to ``"inline"``.
        filename:
            Original filename to embed in ``Content-Disposition: attachment``.
            Used only when ``disposition="attachment"``.  Ignored for ``"inline"``.
        content_type:
            MIME type to enforce on the response
            (e.g. ``"application/pdf"``).
            Prevents browsers from misinterpreting the file when the blob was
            uploaded with a generic ``application/octet-stream`` content type.

        Returns
        -------
        str
            GCS provider  — a short-lived V4 signed URL (15 min) with
                            ``Content-Disposition`` and ``Content-Type``
                            headers baked in and cryptographically signed.
            Local provider — raises ``StorageError`` when non-default params
                             are supplied (signed URLs require GCS).

        Notes
        -----
        * Do NOT store the returned URL in MongoDB — it is transient.
        * Generate a fresh URL on every client request.
        """

    # ── Path builders (canonical key construction) ────────────────────────────

    @abstractmethod
    def build_resume_blob_path(
        self,
        recruiter_id: str,
        candidate_id: str,
        filename:     str,
        mime_type:    str = "",
    ) -> str:
        """
        Build the relative storage key for a candidate's original resume binary.

        The stored filename is always ``original.<ext>`` so blob paths are
        deterministic and provider-agnostic.  The extension is derived from
        ``mime_type`` when supplied; ``filename`` is used as a fallback.

        Example output::

            resumes/<recruiter_id>/<candidate_id>/original.pdf
            resumes/<recruiter_id>/<candidate_id>/original.docx
        """

    @abstractmethod
    def build_extracted_text_blob_path(
        self,
        recruiter_id: str,
        candidate_id: str,
    ) -> str:
        """
        Build the relative storage key for the extracted plain-text resume.

        Example output::

            resumes/<recruiter_id>/<candidate_id>/extracted.txt
        """
