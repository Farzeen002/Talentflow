"""
Recruiter Daily Report email body — Enterprise Document edition (v5).

Changelog from v4 (information-architecture review):
    - "Summary," "Submission Status," and "Candidates Requiring
      Follow-up" were three separate headed sections describing the
      same underlying activity -- reading as three database summaries
      rather than one report. Merged into a single "Summary" section
      with three sub-groups (Activity / Outcomes / Needs Follow-up),
      using the same quiet sub-label pattern already used elsewhere on
      this platform rather than inventing a new visual treatment.
      Needs Follow-up is a drill-down of the Outcomes counts directly
      above it (the same candidates are already counted in the
      Rejected/On Hold rows), so it now sits immediately under
      Outcomes instead of as its own headed section further down.
    - Dropped "Unique POCs" from the top-line metrics. It rarely tells
      a Lead anything "Unique Clients" doesn't already tell them, and
      the per-candidate POC is still fully visible in the detail table
      below -- no information is lost, just removed from the rollup.
    - No visual style, typography, color, or Outlook handling changed
      in this pass -- structure and section ordering only.

Design philosophy is unchanged: the candidate table is the primary
artifact. Everything above it stays quiet -- no badges, no colored
pills, no highlighted boxes. Status is distinguished by weight, not
color.

Public contract is unchanged:
    render_recruiter_body(report: dict, *, esc) -> str

No backend, payload shape, API, schema, or rendering architecture is
touched -- presentation layer only.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Design tokens -- same restrained palette as the Lead report
# ---------------------------------------------------------------------------

FONT_STACK = "Segoe UI, Arial, Helvetica, sans-serif"

INK = "#1f2328"
SUBTLE = "#57606a"
FAINT = "#8b949e"
BORDER = "#d0d7de"
BORDER_LIGHT = "#eaeef2"
ROW_ALT = "#f6f8fa"
ACCENT = "#0969da"

ROW_PAD = "9px"  # single vertical padding value for header + body table rows

# Fixed reading order for the status breakdown -- keeps the section's
# layout stable day to day instead of reshuffling by frequency.
_CANONICAL_STATUS_ORDER = [
    "submitted",
    "interview scheduled",
    "client review",
    "offer released",
    "joined",
    "on hold",
    "rejected",
]


def _t(esc: Callable[[Any], str], value: Any, dash: str = "\u2014") -> str:
    if value is None or value == "":
        return dash
    return esc(value)


def _needs_followup(status: Any) -> bool:
    s = str(status or "").strip().lower()
    return "reject" in s or "hold" in s


def _status_sort_key(status: str) -> tuple[int, str]:
    s = status.strip().lower()
    try:
        return (_CANONICAL_STATUS_ORDER.index(s), "")
    except ValueError:
        return (len(_CANONICAL_STATUS_ORDER), s)  # unrecognized -> end, alphabetical


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _meta_row(items: list[tuple[str, str]]) -> str:
    cells = []
    for i, (label, value) in enumerate(items):
        border = "" if i == 0 else f"border-left:1px solid {BORDER_LIGHT}; padding-left:16px;"
        pad_right = "padding-right:16px;" if i < len(items) - 1 else ""
        cells.append(f"""
    <td style="{pad_right} {border} font-family:{FONT_STACK}; font-size:12.5px; color:{SUBTLE}; white-space:nowrap;">
      <span style="color:{FAINT};">{label}</span>&nbsp; {value}
    </td>""")
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0">
  <tr>{''.join(cells)}</tr>
</table>
"""


def _header(business_date: Any, recruiter_name: Any, generated_at: Any, esc) -> str:
    date_str = _t(esc, business_date)
    recruiter_str = _t(esc, recruiter_name, dash="Unassigned")

    meta_items = [("Business Date", date_str)]
    if generated_at:
        meta_items.append(("Generated", _t(esc, generated_at)))

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td style="padding:28px 32px 20px 32px; border-bottom:1px solid {BORDER};">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="font-family:{FONT_STACK}; font-size:11px; font-weight:600; letter-spacing:0.6px; color:{ACCENT}; text-transform:uppercase; padding-bottom:6px;">
            Recruiter Daily Report
          </td>
        </tr>
        <tr>
          <td style="font-family:{FONT_STACK}; font-size:20px; font-weight:600; color:{INK}; line-height:1.3;">
            {recruiter_str}
          </td>
        </tr>
        <tr>
          <td style="padding-top:12px;">
            {_meta_row(meta_items)}
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
"""


def _section_heading(title: str) -> str:
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td style="padding:28px 0 8px 0; font-family:{FONT_STACK}; font-size:13px; font-weight:600; color:{INK}; border-bottom:1px solid {BORDER_LIGHT};">
      {title}
    </td>
  </tr>
</table>
"""


# ---------------------------------------------------------------------------
# Summary + status tables (label / value, no cards, no color)
# ---------------------------------------------------------------------------

def _group_label(label: str) -> str:
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td style="padding:14px 0 4px 0; font-family:{FONT_STACK}; font-size:11px; font-weight:600; letter-spacing:0.4px; text-transform:uppercase; color:{FAINT};">
      {label}
    </td>
  </tr>
</table>
"""


def _kv_table(rows: list[tuple[str, str]]) -> str:
    trs = []
    for label, value in rows:
        trs.append(f"""
  <tr>
    <td style="padding:8px 0; font-family:{FONT_STACK}; font-size:13px; color:{SUBTLE}; border-bottom:1px solid {BORDER_LIGHT};">{label}</td>
    <td align="right" style="padding:8px 0; font-family:{FONT_STACK}; font-size:13px; font-weight:600; color:{INK}; border-bottom:1px solid {BORDER_LIGHT};">{value}</td>
  </tr>""")
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(trs)}</table>'


def _status_table(status_counts: list[tuple[str, int]], esc) -> str:
    if not status_counts:
        return f'<p style="font-family:{FONT_STACK}; font-size:13px; color:{FAINT}; margin:8px 0 0 0;">No submissions logged today.</p>'
    ordered = sorted(status_counts, key=lambda kv: _status_sort_key(kv[0]))
    rows = [(esc(status) if status else "Unspecified", str(count)) for status, count in ordered]
    return _kv_table(rows)


# ---------------------------------------------------------------------------
# Follow-up list -- one row per candidate (Candidate / Status / Note)
# ---------------------------------------------------------------------------

def _followup_table(flagged_entries: list[dict[str, Any]], esc) -> str:
    rows = []
    for e in flagged_entries:
        name = _t(esc, e.get("candidate_name"))
        status = _t(esc, e.get("submission_status"))
        remark = _t(esc, e.get("remarks"), dash="\u2014")
        rows.append(f"""
  <tr>
    <td style="padding:{ROW_PAD} 12px {ROW_PAD} 0; font-family:{FONT_STACK}; font-size:13px; color:{INK}; border-bottom:1px solid {BORDER_LIGHT}; white-space:nowrap;">
      <strong>{name}</strong>
    </td>
    <td style="padding:{ROW_PAD} 12px; font-family:{FONT_STACK}; font-size:13px; color:{SUBTLE}; border-bottom:1px solid {BORDER_LIGHT}; white-space:nowrap;">
      {status}
    </td>
    <td style="padding:{ROW_PAD} 0; font-family:{FONT_STACK}; font-size:13px; color:{FAINT}; border-bottom:1px solid {BORDER_LIGHT};">
      {remark}
    </td>
  </tr>""")
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{"".join(rows)}</table>'


# ---------------------------------------------------------------------------
# Candidate table -- the primary artifact
# ---------------------------------------------------------------------------

_TABLE_COLUMNS = [
    ("job_id", "Job ID"),
    ("job_name", "Job Name"),
    ("candidate_name", "Candidate"),
    ("candidate_contact_number", "Contact"),
    ("candidate_email", "Email"),
    ("poc", "POC"),
    ("client", "Client"),
    ("submission_status", "Status"),
    ("remarks", "Remarks"),
]


def _candidate_table(entries: list[dict[str, Any]], esc) -> str:
    if not entries:
        return f'<p style="font-family:{FONT_STACK}; font-size:13px; color:{FAINT}; margin:8px 0 0 0;">No candidate submissions logged today.</p>'

    head_cells = "".join(
        f'<th style="text-align:left; padding:{ROW_PAD} 12px; font-family:{FONT_STACK}; font-size:11px; '
        f'font-weight:600; letter-spacing:0.3px; text-transform:uppercase; color:{SUBTLE}; '
        f'background-color:{ROW_ALT}; border-bottom:1px solid {BORDER}; white-space:nowrap;">{label}</th>'
        for _, label in _TABLE_COLUMNS
    )

    body_rows = []
    for i, entry in enumerate(entries):
        row_bg = ROW_ALT if i % 2 == 1 else "#ffffff"
        cells = []
        for key, _ in _TABLE_COLUMNS:
            if key == "submission_status":
                status = entry.get(key)
                weight = "600" if _needs_followup(status) else "400"
                cells.append(
                    f'<td style="padding:{ROW_PAD} 12px; font-family:{FONT_STACK}; font-size:12.5px; '
                    f'font-weight:{weight}; color:{INK}; border-bottom:1px solid {BORDER_LIGHT}; white-space:nowrap;">'
                    f'{_t(esc, status)}</td>'
                )
            else:
                cells.append(
                    f'<td style="padding:{ROW_PAD} 12px; font-family:{FONT_STACK}; font-size:12.5px; color:{INK}; '
                    f'border-bottom:1px solid {BORDER_LIGHT};">{_t(esc, entry.get(key))}</td>'
                )
        body_rows.append(f'<tr style="background-color:{row_bg};">{"".join(cells)}</tr>')

    table = f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="min-width:760px; border:1px solid {BORDER}; border-collapse:collapse;">
  <thead><tr>{head_cells}</tr></thead>
  <tbody>{''.join(body_rows)}</tbody>
</table>
"""
    # Outlook ignores overflow-x and will simply expand the message body at
    # min-width -- an accepted, well-known tradeoff for a 9-column table
    # that must keep every column intact.
    return f'<div style="width:100%; overflow-x:auto;">{table}</div>'


def _footer() -> str:
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td style="padding:24px 32px 26px 32px; border-top:1px solid {BORDER_LIGHT};">
      <p style="font-family:{FONT_STACK}; font-size:11px; color:{FAINT}; margin:0; line-height:1.5;">
        Generated automatically by the Recruitment Automation System. This is a system-generated report; no reply is required.
      </p>
    </td>
  </tr>
</table>
"""


# ---------------------------------------------------------------------------
# Public entry point (signature preserved)
# ---------------------------------------------------------------------------

def render_recruiter_body(report: dict[str, Any], *, esc) -> str:
    """
    Build the Recruiter-specific HTML body fragment -- enterprise
    document edition.

    Args:
        report: Frozen daily-report document as a plain dict.
        esc:    HTML-escape callable supplied by the renderer.
    """
    payload = report.get("payload") or {}
    entries: list[dict[str, Any]] = payload.get("entries") or []

    # Display strings are prepared by report_email_renderer.build_email_presentation.
    # Do not format raw report_date / datetime values here.
    business_date = report.get("business_date_display")
    recruiter_name = report.get("recruiter_name_display") or report.get("recruiter_name")
    generated_at = report.get("generated_at_display")

    total = len(entries)
    unique_jobs = len({e.get("job_id") or e.get("job_name") for e in entries if e.get("job_id") or e.get("job_name")})
    unique_clients = len({e.get("client") for e in entries if e.get("client")})

    status_counts = Counter(str(e.get("submission_status") or "Unspecified") for e in entries)
    status_counts_list = list(status_counts.items())

    flagged_entries = [e for e in entries if _needs_followup(e.get("submission_status"))]

    activity_rows = [
        ("Total Submissions", str(total)),
        ("Unique Jobs Worked", str(unique_jobs)),
        ("Unique Clients", str(unique_clients)),
    ]

    followup_group = ""
    if flagged_entries:
        followup_group = _group_label("Needs Follow-up") + _followup_table(flagged_entries, esc)

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#ffffff;">
  <tr>
    <td>
      {_header(business_date, recruiter_name, generated_at, esc)}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="padding:0 32px;">
            {_section_heading("Summary")}
            {_group_label("Activity")}
            {_kv_table(activity_rows)}
            {_group_label("Outcomes")}
            {_status_table(status_counts_list, esc)}
            {followup_group}
            {_section_heading("Candidate Submissions")}
            <div style="margin-top:10px;">{_candidate_table(entries, esc)}</div>
          </td>
        </tr>
      </table>
      {_footer()}
    </td>
  </tr>
</table>
"""