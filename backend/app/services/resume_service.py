"""
app/services/resume_service.py

Resume domain service.

Responsibilities (current)
--------------------------
* Validate recruiter ownership of a candidate's resume
* Generate short-lived GCS signed URLs for preview and download

Future responsibilities (this file scales to cover)
----------------------------------------------------
* OCR / text extraction triggers
* Download and preview audit logging
* Download tracking (count per candidate)
* Watermarked PDF generation
* Malware scan integration
* ATS-generated resume versions
* Redacted resume exports

Design constraints
------------------
* NO FastAPI request/response objects — those live in app/api/candidates.py
* All MongoDB I/O is async (Motor)
* Recruiter isolation enforced on every query via recruiter_id from JWT
* Storage layer accessed only via get_storage() — never provider-specific imports
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.storage import StorageError, get_storage

logger = logging.getLogger(__name__)

# ── Collection name ───────────────────────────────────────────────────────────
_CANDIDATES_COL = "candidates"

# ── Minimal projection — only what this service needs ────────────────────────
_RESUME_PROJECTION: dict[str, int] = {
    "resume.status":            1,
    "resume.original.blob_path": 1,
    "resume.original.file_type": 1,
    "resume.original.filename":  1,
    "_id":                       0,
}

# ── MIME types that support native browser inline preview ─────────────────────
_BROWSER_PREVIEWABLE: frozenset[str] = frozenset({
    "application/pdf",
})

# ── MIME type inference from blob_path extension (fallback) ──────────────────
_EXT_TO_MIME: dict[str, str] = {
    "pdf":  "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc":  "application/msword",
}

# ── Signed URL lifetime ───────────────────────────────────────────────────────
_SIGNED_URL_EXPIRY_SECONDS = 900   # 15 minutes — matches GCS provider default


# ══════════════════════════════════════════════════════════════════════════════
# Private helpers
# ══════════════════════════════════════════════════════════════════════════════

def _infer_content_type(blob_path: str) -> str:
    """
    Derive MIME type from the file extension in a blob_path.

    Used as a fallback when ``resume.original.file_type`` is null in MongoDB.
    Falls back to ``"application/octet-stream"`` for unknown extensions.

    Examples
    --------
    ``"resumes/r/c/original.pdf"``  → ``"application/pdf"``
    ``"resumes/r/c/original.docx"`` → ``"application/vnd.openxmlformats-..."``
    """
    ext = blob_path.rsplit(".", 1)[-1].lower() if "." in blob_path else ""
    return _EXT_TO_MIME.get(ext, "application/octet-stream")


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

async def generate_resume_url(
    db:           AsyncIOMotorDatabase,
    candidate_id: str,
    recruiter_id: str,
    action:       Literal["preview", "download"],
) -> dict[str, Any]:
    """
    Validate recruiter ownership and return a short-lived GCS signed URL
    for accessing the candidate's resume.

    Parameters
    ----------
    db:
        Async Motor database handle.
    candidate_id:
        UUID of the candidate whose resume is being accessed.
    recruiter_id:
        UUID extracted from the JWT — used to enforce ownership.
    action:
        ``"preview"``  — signed URL with ``Content-Disposition: inline``.
                         DOCX/DOC files are automatically downgraded to
                         ``"download"`` since browsers cannot render them.
        ``"download"`` — signed URL with ``Content-Disposition: attachment``.

    Returns
    -------
    dict
        Matches the ``ResumeUrlResponse`` schema:
        ``candidateId``, ``url``, ``expiresInSeconds``, ``fileType``,
        ``filename``, ``action``, ``note`` (optional).

    Raises
    ------
    HTTPException(404):
        Candidate not found, belongs to a different recruiter, or resume
        has not been uploaded yet (blob_path is null).
    HTTPException(500):
        GCS signed URL generation failed.
    HTTPException(501):
        ``STORAGE_PROVIDER=local`` — signed URLs not supported in local mode.
    """

    # ── Step 1: Fetch candidate (scoped to recruiter) ─────────────────────────
    try:
        doc = await db[_CANDIDATES_COL].find_one(
            {"candidate_id": candidate_id, "recruiter_id": recruiter_id},
            _RESUME_PROJECTION,
        )
    except Exception as exc:
        logger.exception(
            "event=resume_service.db_error candidate_id=%s detail=%s",
            candidate_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve candidate. Please try again.",
        ) from exc

    # Candidate not found OR belongs to a different recruiter — same response
    # (information hiding: attacker learns nothing about whether candidate exists)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candidate '{candidate_id}' not found.",
        )

    # ── Step 2: Extract blob_path ─────────────────────────────────────────────
    resume_raw  = doc.get("resume") or {}
    original    = resume_raw.get("original") or {}
    blob_path   = original.get("blob_path")
    stored_type = original.get("file_type")
    filename    = original.get("filename")

    if not blob_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume not found for this candidate.",
        )

    # ── Step 3: Resolve content type ──────────────────────────────────────────
    content_type: str = stored_type or _infer_content_type(blob_path)

    # ── Step 4: Determine effective disposition ───────────────────────────────
    # DOCX/DOC cannot be rendered inline by browsers — always force download.
    note: str | None = None

    if action == "preview" and content_type not in _BROWSER_PREVIEWABLE:
        effective_disposition = "attachment"
        effective_action      = "download"
        note = (
            "This file type cannot be previewed in the browser. "
            "The file will be downloaded instead."
        )
        logger.info(
            "event=resume_service.preview_downgraded "
            "candidate_id=%s content_type=%s → attachment",
            candidate_id, content_type,
        )
    elif action == "preview":
        effective_disposition = "inline"
        effective_action      = "preview"
    else:
        effective_disposition = "attachment"
        effective_action      = "download"

    # ── Step 5: Generate signed URL ───────────────────────────────────────────
    storage = get_storage()

    try:
        url = storage.get_serving_path(
            blob_path=    blob_path,
            disposition=  effective_disposition,
            filename=     filename,
            content_type= content_type,
        )
    except StorageError as exc:
        err_msg = str(exc)
        if "not supported in local storage mode" in err_msg:
            logger.warning(
                "event=resume_service.local_storage_unsupported candidate_id=%s",
                candidate_id,
            )
            raise HTTPException(
                status_code=status.HTTP_501_NOT_IMPLEMENTED,
                detail=(
                    "Resume URL generation is not supported in local storage mode. "
                    "Set STORAGE_PROVIDER=gcs in .env to enable this feature."
                ),
            ) from exc
        logger.error(
            "event=resume_service.signed_url_failed "
            "candidate_id=%s blob_path=%s error=%s",
            candidate_id, blob_path, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to generate resume access URL. Please try again.",
        ) from exc

    logger.info(
        "event=resume_service.url_generated "
        "candidate_id=%s recruiter_id=%s action=%s effective=%s",
        candidate_id, recruiter_id, action, effective_action,
    )

    # ── Step 6: Build response dict ───────────────────────────────────────────
    response: dict[str, Any] = {
        "candidateId":      candidate_id,
        "url":              url,
        "expiresInSeconds": _SIGNED_URL_EXPIRY_SECONDS,
        "fileType":         content_type,
        "filename":         filename,
        "action":           effective_action,
    }
    if note:
        response["note"] = note

    return response
