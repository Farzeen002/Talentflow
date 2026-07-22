"""
app/utils/normalizers.py

Value normalization utilities for the recruitment automation pipeline.

Responsibilities:
  - Infer Python types from raw string values (bool, int, float, str)
  - Extract experience duration in years from free-text values
  - Extract notice period in days from free-text values
  - Normalize key strings to snake_case slugs
  - Apply normalization across an entire Q&A dict

Design constraints:
  - NEVER raises exceptions for bad input — always returns something
  - No external library dependencies (stdlib only)
  - No filtering logic — pure type conversion
  - All matching is case-insensitive
  - Partial matches are acceptable; fallback is always cleaned string

Public API:
  slugify_key(key: str)          -> str
  experience_years(value: str)   -> Optional[float]
  notice_days(value: str)        -> Optional[int]
  infer_type(value: str)         -> Union[int, float, bool, str]
  normalize_qa(qa: dict)         -> dict
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Compiled regex patterns
# ══════════════════════════════════════════════════════════════════════════════

# ── Key slugification ─────────────────────────────────────────────────────────
_RE_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# ── Boolean literal sets ──────────────────────────────────────────────────────
_BOOL_TRUE_SET  = frozenset({"yes", "y", "true"})
_BOOL_FALSE_SET = frozenset({"no",  "n", "false"})

# ── N/A-equivalent values → normalised to empty string ───────────────────────
_NA_SET = frozenset({
    "n/a", "na", "not applicable", "not available",
    "none", "nil", "-", "--", "...",
})

# ── Pure numeric ──────────────────────────────────────────────────────────────
_RE_PURE_INT   = re.compile(r"^\d+$")
_RE_PURE_FLOAT = re.compile(r"^\d+\.\d+$")

# ── Experience: "3 years", "2.5 yrs", "4 yrs 6 months", "4 years 6 months"
#    Group 1: year count (required)
#    Group 2: month count (optional, only when years are also present)
_RE_EXPERIENCE_YEARS = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:years?|yrs?)"
    r"(?:\s+(?:and\s+)?(\d+(?:\.\d+)?)\s*(?:months?|mos?))?",
    re.IGNORECASE,
)

# ── Months-only experience: "18 months" (no years mentioned)
#    Full-string match to avoid collision with notice-period patterns.
_RE_MONTHS_ONLY_EXP = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(?:months?|mos?)$",
    re.IGNORECASE,
)

# ── "Immediate" / "Immediately" notice period
_RE_IMMEDIATE = re.compile(r"\bimmediate(?:ly)?\b", re.IGNORECASE)

# ── Duration sub-patterns (used by notice_days and infer_type)
_RE_DAYS   = re.compile(r"(\d+)\s*(?:days?)",          re.IGNORECASE)
_RE_WEEKS  = re.compile(r"(\d+)\s*(?:weeks?|wks?)",    re.IGNORECASE)
_RE_MONTHS = re.compile(r"(\d+)\s*(?:months?|mos?)",   re.IGNORECASE)

# ── Starts-with boolean (for "Yes, I have..." / "No, not available" style)
_RE_STARTS_TRUE  = re.compile(r"^(?:yes|true)\b",  re.IGNORECASE)
_RE_STARTS_FALSE = re.compile(r"^(?:no|false)\b",  re.IGNORECASE)


# ══════════════════════════════════════════════════════════════════════════════
# slugify_key
# ══════════════════════════════════════════════════════════════════════════════

def slugify_key(key: str) -> str:
    """
    Normalize a raw question/key string to a snake_case slug.

    Steps:
      1. Strip leading and trailing whitespace.
      2. Lowercase the entire string.
      3. Replace every run of non-alphanumeric characters with ``_``.
      4. Strip leading and trailing underscores from the result.

    Args:
        key: Raw key string (e.g. ``"Unix Experience"``).

    Returns:
        Snake-case slug, or an empty string if input is blank after
        normalisation.

    Examples::

        slugify_key("Unix Experience")       → "unix_experience"
        slugify_key("  PF Available? ")      → "pf_available"
        slugify_key("Notice Period (days)")  → "notice_period_days"
        slugify_key("C++ Experience")        → "c_experience"
        slugify_key("  ")                    → ""
    """
    return _RE_NON_ALNUM.sub("_", key.strip().lower()).strip("_")


# ══════════════════════════════════════════════════════════════════════════════
# experience_years
# ══════════════════════════════════════════════════════════════════════════════

def experience_years(value: str) -> Optional[float]:
    """
    Extract a numeric experience duration in years from a free-text string.

    Supported patterns:
      - ``"3 years"``           → 3.0
      - ``"2.5 years"``         → 2.5
      - ``"4 yrs"``             → 4.0
      - ``"4 yrs 6 months"``    → 4.5
      - ``"4 years 6 months"``  → 4.5
      - ``"4 years and 6 months"`` → 4.5
      - ``"18 months"``         → 1.5   (months-only, full-string match)
      - ``"6 months"``          → 0.5

    Months are converted to a fractional year (``months / 12``) and added
    to the whole-year count.  The result is rounded to 2 decimal places.

    Args:
        value: Raw string value from the parsed Q&A dict.

    Returns:
        Experience as a ``float`` (years), or ``None`` if no recognisable
        experience pattern is found.
    """
    if not value or not value.strip():
        return None

    cleaned = value.strip()

    # ── Pattern 1: "X years [Y months]" ──────────────────────────────────────
    m = _RE_EXPERIENCE_YEARS.search(cleaned)
    if m:
        years  = float(m.group(1))
        months = float(m.group(2)) if m.group(2) else 0.0
        result = round(years + months / 12, 2)
        logger.debug(
            "experience_years: %r → %.2f (years=%s months=%s)",
            value, result, m.group(1), m.group(2),
        )
        return result

    # ── Pattern 2: "X months" standalone (no years mentioned) ─────────────────
    m = _RE_MONTHS_ONLY_EXP.match(cleaned)
    if m:
        result = round(float(m.group(1)) / 12, 2)
        logger.debug(
            "experience_years: %r → %.2f (months-only)", value, result
        )
        return result

    return None


# ══════════════════════════════════════════════════════════════════════════════
# notice_days
# ══════════════════════════════════════════════════════════════════════════════

def notice_days(value: str) -> Optional[int]:
    """
    Extract a notice period as a number of days from a free-text string.

    Conversion rules:
      - ``"Immediate"`` / ``"Immediately"`` → 0
      - ``"15 days"``                        → 15
      - ``"1 week"`` / ``"2 weeks"``         → 7, 14
      - ``"1 month"`` / ``"2 months"``       → 30, 60  (1 month = 30 days)

    Args:
        value: Raw string value from the parsed Q&A dict.

    Returns:
        Notice period as an ``int`` (days), or ``None`` if no recognisable
        notice period pattern is found.
    """
    if not value or not value.strip():
        return None

    cleaned = value.strip()

    # ── "Immediate" / "Immediately" ───────────────────────────────────────────
    if _RE_IMMEDIATE.search(cleaned):
        logger.debug("notice_days: %r → 0 (immediate)", value)
        return 0

    # ── "X days" ─────────────────────────────────────────────────────────────
    m = _RE_DAYS.search(cleaned)
    if m:
        result = int(m.group(1))
        logger.debug("notice_days: %r → %d (days)", value, result)
        return result

    # ── "X weeks" ────────────────────────────────────────────────────────────
    m = _RE_WEEKS.search(cleaned)
    if m:
        result = int(m.group(1)) * 7
        logger.debug("notice_days: %r → %d (weeks→days)", value, result)
        return result

    # ── "X months" ───────────────────────────────────────────────────────────
    m = _RE_MONTHS.search(cleaned)
    if m:
        result = int(m.group(1)) * 30
        logger.debug("notice_days: %r → %d (months→days)", value, result)
        return result

    return None


# ══════════════════════════════════════════════════════════════════════════════
# infer_type
# ══════════════════════════════════════════════════════════════════════════════

def infer_type(value: str) -> Union[int, float, bool, str]:
    """
    Infer and convert a raw string value to the most appropriate Python type.

    Conversion priority (applied in strict order):

    1. **Empty / whitespace-only** → ``""``
    2. **N/A-equivalent** (``"n/a"``, ``"not applicable"``, ``"none"`` …)
       → ``""``
    3. **Boolean exact match** (``"yes"``, ``"no"``, ``"true"``, ``"false"``,
       ``"y"``, ``"n"``) → ``True`` / ``False``
    4. **Pure integer** (``"42"``) → ``int``
    5. **Pure float** (``"3.14"``) → ``float``
    6. **"Immediate"** / **"Immediately"** → ``0``  (notice period sentinel)
    7. **Experience pattern** (``"3 years"``, ``"4 yrs 6 months"``) → ``float``
    8. **Days** (``"15 days"``) → ``int``
    9. **Weeks** (``"2 weeks"``) → ``int`` (×7)
    10. **Months** (``"2 months"``) → ``int`` (×30)
    11. **Starts-with boolean** (``"Yes, I have …"``, ``"No, not yet …"``)
        → ``True`` / ``False``
    12. **Fallback** → stripped string (no conversion)

    Edge cases:
      - ``"N/A"``, ``"not applicable"`` → ``""``
      - ``"Yes, I have"`` → ``True``  (starts-with boolean)
      - ``"4 yrs 6 months"`` → ``4.5``  (experience beats months-only)
      - Multiple numbers in string → first match per pattern wins
      - Inconsistent casing is handled throughout

    Args:
        value: Raw string from the Q&A dict.

    Returns:
        Converted value as ``int``, ``float``, ``bool``, or ``str``.
        Never raises an exception.
    """
    # ── Guard: non-string input (defensive) ───────────────────────────────────
    if not isinstance(value, str):
        return value  # type: ignore[return-value]

    cleaned = value.strip()

    # ── 1. Empty ───────────────────────────────────────────────────────────────
    if not cleaned:
        return ""

    # ── 2. N/A-equivalent ─────────────────────────────────────────────────────
    if cleaned.lower() in _NA_SET:
        logger.debug("infer_type: %r → '' (N/A)", value)
        return ""

    # ── 3. Boolean exact match ────────────────────────────────────────────────
    lower = cleaned.lower()
    if lower in _BOOL_TRUE_SET:
        logger.debug("infer_type: %r → True (bool exact)", value)
        return True
    if lower in _BOOL_FALSE_SET:
        logger.debug("infer_type: %r → False (bool exact)", value)
        return False

    # ── 4. Pure integer ───────────────────────────────────────────────────────
    if _RE_PURE_INT.match(cleaned):
        result_int = int(cleaned)
        logger.debug("infer_type: %r → %d (int)", value, result_int)
        return result_int

    # ── 5. Pure float ─────────────────────────────────────────────────────────
    if _RE_PURE_FLOAT.match(cleaned):
        result_float = float(cleaned)
        logger.debug("infer_type: %r → %f (float)", value, result_float)
        return result_float

    # ── 6. "Immediate" notice period ──────────────────────────────────────────
    if _RE_IMMEDIATE.search(cleaned):
        logger.debug("infer_type: %r → 0 (immediate)", value)
        return 0

    # ── 7. Experience (requires "years"/"yrs" keyword) ────────────────────────
    # NOTE : We intentionally do NOT call experience_years() here because that
    # helper also matches months-only strings ("18 months" → 1.5), which
    # conflicts with step 10 ("2 months" → 60 days per notice-period spec).
    # Only patterns that contain a years/yrs keyword are matched here;
    # standalone "X months" falls through to step 10.
    m_exp = _RE_EXPERIENCE_YEARS.search(cleaned)
    if m_exp:
        _years  = float(m_exp.group(1))
        _months = float(m_exp.group(2)) if m_exp.group(2) else 0.0
        exp_result = round(_years + _months / 12, 2)
        logger.debug("infer_type: %r → %s (experience_years)", value, exp_result)
        return int(exp_result) if exp_result == int(exp_result) else exp_result

    # ── 8. Days ───────────────────────────────────────────────────────────────
    m = _RE_DAYS.search(cleaned)
    if m:
        result_days = int(m.group(1))
        logger.debug("infer_type: %r → %d (days)", value, result_days)
        return result_days

    # ── 9. Weeks → days ───────────────────────────────────────────────────────
    m = _RE_WEEKS.search(cleaned)
    if m:
        result_weeks = int(m.group(1)) * 7
        logger.debug("infer_type: %r → %d (weeks→days)", value, result_weeks)
        return result_weeks

    # ── 10. Months → days ─────────────────────────────────────────────────────
    m = _RE_MONTHS.search(cleaned)
    if m:
        result_months = int(m.group(1)) * 30
        logger.debug("infer_type: %r → %d (months→days)", value, result_months)
        return result_months

    # ── 11. Starts-with boolean (e.g. "Yes, I have experience") ───────────────
    if _RE_STARTS_TRUE.match(cleaned):
        logger.debug("infer_type: %r → True (starts-with bool)", value)
        return True
    if _RE_STARTS_FALSE.match(cleaned):
        logger.debug("infer_type: %r → False (starts-with bool)", value)
        return False

    # ── 12. Fallback: return cleaned string ───────────────────────────────────
    logger.debug("infer_type: %r → %r (string fallback)", value, cleaned)
    return cleaned


# ══════════════════════════════════════════════════════════════════════════════
# normalize_qa
# ══════════════════════════════════════════════════════════════════════════════

def normalize_qa(qa: dict[str, str]) -> dict[str, Any]:
    """
    Apply :func:`infer_type` to every value in a Q&A dict.

    Iterates over all key-value pairs and converts each string value to the
    most appropriate Python type.  Keys are preserved unchanged.

    Behaviour guarantees:
      - Non-string values are passed through unchanged (defensive guard
        inside :func:`infer_type`).
      - Any per-key conversion error is caught, the original raw string is
        kept, and a warning is logged.  The loop always continues.
      - The input dict is never mutated — a new dict is returned.

    Args:
        qa: Raw Q&A dict as produced by
            :func:`~app.services.parsing_service.parse_email_body`.
            Keys are snake_case slugs; values are raw strings.

    Returns:
        New dict with the same keys and type-converted values.

    Examples::

        normalize_qa({
            "unix_experience": "3 years",
            "notice_period":   "Immediate",
            "pf_available":    "Yes",
            "ctc":             "12",
        })
        # → {
        #     "unix_experience": 3,
        #     "notice_period":   0,
        #     "pf_available":    True,
        #     "ctc":             12,
        #   }
    """
    if not qa:
        return {}

    normalized: dict[str, Any] = {}

    for key, raw_value in qa.items():
        try:
            converted = infer_type(raw_value)
            normalized[key] = converted
        except Exception as exc:  # noqa: BLE001
            # Should never reach here given infer_type's own safety net,
            # but belt-and-braces: keep raw value on any unexpected failure.
            logger.warning(
                "normalize_qa: conversion failed for key=%r value=%r — "
                "keeping raw string. %s: %s",
                key, raw_value, type(exc).__name__, exc,
            )
            normalized[key] = raw_value

    logger.debug(
        "normalize_qa: normalized %d field(s)", len(normalized)
    )
    return normalized
