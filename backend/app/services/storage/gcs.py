"""
app/services/storage/gcs.py

Google Cloud Storage provider.

Activation
----------
1. Set ``STORAGE_PROVIDER=gcs`` and ``GCS_BUCKET_NAME=<your-bucket>`` in .env.
2. Install the SDK and auth library::

       pip install google-cloud-storage google-auth

3. Set the following environment variables (copy values from your service-account
   JSON key file — see .env.example for the full list and formatting notes)::

       GCP_PROJECT_ID=...
       GCP_PRIVATE_KEY_ID=...
       GCP_PRIVATE_KEY=...          # \\n-encoded newlines
       GCP_CLIENT_EMAIL=...
       GCP_CLIENT_ID=...

   No ``GOOGLE_APPLICATION_CREDENTIALS`` file path or ``gcloud`` login is required.
   Credentials are built at runtime by ``app.services.storage.credentials``.

Migration from local
--------------------
The blob paths stored in MongoDB are identical between providers
(``"resumes/<recruiter_id>/<candidate_id>/original.pdf"``), so migration is:

1. Copy all files from ``LOCAL_STORAGE_ROOT/`` to the GCS bucket preserving
   the same relative key structure.
2. Flip ``STORAGE_PROVIDER=local`` → ``STORAGE_PROVIDER=gcs`` in .env.
3. Restart workers and API.  No schema or business-logic changes required.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.services.storage.base import StorageError, StorageService, UploadResult

logger = logging.getLogger(__name__)

_MIME_TO_EXT: dict[str, str] = {
    "application/pdf":                                                          "pdf",
    "application/msword":                                                       "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def _get_resume_ext(filename: str, mime_type: str) -> str:
    ext = _MIME_TO_EXT.get(mime_type.lower(), "")
    if not ext:
        suffix = Path(filename).suffix.lstrip(".").lower()
        ext = suffix if suffix in ("pdf", "doc", "docx") else "bin"
    return ext


def _build_disposition_header(disposition: str, filename: str | None) -> str:
    """
    Build a ``Content-Disposition`` header value for use in a GCS signed URL.

    Examples
    --------
    ``"inline"``
    ``"attachment"``
    ``'attachment; filename="John_Doe_Resume.pdf"'``
    """
    if disposition == "inline":
        return "inline"
    if filename:
        # RFC 6266: quote the filename; escape any embedded double-quotes.
        safe_name = filename.replace('"', '\\"')
        return f'attachment; filename="{safe_name}"'
    return "attachment"


class GCSStorageService(StorageService):
    """
    Google Cloud Storage backend.

    Object keys are identical to the local provider's relative paths, so a
    bucket listing mirrors the local directory tree 1-to-1.
    """

    def __init__(self, bucket_name: str) -> None:
        logger.debug(
            "event=gcs.client.init_start bucket_name=%s",
            bucket_name,
        )
        try:
            from google.cloud import storage as _gcs  # type: ignore[import]
        except ImportError:
            logger.error(
                "event=gcs.client.import_failed "
                "detail='google-cloud-storage not installed — run: pip install google-cloud-storage google-auth'"
            )
            raise ImportError(
                "google-cloud-storage is not installed. "
                "Run: pip install google-cloud-storage google-auth"
            ) from None

        from app.config import get_settings
        from app.services.storage.credentials import get_gcp_credentials

        settings = get_settings()

        logger.debug(
            "event=gcs.client.building_credentials "
            "project_id=%s bucket_name=%s",
            settings.GCP_PROJECT_ID,
            bucket_name,
        )
        credentials = get_gcp_credentials()

        self._bucket_name = bucket_name
        self._client = _gcs.Client(
            project=settings.GCP_PROJECT_ID,
            credentials=credentials,
        )
        self._bucket = self._client.bucket(bucket_name)

        logger.info(
            "event=gcs.client.ready "
            "bucket_name=%s project_id=%s",
            bucket_name,
            settings.GCP_PROJECT_ID,
        )

    # ── Write ─────────────────────────────────────────────────────────────────

    def upload_binary(self, blob_path: str, data: bytes) -> UploadResult:
        size = len(data)
        logger.info(
            "event=gcs.upload_binary.start blob_path=%s size_bytes=%d bucket=%s",
            blob_path, size, self._bucket_name,
        )
        try:
            blob = self._bucket.blob(blob_path)
            blob.upload_from_string(data, content_type="application/octet-stream")
            logger.info(
                "event=gcs.upload_binary.success blob_path=%s size_bytes=%d",
                blob_path, size,
            )
            return UploadResult(blob_path=blob_path, size_bytes=size)
        except Exception as exc:
            logger.error(
                "event=gcs.upload_binary.failed "
                "blob_path=%s size_bytes=%d error_type=%s detail=%s",
                blob_path, size, type(exc).__name__, exc,
            )
            raise StorageError(
                f"GCS upload_binary failed for {blob_path!r}: {exc}"
            ) from exc

    def upload_text(self, blob_path: str, text: str) -> UploadResult:
        encoded = text.encode("utf-8")
        size = len(encoded)
        logger.info(
            "event=gcs.upload_text.start blob_path=%s size_bytes=%d bucket=%s",
            blob_path, size, self._bucket_name,
        )
        try:
            blob = self._bucket.blob(blob_path)
            blob.upload_from_string(encoded, content_type="text/plain; charset=utf-8")
            logger.info(
                "event=gcs.upload_text.success blob_path=%s size_bytes=%d",
                blob_path, size,
            )
            return UploadResult(blob_path=blob_path, size_bytes=size)
        except Exception as exc:
            logger.error(
                "event=gcs.upload_text.failed "
                "blob_path=%s size_bytes=%d error_type=%s detail=%s",
                blob_path, size, type(exc).__name__, exc,
            )
            raise StorageError(
                f"GCS upload_text failed for {blob_path!r}: {exc}"
            ) from exc

    # ── Read ──────────────────────────────────────────────────────────────────

    def download_binary(self, blob_path: str) -> bytes:
        logger.debug("event=gcs.download_binary.start blob_path=%s", blob_path)
        try:
            blob = self._bucket.blob(blob_path)
            data = blob.download_as_bytes()
            logger.info(
                "event=gcs.download_binary.success blob_path=%s size_bytes=%d",
                blob_path, len(data),
            )
            return data
        except Exception as exc:
            logger.error(
                "event=gcs.download_binary.failed "
                "blob_path=%s error_type=%s detail=%s",
                blob_path, type(exc).__name__, exc,
            )
            raise StorageError(
                f"GCS download_binary failed for {blob_path!r}: {exc}"
            ) from exc

    # ── Existence / deletion ──────────────────────────────────────────────────

    def exists(self, blob_path: str) -> bool:
        try:
            return self._bucket.blob(blob_path).exists()
        except Exception:
            return False

    def delete(self, blob_path: str) -> None:
        logger.debug("event=gcs.delete.start blob_path=%s", blob_path)
        try:
            blob = self._bucket.blob(blob_path)
            if blob.exists():
                blob.delete()
                logger.info("event=gcs.delete.success blob_path=%s", blob_path)
            else:
                logger.debug("event=gcs.delete.noop blob_path=%s reason='blob_not_found'", blob_path)
        except Exception as exc:
            logger.error(
                "event=gcs.delete.failed blob_path=%s error_type=%s detail=%s",
                blob_path, type(exc).__name__, exc,
            )
            raise StorageError(
                f"GCS delete failed for {blob_path!r}: {exc}"
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
        Return a short-lived V4 signed URL (15 min) for serving a blob to a client.

        ``Content-Disposition`` and ``Content-Type`` are cryptographically baked
        into the URL at signing time — they cannot be tampered with by the client.

        Parameters
        ----------
        blob_path:
            Relative GCS object key.
        disposition:
            ``"inline"``     — PDF opens in browser (iframe / new tab).
            ``"attachment"`` — browser downloads the file.
        filename:
            Suggested filename for ``attachment`` disposition.
        content_type:
            MIME type enforced on the GCS response.
            Prevents browsers from treating a PDF as ``application/octet-stream``.
        """
        import datetime
        logger.debug(
            "event=gcs.signed_url.start blob_path=%s disposition=%s expiry_minutes=15",
            blob_path, disposition,
        )
        try:
            blob = self._bucket.blob(blob_path)

            # Build disposition header string
            disposition_header = _build_disposition_header(disposition, filename)

            url = blob.generate_signed_url(
                expiration=datetime.timedelta(minutes=15),
                method="GET",
                version="v4",
                response_disposition=disposition_header,
                response_type=content_type or "application/octet-stream",
            )
            logger.info(
                "event=gcs.signed_url.success blob_path=%s disposition=%s",
                blob_path, disposition,
            )
            return url
        except Exception as exc:
            logger.error(
                "event=gcs.signed_url.failed blob_path=%s error_type=%s detail=%s",
                blob_path, type(exc).__name__, exc,
            )
            raise StorageError(
                f"GCS signed URL generation failed for {blob_path!r}: {exc}"
            ) from exc

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
