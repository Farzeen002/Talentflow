"""
app/models/resume_access.py

Pydantic response model for the resume signed-URL endpoint.

GET /api/v1/candidates/{candidate_id}/resume?action=preview|download

Kept in its own file because this is a transient, on-demand resource —
not part of the persistent candidate document schema.

Fields
------
candidateId:      UUID of the candidate whose resume was requested.
url:              Short-lived GCS V4 signed URL (expires in ``expiresInSeconds``).
expiresInSeconds: URL lifetime in seconds (always 900 = 15 minutes).
fileType:         MIME type of the resume file (e.g. ``"application/pdf"``).
filename:         Original filename (e.g. ``"John_Doe_Resume.pdf"``).
action:           Effective action — ``"preview"`` or ``"download"``.
                  May differ from the requested ``?action`` param when a
                  DOCX/DOC preview is automatically downgraded to download.
note:             Optional human-readable message set when action was
                  downgraded (e.g. DOCX preview → download fallback).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ResumeUrlResponse(BaseModel):
    """
    Response body for ``GET /api/v1/candidates/{candidate_id}/resume``.

    The ``url`` field is a GCS V4 signed URL valid for 15 minutes.
    Frontend should treat it as single-use and ephemeral — never cache it.
    Request a fresh URL on every Preview / Download button click.
    """

    candidateId:      str
    url:              str
    expiresInSeconds: int           = 900
    fileType:         Optional[str] = None
    filename:         Optional[str] = None
    action:           str                     # "preview" | "download" (effective)
    note:             Optional[str] = None    # set when DOCX downgraded from preview
