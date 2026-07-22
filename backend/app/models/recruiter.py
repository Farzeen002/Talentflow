"""
app/models/recruiter.py

Pydantic domain models for the Recruiter entity.

Separation of concerns:
  RecruiterDocument  → Full MongoDB document (used internally, never sent to clients)
  RecruiterResponse  → Public-safe projection (no tokens, no internal fields)
  OAuthStatus        → Enum for the recruiter's authorization lifecycle state
"""

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class OAuthStatus(str, Enum):
    """Lifecycle state of a recruiter's OAuth authorization."""

    active = "active"
    revoked = "revoked"
    pending = "pending"


class OutlookSyncState(BaseModel):
    """
    Per-recruiter Microsoft Graph delta sync checkpoint for the Nvite folder.

    Only ``delta_link`` and ``folder_id`` are persisted — no other sync metadata.
    """

    delta_link: str | None = Field(
        default=None,
        description=(
            "Full @odata.deltaLink URL from the last completed delta round. "
            "None means initial bootstrap is required."
        ),
    )
    folder_id: str | None = Field(
        default=None,
        description=(
            "Cached Graph mailFolder ID for the Nvite folder. "
            "Resolved once and reused until the folder is missing."
        ),
    )


class ProviderType(str, Enum):
    """
    Email provider for a recruiter's connected mailbox.

    Values
    ------
    gmail:   Google Gmail account (Google OAuth 2.0).
    outlook: Microsoft Outlook / Microsoft 365 account (Microsoft OAuth 2.0).

    The default on ``RecruiterDocument`` is ``gmail`` so that all existing
    documents — created before the provider field was introduced — deserialise
    correctly without a database migration being strictly required (though the
    Phase 0 migration script should still be run for consistency).
    """

    gmail   = "gmail"
    outlook = "outlook"


class RecruiterDocument(BaseModel):
    """
    Full MongoDB document model for a recruiter.

    Includes encrypted OAuth tokens and internal fields.
    Must never be serialised directly into an API response.
    """

    recruiter_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="UUID4 string — primary key for the recruiter.",
    )
    email: str = Field(..., description="Recruiter's verified Google account email.")
    name: str = Field(..., description="Display name from Google userinfo.")
    oauth_tokens_encrypted: str = Field(
        ...,
        description=(
            "Fernet-encrypted JSON blob containing 'access_token' "
            "and optionally 'refresh_token'."
        ),
    )
    oauth_status: OAuthStatus = Field(
        default=OAuthStatus.active,
        description="Current authorization lifecycle state.",
    )
    provider: ProviderType = Field(
        default=ProviderType.gmail,
        description=(
            "Email provider: 'gmail' (Google) or 'outlook' (Microsoft). "
            "Determines which OAuth flow and API client is used during "
            "email ingestion. Defaults to 'gmail' for backward compatibility."
        ),
    )
    outlook_sync: OutlookSyncState | None = Field(
        default=None,
        description=(
            "Microsoft Graph delta sync state for Outlook recruiters only. "
            "Absent or null on Gmail recruiters and legacy Outlook documents."
        ),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp of document creation.",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="UTC timestamp of last document update.",
    )

    model_config = {"use_enum_values": True}

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> "RecruiterDocument":
        """
        Construct a RecruiterDocument from a raw Motor result dict.
        Strips the MongoDB ``_id`` field automatically.
        """
        doc.pop("_id", None)
        return cls(**doc)


class RecruiterResponse(BaseModel):
    """
    Public-safe recruiter profile.

    Returned by /auth/me. Contains no secrets or internal storage fields.
    """

    recruiter_id: str
    email: str
    name: str
    oauth_status: OAuthStatus
    provider: ProviderType
    created_at: datetime

    model_config = {"use_enum_values": True}
