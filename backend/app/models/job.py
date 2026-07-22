"""
app/models/job.py

Pydantic domain models for the Job entity.

Separation of concerns:
  JobStatus     → Lifecycle enum for a job posting
  JobFilters    → Filter thresholds set by recruiter at job creation
  JobCreate     → Frontend payload (POST /jobs) — accepts camelCase
  JobUpdate     → Payload for PATCH /jobs/{job_id} (status and/or notice period)
  JobDocument   → Full MongoDB document (internal use only)
  JobCounts     → Candidate counts sub-model
  ImportableJob → Job discovery row for Create Job auto-fill (GET /jobs/importable)
  JobResponse   → Public API response shape
  CandidateSummary      → Lightweight candidate projection for list views
  CandidateListResponse → Paginated candidate list envelope
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.candidate_response import BlacklistInfoResponse

#for to convert camel case to snake case and vice versa
def to_camel(string: str) -> str:
    """
    Convert snake_case → camelCase for API responses.
    Example:
        employment_type -> employmentType
        created_at -> createdAt
    """
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════

class JobStatus(str, Enum):
    """Lifecycle state of a job posting."""
    active = "active"
    paused = "paused"
    closed = "closed"


# ══════════════════════════════════════════════════════════════════════════════
# Filter sub-models
# ══════════════════════════════════════════════════════════════════════════════

class JobFilters(BaseModel):
    """
    Recruiter-defined filter thresholds stored on the job document.

    The three fixed fields (is_ok_client, is_c2h_ok, has_pf_account) represent
    mandatory screening criteria that are always enforced. They are stored
    explicitly so the filter-query builder remains generic and ATS-compatible
    in future phases.

    max_notice_period_days is the only dynamic threshold — set by the recruiter
    at job creation and updatable via PATCH /jobs/{job_id}/filters.
    """

    is_ok_client:           bool = True
    is_c2h_ok:              bool = True
    has_pf_account:         bool = True
    max_notice_period_days: int  = Field(..., ge=0, description="Max acceptable notice period in days.")


    model_config = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel
    )

# ══════════════════════════════════════════════════════════════════════════════
# Request models
# ══════════════════════════════════════════════════════════════════════════════

class JobCreate(BaseModel):
    """
    Validated payload received from the frontend when creating a job.

    Accepts camelCase keys matching the existing frontend contract.
    Both camelCase (alias) and snake_case are accepted so internal
    code can construct this model directly without aliases.

    The job_id field (frontend: "jobId") is normalised to uppercase
    at validation time to match the format stored on candidate documents.
    """

    title:                  str
    job_id:                 str = Field(..., alias="jobId",              description="Naukri job code, e.g. 'DBA002'.")
    description:            str = Field("",  description="Full JD text — stored for future ATS scoring.")
    location:               str = ""
    employment_type:        str = Field("",  alias="employmentType")
    priority:               str = "Medium"
    experience:             str = ""
    max_notice_period_days: int = Field(..., alias="maxNoticePeriodDays", ge=0,
                                        description="Max acceptable notice period in days — sets the dynamic filter threshold.")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("job_id", mode="before")
    @classmethod
    def normalise_job_id(cls, v: str) -> str:
        """Normalise Naukri job code to uppercase to match candidate documents."""
        if not isinstance(v, str) or not v.strip():
            raise ValueError("job_id must be a non-empty string.")
        return v.strip().upper()


class JobUpdate(BaseModel):
    """
    Payload accepted by PATCH /jobs/{job_id}.

    All fields are optional — supply only what needs changing.
    At least one field must be provided.

    JD-affecting fields (title, description, employment_type):
        Changing any of these resets jd_analysis to ``pending`` and
        automatically re-enqueues JD analysis.  Existing ATS scores become
        version-stale and are re-scored on the next ``calculate-ats`` call.

        If description is cleared to an empty string, jd_analysis is set to
        ``not_available`` and no re-analysis is enqueued.

    Safe fields:
        All other fields take effect immediately with no downstream impact
        on JD analysis or ATS scoring.
    """

    # ── JD-affecting fields ───────────────────────────────────────────────────
    title:           Optional[str] = Field(
        None,
        description="Job title. Changing this triggers JD re-analysis.",
    )
    description:     Optional[str] = Field(
        None,
        description="Full JD text. Changing this triggers JD re-analysis. "
                    "Pass empty string to clear (sets jd_analysis to not_available).",
    )
    employment_type: Optional[str] = Field(
        None, alias="employmentType",
        description="Employment type (e.g. Full-time, Contract). "
                    "Changing this triggers JD re-analysis.",
    )

    # ── Safe fields — no downstream ATS/JD impact ─────────────────────────────
    location:               Optional[str]       = None
    priority:               Optional[str]       = None
    experience:             Optional[str]       = None
    status:                 Optional[JobStatus] = None
    max_notice_period_days: Optional[int]       = Field(
        None, ge=0, alias="maxNoticePeriodDays",
        description="Updated notice period filter threshold in days.",
    )
    is_archived:            Optional[bool]      = Field(
        None, alias="isArchived",
        description="Set true to archive (hide) this job — e.g. wrong job code. "
                    "Set false to restore it. Archived jobs do not appear in the "
                    "default job list and cannot trigger ATS scoring.",
    )

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("title", mode="before")
    @classmethod
    def title_not_empty(cls, v: Any) -> Any:
        """Reject empty or whitespace-only title strings."""
        if v is not None:
            if not isinstance(v, str) or not v.strip():
                raise ValueError("title cannot be an empty string.")
        return v

    @model_validator(mode="after")
    def at_least_one_field(self) -> "JobUpdate":
        """Reject empty PATCH payloads at validation time."""
        values = [
            self.title, self.description, self.employment_type,
            self.location, self.priority, self.experience,
            self.status, self.max_notice_period_days, self.is_archived,
        ]
        if all(v is None for v in values):
            raise ValueError("At least one field must be provided.")
        return self


# ══════════════════════════════════════════════════════════════════════════════
# Document model
# ══════════════════════════════════════════════════════════════════════════════

class JobDocument(BaseModel):
    """
    Full MongoDB document model for a Job.
    Must never be serialised directly into an API response.
    """

    job_id:          str
    recruiter_id:    str
    title:           str
    description:     str         = ""
    location:        str         = ""
    employment_type: str         = ""
    priority:        str         = "Medium"
    experience:      str         = ""
    filters:         JobFilters
    status:          JobStatus   = JobStatus.active
    is_archived:     bool        = False
    created_at:      datetime    = Field(default_factory=datetime.utcnow)
    updated_at:      datetime    = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(use_enum_values=True)

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> "JobDocument":
        """Construct from a raw Motor result dict, stripping MongoDB _id."""
        doc.pop("_id", None)
        return cls(**doc)


# ══════════════════════════════════════════════════════════════════════════════
# Response models
# ══════════════════════════════════════════════════════════════════════════════

class JobCounts(BaseModel):
    """Candidate counts attached to job responses."""

    total:    int = 0
    filtered: int = 0

    model_config = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel
)


class ImportableJob(BaseModel):
    """
    A job code discovered from ingested candidate emails that has not yet
    been created in the ``jobs`` collection for this recruiter.

    Used by ``GET /jobs/importable`` so the Create Job page can auto-fill
    Job ID and Job Title.  Does not create a job — ``POST /jobs`` is unchanged.

    ``candidate_count`` is an informational snapshot at request time only.
    It is not persisted on the Job document and must not drive business logic.
    """

    job_id:          str           = Field(..., description="Naukri job code, e.g. 'DBA003'.")
    job_title:       Optional[str] = Field(
        None,
        description="Best-effort title from candidate metadata.job_title. May be null.",
    )
    candidate_count: int           = Field(
        ...,
        ge=0,
        description=(
            "Informational snapshot of how many candidate documents currently "
            "share this job_id for the recruiter. Not stored on the Job; "
            "not used for create validation or other business logic."
        ),
    )

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )


class JobResponse(BaseModel):
    """
    Public-safe job profile returned by all job API endpoints.
    Includes optional candidate counts for list and detail views.
    """

    job_id:          str
    # recruiter_id:    str
    title:           str
    description:     str               = ""
    location:        str               = ""
    employment_type: str               = ""
    priority:        str               = "Medium"
    experience:      str               = ""
    filters:         JobFilters
    status:          str
    is_archived:     bool               = False
    counts:          Optional[JobCounts] = None
    created_at:      datetime
    updated_at:      datetime

    model_config = ConfigDict(
    use_enum_values=True,
    populate_by_name=True,
    alias_generator=to_camel
   )

# ══════════════════════════════════════════════════════════════════════════════
# Candidate list projection models
# ══════════════════════════════════════════════════════════════════════════════

class CandidateSummary(BaseModel):
    """
    Lightweight candidate shape for job candidate list views.

    Maps nested MongoDB sub-documents (metadata, qa, resume, processing)
    to a flat, frontend-friendly response. Full candidate detail is available
    via GET /candidates/{candidate_id}.
    """

    candidate_id:    str
    name:            Optional[str]   = None
    current_role:    Optional[str]   = None
    current_company: Optional[str]   = None
    experience_years: Optional[float] = None
    notice_period_days: Optional[Any] = None   # normalized int or None
    current_ctc:     Optional[Any]   = None
    expected_ctc:    Optional[Any]   = None
    resume_status:   str             = "missing"
    needs_review:    bool            = False
    created_at:      datetime
    ats_score:       Optional[float] = None   # from candidate_job_scores; None if not yet scored
    ats_status:      Optional[str]   = None   # completed | failed | skipped | None
    blacklist:       Optional[BlacklistInfoResponse] = None

    model_config = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel
    )
    
    @classmethod
    def from_mongo_doc(
        cls,
        doc: dict[str, Any],
        *,
        ats_score:  Optional[float] = None,
        ats_status: Optional[str]   = None,
    ) -> "CandidateSummary":
        """Map a raw MongoDB candidate document to CandidateSummary.

        Args:
            doc:        Raw candidate dict from MongoDB.
            ats_score:  Merged from candidate_job_scores by the service layer.
            ats_status: Merged from candidate_job_scores by the service layer.
        """
        meta       = doc.get("metadata")    or {}
        qa         = doc.get("qa")          or {}
        resume     = doc.get("resume")      or {}
        processing = doc.get("processing")  or {}

        blacklist = (
            BlacklistInfoResponse.from_mongo_subdoc(doc.get("blacklist"))
            if "blacklist" in doc
            else None
        )

        return cls(
            candidate_id=     doc.get("candidate_id", ""),
            name=             meta.get("name"),
            current_role=     meta.get("current_role"),
            current_company=  meta.get("current_company"),
            experience_years= meta.get("experience_years"),
            notice_period_days= qa.get("notice_period_days"),
            current_ctc=      qa.get("current_ctc"),
            expected_ctc=     qa.get("expected_ctc"),
            resume_status=    resume.get("status", "missing"),
            needs_review=     processing.get("needs_review", False),
            created_at=       doc.get("created_at", datetime.utcnow()),
            ats_score=        ats_score,
            ats_status=       ats_status,
            blacklist=       blacklist,
        )


class CandidateListResponse(BaseModel):
    """Paginated envelope for GET /jobs/{job_id}/candidates."""

    job_id:     str
    view:       str
    total:      int
    filtered:   int
    page:       int
    limit:      int
    candidates: list[CandidateSummary]
    
    model_config = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel
   )


# ══════════════════════════════════════════════════════════════════════════════
# Delete response model
# ══════════════════════════════════════════════════════════════════════════════

class JobDeleteResponse(BaseModel):
    """
    Response returned by DELETE /jobs/{job_id}.

    ``success`` is always True on a 200 response — included so the frontend
    can check ``if (response.success)`` without parsing the message string.
    """

    success: bool = True
    job_id:  str
    message: str  = "Job deleted successfully."

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
    )
