"""
app/models/candidate.py

Pydantic domain models for the Candidate entity.

Schema notes
------------
* ``metadata`` and ``qa`` start as empty dicts; Phase 4 parsers will populate them.
* ``raw_email`` is TEMPORARY — stored only for Phase 2/3 debugging.
  It will be removed once parsing is stable.
* ``resume`` is null until a download/parse pipeline is implemented.
* ``processing.parsed`` drives the downstream normalisation queue.
* ``blacklist`` tracks whether a candidate has been marked as fake/invalid by the
  recruiter.  Fields are append-only: unblacklisting adds ``restored_at``/
  ``restored_by`` rather than nulling out the original blacklist fields, so the
  full audit trail is always preserved.

Separation of concerns:
  CandidateDocument  → Full MongoDB document (internal use only)
  CandidateCreate    → Validated construction payload passed to the store layer
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ══════════════════════════════════════════════════════════════════════════════
# Resume sub-models
# ══════════════════════════════════════════════════════════════════════════════

# Rejects absolute filesystem paths, cloud URIs, and signed/temporary URLs.
# Only relative paths are allowed, e.g. "resumes/<recruiter_id>/<candidate_id>/original.pdf".
_ABSOLUTE_OR_URI_RE = re.compile(
    r"^(?:[A-Za-z]:[/\\]|/|\\\\|https?://|gs://|s3://|ftp://)"
)


def _validate_relative_path(v: str | None) -> str | None:
    """
    Ensure a blob_path is a relative storage path.

    Rejects:
    * Absolute filesystem paths  (C:\\..., /home/...)
    * Cloud storage URIs         (gs://..., s3://...)
    * HTTP/HTTPS URLs            (https://...)
    * UNC paths                  (\\\\server\\...)
    """
    if v is not None and _ABSOLUTE_OR_URI_RE.match(v):
        raise ValueError(
            "blob_path must be a relative path — "
            "absolute paths, cloud URIs, and signed URLs are not allowed."
        )
    return v


class ResumeStatus(str, Enum):
    """
    Lifecycle states for resume processing.

    missing    — no resume attachment found in the NVite email.
    pending    — attachment detected; awaiting download and storage write.
    uploaded   — binary file written to storage; text extraction not yet started.
    processing — text extraction job is in progress.
    completed  — text extracted and stored; ready for downstream NLP.
    failed     — extraction failed; see ResumeProcessing.last_error.
    """

    missing    = "missing"
    pending    = "pending"
    uploaded   = "uploaded"
    processing = "processing"
    completed  = "completed"
    failed     = "failed"


class ResumeOriginal(BaseModel):
    """
    Metadata for the original binary resume file stored in the blob store.

    ``blob_path`` is a *relative* storage key, e.g.::

        resumes/<recruiter_id>/<candidate_id>/original.pdf

    The storage root (local directory or GCS bucket) is resolved at runtime
    from ``settings.storage_root``.  Migrating to a new provider requires
    only a config change — no schema changes.
    """

    blob_path:   str | None      = Field(default=None, description="Relative storage key for the binary file.")
    filename:    str | None      = Field(default=None, description="Original filename from the email attachment.")
    file_type:   str | None      = Field(default=None, description="MIME type, e.g. 'application/pdf'.")
    size_bytes:  int | None      = Field(default=None, description="File size in bytes for integrity checks.")
    uploaded_at: datetime | None = Field(default=None, description="Timestamp when binary was written to storage.")

    @field_validator("blob_path", mode="before")
    @classmethod
    def _blob_path_relative(cls, v: str | None) -> str | None:
        return _validate_relative_path(v)


class ResumeExtracted(BaseModel):
    """
    Metadata for the extracted plain-text representation of the resume.

    Only metadata is stored here — the raw text lives in ``text_blob_path``
    inside the blob store.  This keeps MongoDB documents small and avoids
    storing sensitive PII text in the primary database.
    """

    text_blob_path:    str | None      = Field(default=None, description="Relative storage key for the extracted text file.")
    char_count:        int | None      = Field(default=None, description="Character count of extracted text.")
    extracted_at:      datetime | None = Field(default=None)
    extractor:         str | None      = Field(default=None, description="Engine used: 'pdfminer', 'pytesseract', 'google_doc_ai', etc.")
    extractor_version: str | None      = Field(default=None, description="Semver of the extraction engine.")
    language:          str | None      = Field(default=None, description="ISO 639-1 language code, e.g. 'en'.")

    @field_validator("text_blob_path", mode="before")
    @classmethod
    def _text_blob_path_relative(cls, v: str | None) -> str | None:
        return _validate_relative_path(v)


class ResumeProcessing(BaseModel):
    """
    Operational metadata for the resume extraction pipeline.

    Tracks retry state so that a background worker can pick up failed
    extractions without re-processing already-completed ones.
    """

    attempts:        int            = Field(default=0,  description="Number of extraction attempts made.")
    max_attempts:    int            = Field(default=3,  description="Maximum retries before status → failed.")
    queued_at:       datetime | None = Field(default=None, description="When the extraction job was enqueued.")
    last_attempt_at: datetime | None = Field(default=None, description="Timestamp of the most recent attempt.")
    last_error:      str | None      = Field(default=None, description="Exception message from the last failed attempt.")


class ResumeMetadata(BaseModel):
    """
    Top-level resume object stored on the candidate document.

    Provider-agnostic: blob paths are relative keys; the storage backend
    (local filesystem today, GCS tomorrow) is resolved by the service layer.
    """

    status:     ResumeStatus     = Field(default=ResumeStatus.missing)
    original:   ResumeOriginal   = Field(default_factory=ResumeOriginal)
    extracted:  ResumeExtracted  = Field(default_factory=ResumeExtracted)
    processing: ResumeProcessing = Field(default_factory=ResumeProcessing)


# ══════════════════════════════════════════════════════════════════════════════
# Email / processing sub-models
# ══════════════════════════════════════════════════════════════════════════════

class CandidateEmailMeta(BaseModel):
    """Header-level metadata extracted from the inbound email."""

    message_id: str = Field(..., description="Gmail API message ID.")
    timestamp:  str = Field("", description="RFC 2822 date string from the email header.")
    subject:    str = Field("", description="Email subject line.")
    sender:     str = Field("", alias="from", description="Sender address (From header).")

    model_config = {"populate_by_name": True}


class CandidateProcessingState(BaseModel):
    """
    Processing pipeline flags.

    ``parsed``        — True once the Q&A extractor has run.
    ``preprocessed``  — True once normalisation (NLP pre-processing) has run.
    ``parser_version``— Semver string of the parser that last ran.
    ``parse_errors``  — List of non-fatal parse warnings.
    ``needs_review``  — Flagged for human review (e.g. low-confidence parse).
    """

    parsed:          bool       = False
    preprocessed:    bool       = False
    parser_version:  str        = "0.0.0"
    parse_errors:    list[str]  = Field(default_factory=list)
    needs_review:    bool       = False


class CandidateRawEmail(BaseModel):
    """
    Temporary raw email storage for Phase 2/3 debugging.

    ⚠️  This field will be REMOVED once the parsing pipeline is stable.
        Do NOT build any downstream logic that depends on it.
    """

    body:        str                    = ""
    clean_text:  str                    = ""
    attachments: list[dict[str, str]]   = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# Blacklist sub-model
# ══════════════════════════════════════════════════════════════════════════════

class CandidateBlacklist(BaseModel):
    """
    Soft-blacklist state for a candidate.

    Design rules
    ------------
    * Fields are **append-only** — unblacklisting never nulls out the original
      ``reason``, ``blacklisted_at``, or ``blacklisted_by``.  Full history is
      always preserved for audit purposes.
    * ``is_blacklisted=False`` with a non-null ``reason`` means the candidate
      was blacklisted and then restored — the reason explains *why* they were
      originally flagged.
    * ``source`` is set to ``"recruiter"`` by this service.  Reserved values
      for future automated systems: ``"admin"``, ``"fraud_detector"``,
      ``"duplicate_detector"``.

    MongoDB ``$ne: True`` on a missing ``blacklist`` field evaluates to ``True``,
    so existing documents without this sub-doc are treated as active candidates
    automatically — no backfill migration is needed.
    """

    is_blacklisted: bool            = Field(
        default=False,
        description="True when the candidate is currently blacklisted.",
    )
    reason:         str | None      = Field(
        default=None,
        description="Free-text reason supplied by the recruiter (e.g. 'Fake resume').",
        max_length=500,
    )
    blacklisted_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the candidate was blacklisted.",
    )
    blacklisted_by: str | None      = Field(
        default=None,
        description="recruiter_id who performed the blacklist action.",
    )
    source:         str             = Field(
        default="recruiter",
        description="Origin of the blacklist action. Reserved: 'admin', 'fraud_detector', 'duplicate_detector'.",
    )
    # ── Restoration audit (never null out the fields above) ───────────────────
    restored_at:    datetime | None = Field(
        default=None,
        description="UTC timestamp when the blacklist was reversed.",
    )
    restored_by:    str | None      = Field(
        default=None,
        description="recruiter_id who restored the candidate.",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Main document model
# ══════════════════════════════════════════════════════════════════════════════

class CandidateDocument(BaseModel):
    """
    Full MongoDB document model for a candidate.

    Must never be serialised directly into an API response —
    use a dedicated response projection model instead.
    """

    candidate_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID4 string — primary key.",
    )
    recruiter_id: str = Field(
        ...,
        description="UUID of the recruiter whose inbox yielded this candidate.",
    )
    source: str = Field(
        default="gmail_nvite",
        description="Ingestion source identifier.",
    )
    job_id: str | None = Field(
        default=None,
        description="Job posting ID from NVite body title, e.g. 'DBA002'. Groups candidates by posting.",
    )

    # ── Structured fields (populated in Phase 4) ──────────────────────────────
    metadata: dict[str, Any]  = Field(
        default_factory=dict,
        description="Extracted candidate profile fields (name, phone, etc.).",
    )
    skills: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted candidate skills. 'raw' key holds list of skill strings.",
    )
    qa: dict[str, Any] = Field(
        default_factory=dict,
        description="Extracted Q&A pairs from the email body.",
    )
    resume: ResumeMetadata = Field(
        default_factory=ResumeMetadata,
        description="Resume lifecycle metadata. Binary and extracted text live in blob storage; only paths stored here.",
    )

    # ── Email provenance ──────────────────────────────────────────────────────
    email: CandidateEmailMeta

    # ── Processing pipeline state ─────────────────────────────────────────────
    processing: CandidateProcessingState = Field(
        default_factory=CandidateProcessingState
    )

    # ── Temporary debug storage (removed after parsing is stable) ─────────────
    raw_email: CandidateRawEmail = Field(
        default_factory=CandidateRawEmail
    )

    # ── Blacklist state ───────────────────────────────────────────────────────
    blacklist: CandidateBlacklist = Field(
        default_factory=CandidateBlacklist,
        description="Soft-blacklist metadata. Active candidates have is_blacklisted=False.",
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {"arbitrary_types_allowed": True}

    def to_mongo_dict(self) -> dict[str, Any]:
        """
        Serialise to a plain dict suitable for ``collection.insert_one()``.

        Uses ``by_alias=True`` so the ``from`` alias in ``CandidateEmailMeta``
        is respected in the stored document.
        """
        return self.model_dump(by_alias=True)
