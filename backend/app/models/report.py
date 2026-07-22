"""
app/models/report.py

Pydantic domain models for the Daily Reports module.

Separation of concerns:
  Enums / nested value objects  → Shared vocabulary (stored + API)
  *Document / payload shapes    → MongoDB document (internal; never sent raw)
  *Create / *Update / *Open     → Request payloads (camelCase accepted)
  *Response                     → Public API projection (camelCase aliases)

Phase 1 supports two report kinds under one aggregate:
  - recruiter → transactional candidate submission entries
  - lead      → structured summary metrics + text item collections

Identity (recruiter_id / name / email) is never accepted from the client
for authorship; services resolve it from the authenticated user.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


def to_camel(string: str) -> str:
    """Convert snake_case → camelCase for API responses."""
    parts = string.split("_")
    return parts[0] + "".join(word.capitalize() for word in parts[1:])


_CAMEL_CONFIG = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel,
)

# Nested domain sections used both in Mongo (snake_case) and API input (camelCase).
_CAMEL_ENUM_CONFIG = ConfigDict(
    populate_by_name=True,
    alias_generator=to_camel,
    use_enum_values=True,
)

# Permissive contact-number check only — no country-specific length/format rules.
_CONTACT_NUMBER_RE = re.compile(r"^\+?[\d\s\-()]{7,20}$")

_EMAIL_ADAPTER: TypeAdapter[EmailStr] = TypeAdapter(EmailStr)


def _validate_optional_email(v: Any) -> Any:
    """
    Allow null/blank for draft rows; when present, validate via Pydantic EmailStr.
    """
    if v is None:
        return None
    if not isinstance(v, str) or not v.strip():
        return None
    return str(_EMAIL_ADAPTER.validate_python(v.strip()))


# ══════════════════════════════════════════════════════════════════════════════
# Enums
# ══════════════════════════════════════════════════════════════════════════════

class ReportKind(str, Enum):
    """Business category of a daily report."""

    recruiter = "recruiter"
    lead = "lead"


class ReportStatus(str, Enum):
    """Lifecycle state of a daily report."""

    draft = "draft"
    sent = "sent"
    failed = "failed"


class SubmissionStatus(str, Enum):
    """
    Controlled status for a recruiter candidate-submission entry.

    Extra context (e.g. portal name) belongs in optional ``remarks``,
    not in this enum.
    """

    submitted = "submitted"
    on_hold = "on_hold"
    rejected = "rejected"
    client_review = "client_review"
    interview_scheduled = "interview_scheduled"
    offer_released = "offer_released"
    joined = "joined"


# ══════════════════════════════════════════════════════════════════════════════
# Nested value objects (shared by document + response)
# ══════════════════════════════════════════════════════════════════════════════

class ReportRecipients(BaseModel):
    """To / CC recipient lists."""

    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


class ReportDelivery(BaseModel):
    """
    Email delivery operational metadata.

    ``sent_at`` is successful provider delivery only.
    Business freeze time lives on the parent as ``submitted_at``.

    ``provider_message_id`` is typically null for Outlook: Microsoft Graph
    ``sendMail`` returns HTTP 202 Accepted with an empty body and does not
    expose a sent-message id.
    """

    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: str | None = None
    provider: str | None = None
    provider_message_id: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class RecruiterEntry(BaseModel):
    """
    One candidate-submission row on a recruiter daily report.

    ``job_id`` is a free-text business reference — not linked to the Jobs module.
    Incomplete rows are allowed while the parent report is ``draft`` (nulls OK).
    """

    entry_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str | None = None
    candidate_name: str | None = None
    job_name: str | None = None
    candidate_contact_number: str | None = None
    candidate_email: str | None = None
    poc: str | None = None
    client: str | None = None
    submission_status: SubmissionStatus | None = None
    remarks: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(use_enum_values=True)


class LeadTextItem(BaseModel):
    """One item in a lead-report text collection (activities / risks / plan)."""

    item_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(use_enum_values=True)


class LeadRecruitmentSummary(BaseModel):
    requirements_managed: int | None = Field(None, ge=0)

    model_config = _CAMEL_ENUM_CONFIG


class LeadTeamProfileReview(BaseModel):
    profiles_received: int | None = Field(None, ge=0)
    profiles_approved: int | None = Field(None, ge=0)
    profiles_rejected: int | None = Field(None, ge=0)

    model_config = _CAMEL_ENUM_CONFIG


class LeadRecruitmentDelivery(BaseModel):
    profiles_submitted: int | None = Field(None, ge=0)
    interviews: int | None = Field(None, ge=0)
    offers: int | None = Field(None, ge=0)
    joinings: int | None = Field(None, ge=0)

    model_config = _CAMEL_ENUM_CONFIG


class RecruiterReportPayload(BaseModel):
    """Payload discriminated by ``report_kind == recruiter``."""

    entries: list[RecruiterEntry] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


class LeadReportPayload(BaseModel):
    """Payload discriminated by ``report_kind == lead``."""

    recruitment_summary: LeadRecruitmentSummary = Field(
        default_factory=LeadRecruitmentSummary,
    )
    team_profile_review: LeadTeamProfileReview = Field(
        default_factory=LeadTeamProfileReview,
    )
    lead_recruitment_delivery: LeadRecruitmentDelivery = Field(
        default_factory=LeadRecruitmentDelivery,
    )
    key_activities: list[LeadTextItem] = Field(default_factory=list)
    challenges_risks: list[LeadTextItem] = Field(default_factory=list)
    plan_for_tomorrow: list[LeadTextItem] = Field(default_factory=list)

    model_config = ConfigDict(use_enum_values=True)


# ══════════════════════════════════════════════════════════════════════════════
# Document model (MongoDB)
# ══════════════════════════════════════════════════════════════════════════════

class DailyReportDocument(BaseModel):
    """
    Full MongoDB document for a daily report aggregate.

    Must never be serialised directly into an API response.
    ``payload`` is stored as a plain dict in Mongo; shape depends on
    ``report_kind`` (recruiter entries vs lead summary).
    """

    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    recruiter_id: str
    recruiter_name: str
    recruiter_email: str
    report_date: str = Field(..., description="Business date YYYY-MM-DD (Asia/Kolkata).")
    report_kind: ReportKind
    status: ReportStatus = ReportStatus.draft
    schema_version: int = Field(
        default=1,
        description=(
            "Document schema version for Daily Reports. "
            "Phase 1 documents use schema_version=1. "
            "Bump this when the persisted payload shape changes so migrations "
            "can detect and upgrade older documents safely."
        ),
    )
    recipients: ReportRecipients = Field(default_factory=ReportRecipients)
    recipients_snapshot: ReportRecipients | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery: ReportDelivery = Field(default_factory=ReportDelivery)
    submitted_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(use_enum_values=True)

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> "DailyReportDocument":
        """Construct from a raw Motor result dict, stripping MongoDB ``_id``."""
        doc = dict(doc)
        doc.pop("_id", None)
        return cls(**doc)

    def to_mongo(self) -> dict[str, Any]:
        """Serialise for ``insert_one`` / ``replace_one`` (exclude None snapshot only if desired)."""
        return self.model_dump(mode="python")


def empty_recruiter_payload() -> dict[str, Any]:
    """Initial payload skeleton for a new recruiter draft."""
    return RecruiterReportPayload().model_dump(mode="python")


def empty_lead_payload() -> dict[str, Any]:
    """Initial payload skeleton for a new lead draft."""
    return LeadReportPayload().model_dump(mode="python")


# ══════════════════════════════════════════════════════════════════════════════
# Request models
# ══════════════════════════════════════════════════════════════════════════════

class ReportOpenRequest(BaseModel):
    """Idempotent open-or-create draft payload."""

    report_date: str = Field(..., alias="reportDate", description="YYYY-MM-DD")
    report_kind: ReportKind = Field(..., alias="reportKind")

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @field_validator("report_date")
    @classmethod
    def validate_report_date(cls, v: str) -> str:
        if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()):
            raise ValueError("reportDate must be YYYY-MM-DD.")
        # Structural check only; calendar lookback is enforced in the service.
        year, month, day = map(int, v.strip().split("-"))
        try:
            datetime(year, month, day)
        except ValueError as exc:
            raise ValueError("reportDate is not a valid calendar date.") from exc
        return v.strip()


class RecipientsUpdateRequest(BaseModel):
    """
    Partial update for working To/CC while draft.

    Omitted fields are left unchanged by the service.
    Providing ``to`` or ``cc`` replaces that entire list.
    """

    to: list[str] | None = None
    cc: list[str] | None = None

    model_config = _CAMEL_CONFIG

    @model_validator(mode="after")
    def at_least_one_field(self) -> "RecipientsUpdateRequest":
        if self.to is None and self.cc is None:
            raise ValueError("At least one of 'to' or 'cc' must be provided.")
        return self

    @field_validator("to", "cc", mode="before")
    @classmethod
    def strip_emails(cls, v: Any) -> Any:
        if v is None:
            return v
        if not isinstance(v, list):
            raise ValueError("Recipients must be a list of email strings.")
        return [str(item).strip() for item in v]


class RecruiterEntryCreateRequest(BaseModel):
    """
    Add one recruiter entry. All business fields optional (incomplete draft OK).

    When a value is provided, only basic format checks apply.
    """

    job_id: str | None = Field(None, alias="jobId")
    candidate_name: str | None = Field(None, alias="candidateName")
    job_name: str | None = Field(None, alias="jobName")
    candidate_contact_number: str | None = Field(None, alias="candidateContactNumber")
    candidate_email: EmailStr | None = Field(None, alias="candidateEmail")
    poc: str | None = None
    client: str | None = None
    submission_status: SubmissionStatus | None = Field(None, alias="submissionStatus")
    remarks: str | None = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @field_validator("job_id", "candidate_name", "job_name", "poc", "client", "remarks", mode="before")
    @classmethod
    def blank_string_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        if isinstance(v, str):
            return v.strip()
        return v

    @field_validator("candidate_email", mode="before")
    @classmethod
    def validate_email(cls, v: Any) -> Any:
        return _validate_optional_email(v)

    @field_validator("candidate_contact_number", mode="before")
    @classmethod
    def validate_contact_number(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            return None
        cleaned = v.strip()
        if not _CONTACT_NUMBER_RE.match(cleaned):
            raise ValueError(
                "candidateContactNumber has an invalid format."
            )
        return cleaned


class RecruiterEntryUpdateRequest(BaseModel):
    """
    Partial update for one recruiter entry.

    Omitted fields unchanged. Explicit null clears the field (draft incomplete).
    Service code must honour ``model_fields_set`` (including explicit nulls).
    """

    job_id: Optional[str] = Field(None, alias="jobId")
    candidate_name: Optional[str] = Field(None, alias="candidateName")
    job_name: Optional[str] = Field(None, alias="jobName")
    candidate_contact_number: Optional[str] = Field(None, alias="candidateContactNumber")
    candidate_email: Optional[EmailStr] = Field(None, alias="candidateEmail")
    poc: Optional[str] = None
    client: Optional[str] = None
    submission_status: Optional[SubmissionStatus] = Field(
        None, alias="submissionStatus"
    )
    remarks: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)

    @model_validator(mode="after")
    def at_least_one_field(self) -> "RecruiterEntryUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided.")
        return self

    @field_validator("candidate_email", mode="before")
    @classmethod
    def validate_email(cls, v: Any) -> Any:
        return _validate_optional_email(v)

    @field_validator("candidate_contact_number", mode="before")
    @classmethod
    def validate_contact_number(cls, v: Any) -> Any:
        if v is None:
            return None
        if not isinstance(v, str) or not v.strip():
            return None
        cleaned = v.strip()
        if not _CONTACT_NUMBER_RE.match(cleaned):
            raise ValueError(
                "candidateContactNumber has an invalid format."
            )
        return cleaned

    @field_validator(
        "job_id",
        "candidate_name",
        "job_name",
        "poc",
        "client",
        "remarks",
        mode="before",
    )
    @classmethod
    def strip_or_none(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            cleaned = v.strip()
            return cleaned if cleaned else None
        return v


class LeadMetricsUpdateRequest(BaseModel):
    """
    Partial patch for lead numerical sections.

    Accepts camelCase section + nested metric keys from the API.
    Nested section models expose ``model_fields_set`` so the service can
    update only keys the client sent (omit = unchanged, null = clear).
    """

    recruitment_summary: Optional[LeadRecruitmentSummary] = None
    team_profile_review: Optional[LeadTeamProfileReview] = None
    lead_recruitment_delivery: Optional[LeadRecruitmentDelivery] = None

    model_config = _CAMEL_CONFIG

    @model_validator(mode="after")
    def at_least_one_section(self) -> "LeadMetricsUpdateRequest":
        if not self.model_fields_set:
            raise ValueError("At least one metrics section must be provided.")
        return self


class LeadTextItemCreateRequest(BaseModel):
    """Add one text item to a lead collection."""

    text: str

    model_config = _CAMEL_CONFIG

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("text cannot be empty.")
        return v.strip()


class LeadTextItemUpdateRequest(BaseModel):
    """Partial update for a lead text item (Phase 1: text only)."""

    text: str

    model_config = _CAMEL_CONFIG

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("text cannot be empty.")
        return v.strip()


# ══════════════════════════════════════════════════════════════════════════════
# Response models
# ══════════════════════════════════════════════════════════════════════════════

class ReportRecruiterResponse(BaseModel):
    """Read-only author projection on report responses."""

    recruiter_id: str
    name: str
    email: str

    model_config = _CAMEL_CONFIG


class ReportDeliveryResponse(BaseModel):
    attempt_count: int = 0
    last_attempt_at: datetime | None = None
    sent_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: str | None = None
    provider: str | None = None
    provider_message_id: str | None = None

    model_config = _CAMEL_CONFIG


class ReportRecipientsResponse(BaseModel):
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)

    model_config = _CAMEL_CONFIG


class ReportDeliverySummaryResponse(BaseModel):
    """Short delivery projection for list endpoints."""

    attempt_count: int = 0
    sent_at: datetime | None = None
    failed_at: datetime | None = None
    last_error: str | None = None

    model_config = _CAMEL_CONFIG


class DailyReportResponse(BaseModel):
    """
    Full daily report API response.

    ``submitted_at`` — business submission / freeze time.
    ``delivery.sent_at`` — successful email delivery time.
    """

    report_id: str
    report_date: str
    report_kind: str
    status: str
    schema_version: int = 1
    recruiter: ReportRecruiterResponse
    recipients: ReportRecipientsResponse
    recipients_snapshot: ReportRecipientsResponse | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery: ReportDeliveryResponse = Field(default_factory=ReportDeliveryResponse)
    submitted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = _CAMEL_CONFIG

    @classmethod
    def from_document(cls, doc: DailyReportDocument | dict[str, Any]) -> "DailyReportResponse":
        """Build response from a document model or raw Mongo dict."""
        if isinstance(doc, dict):
            doc = DailyReportDocument.from_mongo(doc)

        recipients = doc.recipients or ReportRecipients()
        snapshot = doc.recipients_snapshot
        delivery = doc.delivery or ReportDelivery()

        # Payload keys are snake_case in Mongo; expose camelCase for API consumers.
        payload_camel = _camelise_payload(doc.payload or {}, doc.report_kind)

        return cls(
            report_id=doc.report_id,
            report_date=doc.report_date,
            report_kind=doc.report_kind if isinstance(doc.report_kind, str) else doc.report_kind.value,
            status=doc.status if isinstance(doc.status, str) else doc.status.value,
            schema_version=doc.schema_version,
            recruiter=ReportRecruiterResponse(
                recruiter_id=doc.recruiter_id,
                name=doc.recruiter_name,
                email=doc.recruiter_email,
            ),
            recipients=ReportRecipientsResponse(to=list(recipients.to), cc=list(recipients.cc)),
            recipients_snapshot=(
                ReportRecipientsResponse(to=list(snapshot.to), cc=list(snapshot.cc))
                if snapshot is not None
                else None
            ),
            payload=payload_camel,
            delivery=ReportDeliveryResponse(
                attempt_count=delivery.attempt_count,
                last_attempt_at=delivery.last_attempt_at,
                sent_at=delivery.sent_at,
                failed_at=delivery.failed_at,
                last_error=delivery.last_error,
                provider=delivery.provider,
                provider_message_id=delivery.provider_message_id,
            ),
            submitted_at=doc.submitted_at,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )


class DailyReportSummaryResponse(BaseModel):
    """List-row projection for report history."""

    report_id: str
    report_date: str
    report_kind: str
    status: str
    submitted_at: datetime | None = None
    delivery: ReportDeliverySummaryResponse = Field(
        default_factory=ReportDeliverySummaryResponse,
    )
    created_at: datetime
    updated_at: datetime

    model_config = _CAMEL_CONFIG

    @classmethod
    def from_document(
        cls, doc: DailyReportDocument | dict[str, Any]
    ) -> "DailyReportSummaryResponse":
        if isinstance(doc, dict):
            doc = DailyReportDocument.from_mongo(doc)
        delivery = doc.delivery or ReportDelivery()
        return cls(
            report_id=doc.report_id,
            report_date=doc.report_date,
            report_kind=doc.report_kind if isinstance(doc.report_kind, str) else doc.report_kind.value,
            status=doc.status if isinstance(doc.status, str) else doc.status.value,
            submitted_at=doc.submitted_at,
            delivery=ReportDeliverySummaryResponse(
                attempt_count=delivery.attempt_count,
                sent_at=delivery.sent_at,
                failed_at=delivery.failed_at,
                last_error=delivery.last_error,
            ),
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )


class DailyReportListResponse(BaseModel):
    """Paginated report history envelope."""

    items: list[DailyReportSummaryResponse]
    page: int
    limit: int
    total: int

    model_config = _CAMEL_CONFIG


class ReportDefaultsResponse(BaseModel):
    """Default To/CC from application settings for a report kind."""

    report_kind: str
    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)

    model_config = _CAMEL_CONFIG


# ══════════════════════════════════════════════════════════════════════════════
# Payload camelCase helpers (response boundary only)
# ══════════════════════════════════════════════════════════════════════════════

def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _camelise_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {_snake_to_camel(k): _camelise_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camelise_value(item) for item in value]
    return value


def _camelise_payload(payload: dict[str, Any], report_kind: str | ReportKind) -> dict[str, Any]:
    """
    Convert stored snake_case payload keys to camelCase for API output.

    Kind is accepted for future kind-specific shaping; Phase 1 camelises recursively.
    """
    _ = report_kind  # reserved for kind-specific projections
    return _camelise_value(payload)
