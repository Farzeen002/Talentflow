"""
app/services/parsing_service.py

Parser for Naukri NVite candidate notification emails.

Extracts from each email:
  metadata:
    name      — candidate full name (ALL-CAPS line before Q&A anchor)
    email     — null (Naukri hides contact details in this email type)
    job_title — from subject line or body "Job Title" section
  qa:
    is_c2h_ok, current_ctc, expected_ctc, robotics_experience_years,
    is_ok_abb_lbc, notice_period_days  (raw string values)

Raw HTML and cleaned text are also stored for debugging.
"""
from __future__ import annotations

import html as _html_module
import re
from typing import Any


# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

# Divides metadata section from Q&A section.
_QA_ANCHOR_RE = re.compile(
    r"Check\s+out\s+candidate.s\s+response\s+to\s+your\s+questions",
    re.IGNORECASE,
)

# NVite email subject prefix to strip before extracting job title.
_SUBJECT_PREFIX_RE = re.compile(
    r"^NVite\s*-\s*Naukri\.com\s*-?\s*",
    re.IGNORECASE,
)

# Lines to skip when scanning for candidate name.
_NAME_NOISE_RE = re.compile(
    r"\b(?:Naukri|NVite|You\s+have\s+a\s+new\s+response)\b",
    re.IGNORECASE,
)

# NVite emails always render the candidate's current role as:
#   "<Role> at <Company>"  (e.g. "Application Support Engineer at CitiusTech")
# The line immediately before this is always the candidate name.
_ROLE_AT_COMPANY_RE = re.compile(r".+\s+at\s+\w", re.IGNORECASE)


# "Posted X days ago" header line — the line immediately after it is the name.
_POSTED_RE = re.compile(r"^Posted\b.*\bago$", re.IGNORECASE)


# "Keyskills" label line — value may be on the same line OR the next line.
_KEYSKILLS_LABEL_RE = re.compile(r"^Keyskills$", re.IGNORECASE)
_KEYSKILLS_LINE_RE  = re.compile(r"^Keyskills\s+(.+)$", re.IGNORECASE)


# Known section labels that end a multi-line keyskills block.
_SECTION_STOP_RE = re.compile(
    r"^(?:Location|Past\s+Experience|Notice\s+Period|Education|"
    r"View\s+response|View\s+Contact|This\s+doesn|Posted\b|"
    r"NVite\b|You\s+have|Job\s+Title)\b",
    re.IGNORECASE,
)

# Permitted characters in a candidate name:
#   letters, spaces, dots (K.UDAY), hyphens (Jean-Pierre), apostrophes (D'Souza)
_NAME_CHARS_RE = re.compile(r"^[A-Za-z\s.\-']+$")

# ── Profile summary block patterns ────────────────────────────────────────────
# Matches "X Years & Y Months" experience lines.
_EXP_LINE_RE = re.compile(
    r"^\d+\s+Years?\s*(?:&|and)\s*\d+\s+Months?$", re.IGNORECASE
)
# Matches "N .NN Lacs" CTC lines.
_CTC_LINE_RE = re.compile(r"^[\d\s.]+\s*Lacs?$", re.IGNORECASE)
# Matches literal "Not Mentioned" role lines.
_NOT_MENTIONED_RE = re.compile(r"^not\s+mentioned$", re.IGNORECASE)

# ── Labeled-field patterns ─────────────────────────────────────────────────────
_LOCATION_LABEL_RE = re.compile(r"^Location\s+(.+)$", re.IGNORECASE)
_NOTICE_LABEL_RE   = re.compile(r"^Notice\s+Period\s+(.+)$", re.IGNORECASE)

# ── Notice period normalisation ────────────────────────────────────────────────
_NA_NOTICE_RE        = re.compile(r"\b(?:not\s+mentioned|na|n/a|nil)\b", re.IGNORECASE)
_IMMEDIATE_NOTICE_RE = re.compile(r"\b(?:immediate|serving)\b", re.IGNORECASE)
_NOTICE_YEARS_RE     = re.compile(r"(\d+)\s*years?", re.IGNORECASE)
_NOTICE_MONTHS_RE    = re.compile(r"(\d+)\s*months?", re.IGNORECASE)
_NOTICE_DAYS_RE      = re.compile(r"(\d+)\s*days?", re.IGNORECASE)

# Job ID code embedded in the body job title, e.g. "...L2-(DBA002)".
_JOB_ID_RE = re.compile(r"\(([A-Z0-9]{3,})\)\s*$", re.IGNORECASE)

# Pattern-based Q&A matching — handles variable question text across job posts.
# Tuple of (compiled_regex, canonical_key).
# The special key "__exp__" triggers dynamic key generation from the technology
# mentioned after "in" in the question, e.g. "in RUN Management" →
# "experience_run_management_years".
_QA_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"are\s+you\s+okay\s+to\s+work\b",           re.IGNORECASE), "is_ok_client"),
    (re.compile(r"\bc2h\b|contract\s+to\s+hire",             re.IGNORECASE), "is_c2h_ok"),
    (re.compile(r"what\s+is\s+your\s+notice\s+period",       re.IGNORECASE), "notice_period_days"),
    (re.compile(r"\bpf\s+account\b",                          re.IGNORECASE), "has_pf_account"),
    (re.compile(r"current\s+ctc",                            re.IGNORECASE), "current_ctc"),
    (re.compile(r"expected\s+ctc",                           re.IGNORECASE), "expected_ctc"),
    (re.compile(r"years\s+of\s+exp(?:erience|rience).*?\bin\s+",
                                                              re.IGNORECASE), "__exp__"),
    (re.compile(r"residing.*?willing\s+to\s+relocate",       re.IGNORECASE), "willing_to_relocate"),
]

# HTML cleanup patterns.
_BLOCK_TAG_RE  = re.compile(r"<(?:br|p|div|tr|td|th|li|h[1-6])(?:\s[^>]*)?>", re.IGNORECASE)
_ANY_TAG_RE    = re.compile(r"<[^>]+>")
_MULTI_SPACE_RE = re.compile(r" {2,}")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — HTML → plain text
# ══════════════════════════════════════════════════════════════════════════════

def _clean_html(body: str) -> str:
    """Strip HTML tags, decode entities, normalise whitespace."""
    if not body or not body.strip():
        return ""
    text = _BLOCK_TAG_RE.sub("\n", body)
    text = _ANY_TAG_RE.sub("", text)
    text = _html_module.unescape(text)
    text = text.replace("\xa0", " ")
    lines = [_MULTI_SPACE_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    text  = "\n".join(lines)
    text  = _MULTI_BLANK_RE.sub("\n\n", text).strip()
    return text


def _split_lines(text: str) -> list[str]:
    """Return non-empty lines from cleaned text."""
    return [ln for ln in text.splitlines() if ln.strip()]


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Section split at Q&A anchor
# ══════════════════════════════════════════════════════════════════════════════

def _split_sections(clean_text: str) -> tuple[str, str]:
    """
    Split at the Q&A anchor line.

    Returns:
        (metadata_text, qa_text) — qa_text is empty string if anchor absent.
    """
    m = _QA_ANCHOR_RE.search(clean_text)
    if m:
        return clean_text[: m.start()], clean_text[m.start() :]
    return clean_text, ""


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Field extractors
# ══════════════════════════════════════════════════════════════════════════════

def _extract_job_title(metadata_text: str, subject: str) -> str | None:
    """
    Extract job title.

    Strategy
    --------
    1. Subject line (most reliable — already a clean comma-delimited summary).
       Format: "NVite - Naukri.com -<JobTitle>, <exp>, <location>, <ctc>"
       After stripping the prefix the first comma segment is the job title.

    2. Body fallback — line immediately after the "Job Title" label line.
       NVite body format::

           Job Title
           *Design Engineer Robotics* Applicants

       The value line has markdown ``*`` wrapping and a trailing " Applicants"
       count — both are stripped.
    """
    # 1. Subject
    if subject and subject.strip():
        remainder = _SUBJECT_PREFIX_RE.sub("", subject.strip())
        if remainder != subject.strip():
            job_title = remainder.split(",")[0].strip()
            if job_title:
                return job_title
        # Fallback: segment split for subjects without "Naukri.com"
        for seg in subject.split(" - ")[1:]:
            candidate = seg.split(",")[0].strip()
            if (
                candidate
                and len(candidate) >= 3
                and not re.search(r"\bnaukri|nvite\b", candidate, re.IGNORECASE)
                and re.match(r"^[A-Za-z0-9\s\-/&().]+$", candidate)
            ):
                return candidate

    # 2. Body
    lines = _split_lines(metadata_text)
    for i, line in enumerate(lines):
        if line.strip().lower() == "job title" and i + 1 < len(lines):
            raw = lines[i + 1].replace("*", "")
            raw = re.sub(r"\s+Applicants.*$", "", raw, flags=re.IGNORECASE).strip()
            if raw:
                return raw

    return None


def _extract_name(metadata_text: str) -> str | None:
    """
    Extract candidate name.

    NVite emails always follow this fixed structure::

        Posted X days ago          ← anchor
        <Candidate Name>           ← next line (any casing)
        <Role> at <Company>        ← may be absent ("Not Mentioned")

    Strategy
    --------
    1. "Posted … ago" anchor — line immediately after it is the name.
       Most direct and reliable; works regardless of whether the candidate
       has a current role listed.
    2. "Role at Company" anchor — line immediately before it is the name.
       Fallback when the Posted line is absent.
    3. ALL-CAPS line — last resort for unusual email formats.
    """
    lines = _split_lines(metadata_text)

    # Strategy 1: line after "Posted X days ago"
    for i, line in enumerate(lines):
        if not _POSTED_RE.match(line):
            continue
        if i + 1 >= len(lines):
            break
        candidate = lines[i + 1].strip()
        if (
            3 <= len(candidate) <= 60
            and _NAME_CHARS_RE.match(candidate)
            and not _NAME_NOISE_RE.search(candidate)
        ):
            return candidate

    # Strategy 2: line before "Role at Company"
    for i, line in enumerate(lines):
        if i == 0:
            continue
        if not _ROLE_AT_COMPANY_RE.match(line):
            continue
        candidate = lines[i - 1].strip()
        if (
            3 <= len(candidate) <= 60
            and _NAME_CHARS_RE.match(candidate)
            and not _NAME_NOISE_RE.search(candidate)
        ):
            return candidate

    # Strategy 3: ALL-CAPS fallback
    for line in lines:
        if not (3 <= len(line) <= 60):
            continue
        if not _NAME_CHARS_RE.match(line):
            continue
        if not line.isupper():
            continue
        if _NAME_NOISE_RE.search(line):
            continue
        return line

    return None


def _normalize_notice(value: str) -> int | None:
    """
    Convert a notice period string to integer days.

    Examples::

        "15 Days or less"       → 15
        "1 Month"               → 30
        "3 Months"              → 90
        "Serving Notice Period" → 0
        "Immediate"             → 0
        "Not Mentioned"         → None
    """
    v = value.strip()
    if not v or _NA_NOTICE_RE.search(v):
        return None
    if _IMMEDIATE_NOTICE_RE.search(v):
        return 0
    m = _NOTICE_YEARS_RE.search(v)
    if m:
        return int(m.group(1)) * 365
    m = _NOTICE_MONTHS_RE.search(v)
    if m:
        return int(m.group(1)) * 30
    m = _NOTICE_DAYS_RE.search(v)
    if m:
        return int(m.group(1))
    return None


def _extract_profile_fields(metadata_text: str) -> dict[str, Any]:
    """
    Extract structured fields from the candidate summary block.

    NVite emails always follow this fixed layout between "Posted X ago" and
    "View Contact Details"::

        Posted X days ago
        <Name>                          ← already extracted separately
        <Role> at <Company>  OR  Not Mentioned
        <X Years & Y Months>
        <N .NN Lacs>
        View Contact Details

    Returns dict with keys:
        current_role, current_company, experience_years, profile_ctc_rupees
    All values may be None.
    """
    lines = _split_lines(metadata_text)

    # Locate block start — line immediately after "Posted X ago"
    block_start = None
    for i, line in enumerate(lines):
        if _POSTED_RE.match(line):
            block_start = i + 1
            break
    if block_start is None:
        return {}

    # Locate block end — "View Contact Details" sentinel
    block_end = len(lines)
    for j in range(block_start, len(lines)):
        if re.match(r"^View\s+Contact\s+Details$", lines[j], re.IGNORECASE):
            block_end = j
            break

    block = lines[block_start:block_end]
    # block[0] = name  (skip — already handled by _extract_name)
    # block[1] = role line
    # remaining: experience, CTC (in any order, matched by pattern)

    current_role       = None
    current_company    = None
    experience_years   = None
    profile_ctc_rupees = None

    for k, bline in enumerate(block):
        if k == 1:                              # role / company line
            if not _NOT_MENTIONED_RE.match(bline):
                parts = re.split(r"\s+at\s+", bline, maxsplit=1, flags=re.IGNORECASE)
                if len(parts) == 2:
                    current_role    = parts[0].strip() or None
                    current_company = parts[1].strip() or None
        elif _EXP_LINE_RE.match(bline):        # experience line
            em = re.match(
                r"(\d+)\s+Years?\s*(?:&|and)\s*(\d+)\s+Months?",
                bline, re.IGNORECASE,
            )
            if em:
                experience_years = round(int(em.group(1)) + int(em.group(2)) / 12, 1)
        elif _CTC_LINE_RE.match(bline):        # CTC line
            ctc_m = re.match(r"^([\d\s.]+)\s*Lacs?", bline, re.IGNORECASE)
            if ctc_m:
                try:
                    num_str = re.sub(r"\s+", "", ctc_m.group(1))
                    profile_ctc_rupees = int(float(num_str) * 100_000)
                except ValueError:
                    pass

    return {
        "current_role":       current_role,
        "current_company":    current_company,
        "experience_years":   experience_years,
        "profile_ctc_rupees": profile_ctc_rupees,
    }


def _extract_current_location(metadata_text: str) -> str | None:
    """
    Extract candidate's primary location from the "Location" label line.

    NVite format::

        Location Hyderabad (preferred location is ...)

    Returns only the current city — text before the first "(".
    """
    for line in _split_lines(metadata_text):
        m = _LOCATION_LABEL_RE.match(line)
        if m:
            loc = m.group(1).split("(")[0].strip()
            return loc or None
    return None


def _extract_notice_period_days(metadata_text: str) -> int | None:
    """
    Extract notice period from the "Notice Period" label and normalise to days.
    """
    for line in _split_lines(metadata_text):
        m = _NOTICE_LABEL_RE.match(line)
        if m:
            return _normalize_notice(m.group(1))
    return None


def _extract_keyskills(metadata_text: str) -> list[str]:
    """
    Extract candidate key skills from the Keyskills label.

    Handles two NVite rendering variants:
      - Inline: "Keyskills Java,Python,SQL"
      - Split:  "Keyskills\\nJava,Python,SQL"

    Long skill lists may wrap across multiple plain-text lines; all
    continuation lines are joined before splitting on ``,``.

    Known section labels act as stop markers for multi-line collection.
    """
    lines       = _split_lines(metadata_text)
    skill_parts = []
    collecting  = False

    for i, line in enumerate(lines):
        if not collecting:
            # Variant A: label + value on same line
            m = _KEYSKILLS_LINE_RE.match(line)
            if m:
                skill_parts.append(m.group(1))
                collecting = True
                continue

            # Variant B: label-only line — next line begins the value
            if _KEYSKILLS_LABEL_RE.match(line):
                collecting = True
                continue
        else:
            if _SECTION_STOP_RE.match(line):
                break
            skill_parts.append(line)

    if not skill_parts:
        return []

    raw_text = " ".join(skill_parts)
    return [s.strip() for s in raw_text.split(",") if s.strip()]

def _match_qa_pattern(question: str) -> str | None:
    """
    Return the canonical QA key for a question line, or ``None`` if unrecognised.

    For experience questions the key is generated dynamically from the
    technology topic, e.g. "RUN Management" → ``experience_run_management_years``.
    """
    for pat, key in _QA_PATTERNS:
        if not pat.search(question):
            continue
        if key == "__exp__":
            m = re.search(r"\bin\s+(.+?)(?:\?|$)", question, re.IGNORECASE)
            topic = m.group(1).strip() if m else "unknown"
            slug  = re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")
            return f"experience_{slug}_years"
        return key
    return None


def _extract_job_id(metadata_text: str) -> str | None:
    """
    Extract the job posting ID from the body job-title line.

    NVite body format::

        Job Title
        *Application Support Engineer - L2-(DBA002)* Applicants

    The code inside the last set of parentheses is the unique job ID used to
    group all candidates who applied to the same posting.
    """
    lines = _split_lines(metadata_text)
    for i, line in enumerate(lines):
        if line.strip().lower() == "job title" and i + 1 < len(lines):
            raw = lines[i + 1].replace("*", "")
            raw = re.sub(r"\s+Applicants.*$", "", raw, flags=re.IGNORECASE).strip()
            m = _JOB_ID_RE.search(raw)
            if m:
                return m.group(1).upper()
    return None


def _extract_qa(qa_text: str) -> dict[str, str]:
    """
    Extract Q&A pairs using pattern-based question matching.

    Handles variable question wording across job templates (e.g. client name
    changes in "Are you okay to work <Client>?").  Only questions that match
    a pattern in ``_QA_PATTERNS`` are recorded.  Values are raw strings —
    normalisation happens downstream in ``normalize_qa``.
    """
    if not qa_text:
        return {}

    qa: dict[str, str] = {}
    lines = _split_lines(qa_text)
    i = 0
    while i < len(lines):
        key = _match_qa_pattern(lines[i])
        if key is not None and i + 1 < len(lines):
            qa[key] = lines[i + 1]
            i += 2
        else:
            i += 1
    return qa


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def parse_email_body(body: str, subject: str = "") -> dict[str, Any]:
    """
    Parse a Naukri NVite email and return structured candidate data.

    Args:
        body:    Raw email body (plain text as returned by Gmail API).
        subject: Email subject line.

    Returns:
        Dict with keys: ``raw``, ``clean``, ``metadata``, ``qa``,
        ``sections``, ``processing``.
    """
    clean_text = _clean_html(body)
    lines      = _split_lines(clean_text)

    metadata_text, qa_text = _split_sections(clean_text)

    name      = _extract_name(metadata_text)
    job_title = _extract_job_title(metadata_text, subject)
    job_id    = _extract_job_id(metadata_text)
    profile   = _extract_profile_fields(metadata_text)
    location  = _extract_current_location(metadata_text)
    notice    = _extract_notice_period_days(metadata_text)
    skills    = _extract_keyskills(metadata_text)
    qa        = _extract_qa(qa_text)

    text_lower = clean_text.lower()

    return {
        "job_id": job_id,
        "raw": {
            "subject":   subject,
            "body_html": body,
        },
        "clean": {
            "text":  clean_text,
            "lines": lines,
        },
        "metadata": {
            "name":               name,
            "email":              None,
            "job_title":          job_title,
            "current_role":       profile.get("current_role"),
            "current_company":    profile.get("current_company"),
            "experience_years":   profile.get("experience_years"),
            "profile_ctc_rupees": profile.get("profile_ctc_rupees"),
            "current_location":    location,
            "profile_notice_days": notice,
        },
        "skills": {
            "raw": skills,
        },
        "qa": qa,
        "sections": {
            "has_keyskills":  any(f in text_lower for f in ("keyskills", "key skills")),
            "has_qa_section": bool(qa_text),
        },
        "processing": {
            "parsed":       bool(qa_text),
            "parse_errors": [],
            "needs_review": name is None,
        },
    }