"""
app/models/candidate_response.py

Frontend-safe API response contract for the Candidate detail endpoint.

Separation of concerns
----------------------
* ``app/models/candidate.py``  → Internal domain model (MongoDB shape). NEVER modified here.
* ``app/models/candidate_response.py`` → API surface only. camelCase, filtered, safe.

What this layer does
--------------------
1. Converts snake_case MongoDB field names → camelCase API field names.
2. Excludes internal/sensitive fields:
     - recruiter_id
     - raw_email (full object)
     - email.message_id
     - resume.original.blob_path
     - resume.extracted.text_blob_path
3. Applies recursive camelCase conversion to the dynamic ``metadata``,
   ``skills``, and ``qa`` dicts (keys are parser-generated snake_case strings).
4. Provides a single ``CandidateDetailResponse.from_mongo_doc()`` factory
   that maps a raw MongoDB dict → a typed, serialisable response object.
5. Provides blacklist-related models for PATCH /blacklist and /unblacklist
   endpoints:
   - ``BlacklistRequest``         → request body (reason field)
   - ``BlacklistInfoResponse``    → embedded blacklist badge in GET detail
   - ``CandidateBlacklistResponse`` → response from PATCH endpoints

The ``email.sender`` field is stored in MongoDB as the key ``"from"``
(Python reserved word). It is exposed in the API response as ``"from"``
via ``serialization_alias``. The route must set ``response_model_by_alias=True``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ══════════════════════════════════════════════════════════════════════════════
# camelCase conversion utilities (for dynamic dict fields)
# ══════════════════════════════════════════════════════════════════════════════

def _snake_to_camel(name: str) -> str:
    """
    Convert a single snake_case identifier to camelCase.

    Uses ``capitalize()`` (not ``title()``) to avoid uppercasing letters
    after digits — preserving identifiers like ``c2h`` → ``C2h``.

    Examples::

        is_ok_client   → isOkClient
        is_c2h_ok      → isC2hOk
        notice_period_days → noticePeriodDays
    """
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camelise(value: Any) -> Any:
    """
    Recursively convert all dict keys from snake_case to camelCase.

    Traverses nested dicts and lists so that deeply nested parser output
    (e.g. ``qa``, ``metadata``) is fully converted.
    """
    if isinstance(value, dict):
        return {_snake_to_camel(k): _camelise(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelise(item) for item in value]
    return value


# ══════════════════════════════════════════════════════════════════════════════
# Resume sub-response models
# ══════════════════════════════════════════════════════════════════════════════

class ResumeOriginalResponse(BaseModel):
    """
    Metadata for the original binary resume file.

    ``blob_path`` is intentionally excluded — it is an internal storage key
    that the frontend has no use for and must never be exposed.
    """

    filename:   Optional[str]      = None
    fileType:   Optional[str]      = None
    sizeBytes:  Optional[int]      = None
    uploadedAt: Optional[datetime] = None


class ResumeExtractedResponse(BaseModel):
    """
    Metadata for the extracted plain-text representation.

    ``text_blob_path`` is intentionally excluded — internal storage concern.
    """

    charCount:        Optional[int]      = None
    extractedAt:      Optional[datetime] = None
    extractor:        Optional[str]      = None
    extractorVersion: Optional[str]      = None
    language:         Optional[str]      = None


class ResumeProcessingResponse(BaseModel):
    """Resume extraction pipeline operational state (retry tracking)."""

    attempts:      int                = 0
    maxAttempts:   int                = 3
    lastAttemptAt: Optional[datetime] = None
    lastError:     Optional[str]      = None
    # queued_at excluded — internal pipeline timing, not useful to frontend


class ResumeResponse(BaseModel):
    """Top-level resume envelope without any internal storage paths."""

    status:     str                     = "missing"
    original:   ResumeOriginalResponse  = Field(default_factory=ResumeOriginalResponse)
    extracted:  ResumeExtractedResponse = Field(default_factory=ResumeExtractedResponse)
    processing: ResumeProcessingResponse = Field(default_factory=ResumeProcessingResponse)


# ══════════════════════════════════════════════════════════════════════════════
# Email sub-response model
# ══════════════════════════════════════════════════════════════════════════════

class EmailResponse(BaseModel):
    """
    Header-level email metadata safe for frontend consumption.

    ``message_id`` is excluded — it is an internal Gmail API identifier.

    The ``sender`` field is stored in MongoDB under the key ``"from"``
    (a Python reserved word). It is exposed in API responses as ``"from"``
    via ``serialization_alias``. The route must use ``response_model_by_alias=True``
    for this alias to take effect.
    """

    subject:   str = ""
    sender:    str = Field(default="", serialization_alias="from")
    timestamp: str = ""


# ══════════════════════════════════════════════════════════════════════════════
# Processing state sub-response model
# ══════════════════════════════════════════════════════════════════════════════

class ProcessingResponse(BaseModel):
    """Candidate parsing pipeline flags — frontend uses these for status display."""

    parsed:        bool      = False
    preprocessed:  bool      = False
    parserVersion: str       = "0.0.0"
    parseErrors:   list[str] = Field(default_factory=list)
    needsReview:   bool      = False


# ══════════════════════════════════════════════════════════════════════════════
# Blacklist models
# ══════════════════════════════════════════════════════════════════════════════

class BlacklistRequest(BaseModel):
    """
    Request body for ``PATCH /candidates/{candidate_id}/blacklist``.

    ``reason`` is optional — recruiters can blacklist without typing a reason,
    though providing one is strongly recommended for audit purposes.
    """

    reason: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Why this candidate is being blacklisted (e.g. 'Fake resume', 'Duplicate').",
    )


class BlacklistInfoResponse(BaseModel):
    """
    Blacklist badge embedded in ``GET /candidates/{candidate_id}`` responses.

    Always returned — ``isBlacklisted=False`` for active candidates.
    Allows the frontend to render a 'BLACKLISTED' badge without a second request.
    Includes ``restoredAt`` so the UI can show the full history timeline.
    """

    isBlacklisted: bool               = False
    reason:        Optional[str]      = None
    blacklistedAt: Optional[datetime] = None
    source:        str                = "recruiter"
    restoredAt:    Optional[datetime] = None

    @classmethod
    def from_mongo_subdoc(cls, raw: dict[str, Any] | None) -> "BlacklistInfoResponse":
        """Map a MongoDB ``blacklist`` sub-document (snake_case) to this model."""
        blacklist_raw = raw or {}
        return cls(
            isBlacklisted=blacklist_raw.get("is_blacklisted", False),
            reason=       blacklist_raw.get("reason"),
            blacklistedAt=blacklist_raw.get("blacklisted_at"),
            source=       blacklist_raw.get("source", "recruiter"),
            restoredAt=   blacklist_raw.get("restored_at"),
        )


class CandidateBlacklistResponse(BaseModel):
    """
    Response returned by both:
    - ``PATCH /candidates/{candidate_id}/blacklist``
    - ``PATCH /candidates/{candidate_id}/unblacklist``

    ``success`` is always ``True`` on a 200 response.
    ``isBlacklisted`` reflects the **new** state after the operation.
    ``blacklistedAt`` is ``None`` after an unblacklist (the candidate is active again).
    """

    success:       bool               = True
    candidateId:   str
    isBlacklisted: bool
    reason:        Optional[str]      = None
    blacklistedAt: Optional[datetime] = None
    message:       str


# ══════════════════════════════════════════════════════════════════════════════
# Top-level candidate detail response
# ══════════════════════════════════════════════════════════════════════════════

class CandidateDetailResponse(BaseModel):
    """
    Frontend-safe API response shape for GET /candidates/{candidate_id}.

    Fields intentionally excluded from the MongoDB document:
    - ``recruiter_id``                  — backend isolation key, not frontend concern
    - ``raw_email``                     — temporary debug storage, PII-heavy
    - ``email.message_id``              — internal Gmail API identifier
    - ``resume.original.blob_path``     — internal storage key
    - ``resume.extracted.text_blob_path`` — internal storage key

    Dynamic dict fields (``metadata``, ``skills``, ``qa``) have their keys
    recursively converted from snake_case to camelCase at mapping time.

    ``blacklist`` is always included — active candidates have
    ``blacklist.isBlacklisted=False``.  The frontend uses this sub-object to
    render a 'BLACKLISTED' badge without a second request.
    """

    candidateId: str
    source:      str           = "gmail_nvite"
    jobId:       Optional[str] = None

    # Dynamic dicts — keys are camelCase-converted from parser output
    metadata:    dict[str, Any] = Field(default_factory=dict)
    skills:      dict[str, Any] = Field(default_factory=dict)
    qa:          dict[str, Any] = Field(default_factory=dict)

    # Structured sub-objects
    resume:     ResumeResponse
    email:      EmailResponse
    processing: ProcessingResponse  = Field(default_factory=ProcessingResponse)
    blacklist:  BlacklistInfoResponse = Field(default_factory=BlacklistInfoResponse)

    # Timestamps
    createdAt: datetime
    updatedAt: datetime

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_mongo_doc(cls, doc: dict[str, Any]) -> "CandidateDetailResponse":
        """
        Map a raw MongoDB candidate document to a ``CandidateDetailResponse``.

        Handles:
        - Field exclusion (recruiter_id, raw_email, blob paths, message_id)
        - camelCase conversion of dynamic dict keys (metadata, skills, qa)
        - Nested sub-object mapping with null-safe .get() access
        - The ``email.from`` → ``email.sender`` Python name mapping
        - ``blacklist`` sub-document mapping (always present; defaults to inactive)

        Args:
            doc: Raw dict returned by Motor ``find_one()``. May contain all
                 MongoDB fields except those already excluded by the service
                 layer projection.

        Returns:
            A fully populated ``CandidateDetailResponse`` instance.
        """
        # ── Extract sub-dicts with null guards ────────────────────────────────────
        email_raw     = doc.get("email")      or {}
        resume_raw    = doc.get("resume")     or {}
        orig_raw      = resume_raw.get("original")   or {}
        ext_raw       = resume_raw.get("extracted")  or {}
        rproc_raw     = resume_raw.get("processing") or {}   # resume extraction state
        cproc_raw     = doc.get("processing") or {}          # candidate parsing state
        blacklist_raw = doc.get("blacklist")  or {}          # blacklist state

        return cls(
            candidateId=doc.get("candidate_id", ""),
            source=     doc.get("source", "gmail_nvite"),
            jobId=      doc.get("job_id"),

            # Dynamic dicts — keys recursively camelCased
            metadata=_camelise(doc.get("metadata") or {}),
            skills=  _camelise(doc.get("skills")   or {}),
            qa=      _camelise(doc.get("qa")        or {}),

            resume=ResumeResponse(
                status=    resume_raw.get("status", "missing"),
                original=ResumeOriginalResponse(
                    filename=   orig_raw.get("filename"),
                    fileType=   orig_raw.get("file_type"),    # snake → camel via model field name
                    sizeBytes=  orig_raw.get("size_bytes"),
                    uploadedAt= orig_raw.get("uploaded_at"),
                    # blob_path excluded
                ),
                extracted=ResumeExtractedResponse(
                    charCount=        ext_raw.get("char_count"),
                    extractedAt=      ext_raw.get("extracted_at"),
                    extractor=        ext_raw.get("extractor"),
                    extractorVersion= ext_raw.get("extractor_version"),
                    language=         ext_raw.get("language"),
                    # text_blob_path excluded
                ),
                processing=ResumeProcessingResponse(
                    attempts=     rproc_raw.get("attempts",      0),
                    maxAttempts=  rproc_raw.get("max_attempts",  3),
                    lastAttemptAt=rproc_raw.get("last_attempt_at"),
                    lastError=    rproc_raw.get("last_error"),
                ),
            ),

            email=EmailResponse(
                subject=  email_raw.get("subject",   ""),
                sender=   email_raw.get("from",      ""),   # "from" is the MongoDB key
                timestamp=email_raw.get("timestamp", ""),
                # message_id excluded
            ),

            processing=ProcessingResponse(
                parsed=       cproc_raw.get("parsed",        False),
                preprocessed= cproc_raw.get("preprocessed",  False),
                parserVersion=cproc_raw.get("parser_version","0.0.0"),
                parseErrors=  cproc_raw.get("parse_errors",  []),
                needsReview=  cproc_raw.get("needs_review",  False),
            ),

            blacklist=BlacklistInfoResponse.from_mongo_subdoc(blacklist_raw),

            createdAt=doc.get("created_at", datetime.utcnow()),
            updatedAt=doc.get("updated_at", datetime.utcnow()),
        )


# ══════════════════════════════════════════════════════════════════════════════
# ATS score response models
# ══════════════════════════════════════════════════════════════════════════════

class AtsScoreBreakdownResponse(BaseModel):
    """
    Detailed ATS scoring breakdown for the candidate profile page.

    All fields map directly from the ``score_breakdown`` sub-document stored
    in ``candidate_job_scores``.  That document uses snake_case keys (written
    by ``compute_ats_score()`` in ``ats_scoring.py``); the field names below
    are the camelCase equivalents exposed in the API.

    ``llm_evaluation`` is intentionally excluded — it is a raw, large debug
    payload with per-requirement LLM evidence strings.  It has no use in
    frontend display and would bloat the response.
    """

    rawScore:         int        = 0
    finalScore:       int        = 0
    criticalRatio:    float      = 0.0
    matchedCritical:  int        = 0
    totalCritical:    int        = 0
    matchedSkills:    list[str]  = Field(default_factory=list)
    missingSkills:    list[str]  = Field(default_factory=list)
    postProcessRules: list[str]  = Field(default_factory=list)
    jdMinExp:         int        = 0
    experienceYears:  int        = 0

    @classmethod
    def from_score_doc(cls, breakdown: dict[str, Any]) -> "AtsScoreBreakdownResponse":
        """
        Map a raw ``score_breakdown`` dict (snake_case MongoDB keys) to this model.

        Null-safe: missing keys default to zero / empty-list.  This guards
        against older score records that pre-date any breakdown field additions.

        Args:
            breakdown: The ``score_breakdown`` sub-dict from a
                       ``candidate_job_scores`` document.

        Returns:
            A fully populated ``AtsScoreBreakdownResponse``.
        """
        if not breakdown or not isinstance(breakdown, dict):
            return cls()

        return cls(
            rawScore=        int(breakdown.get("raw_score",         0) or 0),
            finalScore=      int(breakdown.get("final_score",       0) or 0),
            criticalRatio=   float(breakdown.get("critical_ratio",  0.0) or 0.0),
            matchedCritical= int(breakdown.get("matched_critical",  0) or 0),
            totalCritical=   int(breakdown.get("total_critical",    0) or 0),
            matchedSkills=   list(breakdown.get("matched_skills",   []) or []),
            missingSkills=   list(breakdown.get("missing_skills",   []) or []),
            postProcessRules=list(breakdown.get("post_process_rules",[]) or []),
            jdMinExp=        int(breakdown.get("jd_min_exp",        0) or 0),
            experienceYears= int(breakdown.get("experience_years",  0) or 0),
        )


# ── ATS score status constants ─────────────────────────────────────────────────
# All valid values for AtsScoreResponse.status.
# "not_scored" is a synthetic state produced by the service layer when no
# candidate_job_scores record exists for the (candidate_id, job_id) pair.
# All other values come directly from the stored document.
ATS_STATUS_COMPLETED  = "completed"
ATS_STATUS_PROCESSING = "processing"
ATS_STATUS_FAILED     = "failed"
ATS_STATUS_SKIPPED    = "skipped"
ATS_STATUS_NOT_SCORED = "not_scored"


class AtsScoreResponse(BaseModel):
    """
    ATS scoring state for a specific (candidate, job) pair.

    Returned by ``GET /candidates/{candidate_id}/ats-score?jobId=DBA002``.

    **Status semantics**:
      ``completed``  — Score computed and stored. ``score`` and ``scoreBreakdown``
                       are populated. ``isStale`` may be ``True`` if the JD was
                       re-analysed after scoring.
      ``processing`` — ATS batch is mid-flight for this candidate. Poll every 5s.
      ``failed``     — LLM or scoring error for this candidate. No score.
      ``skipped``    — Candidate had no processable resume. No score. Retrying
                       ATS will not help until the resume pipeline completes.
      ``not_scored`` — No ``candidate_job_scores`` record exists yet. Batch ATS
                       has not been triggered, or this candidate arrived after
                       the last batch run. Use the job-level ATS trigger to score.

    **isStale semantics**:
      ``True`` only when ``status == "completed"`` AND the score was computed
      against an older ``jd_analysis.version`` than the job currently has.
      Indicates the JD was re-analysed after scoring — a fresh ATS run will
      re-score this candidate against the updated JD.
      Always ``False`` for non-completed states.
    """

    candidateId:       str
    jobId:             str
    score:             Optional[float]                    = None
    status:            str                                          # see status semantics above
    scoredAt:          Optional[datetime]                 = None
    jdAnalysisVersion: Optional[int]                      = None
    isStale:           bool                               = False
    scoreBreakdown:    Optional[AtsScoreBreakdownResponse] = None

