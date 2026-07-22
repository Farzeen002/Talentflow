"""
app/services/report_email_renderer.py

Daily Report email rendering — isolated from ReportService business logic.

Responsibilities:
  - build_subject (Settings templates)
  - HTML escaping / list helpers
  - presentation formatting (business date / generated timestamp)
  - shared HTML document shell
  - dispatch by report_kind to recruiter / lead body templates
  - render_report_email(report) → (subject, html)

No Outlook send, Mongo, validation, or lifecycle logic.

Date/time rule: templates receive already-formatted display strings only.
Formatting lives here (Asia/Kolkata by default via REPORT_TZ).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.models.report import ReportKind

settings = get_settings()

# Compact enterprise email formats (no milliseconds).
_BUSINESS_DATE_FMT = "%d %b %Y"  # 20 Jul 2026
_GENERATED_AT_FMT = "%d %b %Y, %I:%M %p"  # 20 Jul 2026, 06:01 AM

# ZoneInfo %Z is unreliable on some platforms (e.g. Windows); pin known abbrevs.
_TZ_DISPLAY_ABBREV: dict[str, str] = {
    "Asia/Kolkata": "IST",
}


# ══════════════════════════════════════════════════════════════════════════════
# HTML helpers
# ══════════════════════════════════════════════════════════════════════════════

def esc(value: Any) -> str:
    """Escape a value for safe inclusion in HTML email bodies."""
    if value is None:
        return ""
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def ul_items(items: Any) -> str:
    """Render a list of ``{text: ...}`` dicts as an HTML unordered list."""
    items = items or []
    if not items:
        return "<p><em>None</em></p>"
    lis = "".join(f"<li>{esc(it.get('text'))}</li>" for it in items)
    return f"<ul>{lis}</ul>"


# ══════════════════════════════════════════════════════════════════════════════
# Presentation formatting (centralized — templates must not format dates)
# ══════════════════════════════════════════════════════════════════════════════

def format_business_date(report_date: Any) -> str:
    """
    Format Mongo ``report_date`` (YYYY-MM-DD) for email headers.

    Returns ``""`` when missing/invalid so callers can show an em dash.
    """
    if report_date is None:
        return ""
    if isinstance(report_date, datetime):
        return report_date.date().strftime(_BUSINESS_DATE_FMT)
    if isinstance(report_date, date):
        return report_date.strftime(_BUSINESS_DATE_FMT)
    text = str(report_date).strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).strftime(_BUSINESS_DATE_FMT)
    except ValueError:
        return ""


def format_generated_at(value: Any, *, tz_name: str | None = None) -> str:
    """
    Format a generated/submitted timestamp for email headers in REPORT_TZ.

    Naive datetimes are treated as UTC (Mongo / ``utcnow`` audit fields).
    Milliseconds are never included.
    """
    dt = _coerce_datetime(value)
    if dt is None:
        return ""

    zone_name = tz_name or settings.REPORT_TZ
    local = dt.astimezone(ZoneInfo(zone_name))
    abbrev = _TZ_DISPLAY_ABBREV.get(zone_name) or (local.tzname() or zone_name)
    return f"{local.strftime(_GENERATED_AT_FMT)} {abbrev}"


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # Support ISO with/without Z and space-separated Mongo str forms.
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt
    return None


def _pick_generated_at(report: dict[str, Any]) -> Any:
    """Prefer freeze/submit time, then audit timestamps, then render time."""
    for key in ("submitted_at", "updated_at", "created_at"):
        if report.get(key) is not None:
            return report[key]
    return datetime.now(timezone.utc)


def build_email_presentation(report: dict[str, Any]) -> dict[str, Any]:
    """
    Shallow-copy the report dict and attach display-ready presentation fields.

    Templates must read these keys and must not format raw dates themselves:
      - business_date_display
      - generated_at_display
      - prepared_by_display
      - recruiter_name_display
    """
    view = dict(report)
    recruiter_name = (report.get("recruiter_name") or "").strip()

    view["business_date_display"] = format_business_date(report.get("report_date"))
    view["generated_at_display"] = format_generated_at(_pick_generated_at(report))
    view["prepared_by_display"] = recruiter_name
    view["recruiter_name_display"] = recruiter_name
    return view


# ══════════════════════════════════════════════════════════════════════════════
# Subject + shared shell
# ══════════════════════════════════════════════════════════════════════════════

def build_subject(report: dict[str, Any]) -> str:
    """
    Build the email subject from Settings templates for the report kind.

    Placeholders: ``{report_kind}``, ``{recruiter_name}``, ``{report_date}``.
    ``report_date`` stays ISO (YYYY-MM-DD) for stable subject lines.
    """
    kind = report.get("report_kind")
    template = (
        settings.REPORT_LEAD_SUBJECT_TEMPLATE
        if kind == ReportKind.lead.value
        else settings.REPORT_RECRUITER_SUBJECT_TEMPLATE
    )
    return template.format(
        report_kind=kind,
        recruiter_name=report.get("recruiter_name") or "",
        report_date=report.get("report_date") or "",
    )


def wrap_html(body: str) -> str:
    """Wrap a kind-specific body fragment in a minimal HTML document shell."""
    return (
        "<!DOCTYPE html><html><body style='font-family:Segoe UI,Arial,sans-serif;"
        "color:#1a1a1a;line-height:1.4;margin:0;padding:0'>"
        f"{body}"
        "</body></html>"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def render_report_email(report: dict[str, Any]) -> tuple[str, str]:
    """
    Render subject + full HTML body for a frozen daily report.

    Builds presentation display fields, then dispatches body rendering by
    ``report_kind``. Subject comes from Settings.

    Returns:
        ``(subject, html_body)``
    """
    # Lazy imports avoid circular imports with template modules that use helpers.
    from app.services.report_templates.lead import render_lead_body
    from app.services.report_templates.recruiter import render_recruiter_body

    view = build_email_presentation(report)
    kind = view.get("report_kind")
    if kind == ReportKind.recruiter.value:
        body = render_recruiter_body(view, esc=esc)
    else:
        body = render_lead_body(view, esc=esc, ul_items=ul_items)

    subject = build_subject(report)
    html = wrap_html(body)
    return subject, html
