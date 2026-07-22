"""
Lead Recruiter Daily Report email body — Enterprise Document edition (v4).

Changelog from v3 (PR review fixes):
    - Row padding unified to 8px vertically across all repeating rows
      (metrics table rows and list rows previously used 8px vs 5px).
    - Header metadata (Business Date / Prepared By / Generated) rebuilt
      as a proper table row with border-left dividers instead of
      stacked &nbsp; spacer entities -- guarantees consistent spacing
      and predictable wrap behavior across Outlook/Gmail/Apple Mail.
    - Removed unused _num() helper (dead code from an earlier version).
    - Eyebrow label shortened to "LEAD RECRUITER" -- the H1 already
      says "Daily Recruitment Report," so the eyebrow no longer
      repeats "daily report."
    - Section heading "Recruitment Summary" renamed to "Summary" to
      match the vocabulary used in the Recruiter report.
    - Ordered-list markers ("1.", "2.", ...) no longer forced into a
      fixed 20px column, so double-digit numbers don't crowd the text.
    - Generated timestamp added to the header (alongside Business Date
      and Prepared By) for parity with the Recruiter report's header,
      if the field is present on the report/payload.

Design philosophy is unchanged from v3: typography and spacing carry
the hierarchy, not color or decoration. No status badges, no KPI
cards, no colored warning boxes, no auto-generated narrative sentences.

Public contract is unchanged:
    render_lead_body(report, *, esc, ul_items) -> str

`ul_items` is accepted for signature compatibility with existing call
sites; unused internally, since this design's lists need a specific
plain-dash treatment a generic <ul> can't produce.

No backend, payload, API, business logic, or rendering architecture is
touched -- presentation layer only.
"""

from __future__ import annotations

from typing import Any, Callable

# ---------------------------------------------------------------------------
# Design tokens -- deliberately small palette
# ---------------------------------------------------------------------------

FONT_STACK = "Segoe UI, Arial, Helvetica, sans-serif"

INK = "#1f2328"        # primary text -- charcoal, not pure black
SUBTLE = "#57606a"      # labels, secondary text
FAINT = "#8b949e"       # meta text, list markers, footer
BORDER = "#d0d7de"      # structural dividers (header rule, table borders)
BORDER_LIGHT = "#eaeef2"  # row hairlines
ROW_ALT = "#f6f8fa"     # alternating row background
ACCENT = "#0969da"      # single muted enterprise blue, used sparingly

ROW_PAD = "8px"         # single vertical padding value for every repeating row


def _t(esc: Callable[[Any], str], value: Any, dash: str = "\u2014") -> str:
    if value is None or value == "":
        return dash
    return esc(value)


def _items_text(items: Any) -> list[str]:
    if not items:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            t = item.get("text")
            if t:
                out.append(str(t))
        elif item:
            out.append(str(item))
    return out


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _meta_row(items: list[tuple[str, str]]) -> str:
    """Render label/value metadata pairs as table cells with border-left
    dividers between them -- replaces stacked &nbsp; spacer entities so
    spacing and wrap behavior are consistent across clients."""
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


def _header(business_date: Any, prepared_by: Any, generated_at: Any, esc) -> str:
    date_str = _t(esc, business_date)
    prepared_str = _t(esc, prepared_by)

    meta_items = [("Business Date", date_str), ("Prepared By", prepared_str)]
    if generated_at:
        meta_items.append(("Generated", _t(esc, generated_at)))

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td style="padding:28px 32px 20px 32px; border-bottom:1px solid {BORDER};">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="font-family:{FONT_STACK}; font-size:11px; font-weight:600; letter-spacing:0.6px; color:{ACCENT}; text-transform:uppercase; padding-bottom:6px;">
            Lead Recruiter
          </td>
        </tr>
        <tr>
          <td style="font-family:{FONT_STACK}; font-size:20px; font-weight:600; color:{INK}; line-height:1.3;">
            Daily Recruitment Report
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
# Metrics table (replaces hero cards / funnel bands)
# ---------------------------------------------------------------------------

def _metrics_group_label(label: str) -> str:
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  <tr>
    <td style="padding:14px 0 4px 0; font-family:{FONT_STACK}; font-size:11px; font-weight:600; letter-spacing:0.4px; text-transform:uppercase; color:{FAINT};">
      {label}
    </td>
  </tr>
</table>
"""


def _metrics_table(rows: list[tuple[str, str]]) -> str:
    trs = []
    for label, value in rows:
        trs.append(f"""
  <tr>
    <td style="padding:{ROW_PAD} 0; font-family:{FONT_STACK}; font-size:13px; color:{SUBTLE}; border-bottom:1px solid {BORDER_LIGHT};">{label}</td>
    <td align="right" style="padding:{ROW_PAD} 0; font-family:{FONT_STACK}; font-size:13px; font-weight:600; color:{INK}; border-bottom:1px solid {BORDER_LIGHT};">{value}</td>
  </tr>""")
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
  {''.join(trs)}
</table>
"""


# ---------------------------------------------------------------------------
# Plain lists (activities / risks / plan)
# ---------------------------------------------------------------------------

def _list(items: list[str], esc, empty_text: str, ordered: bool = False) -> str:
    if not items:
        return f'<p style="font-family:{FONT_STACK}; font-size:13px; color:{FAINT}; margin:8px 0 0 0;">{empty_text}</p>'
    rows = []
    for i, text in enumerate(items, start=1):
        marker = f"{i}." if ordered else "\u2013"
        marker_width = "" if ordered else 'width="20"'
        marker_style = "white-space:nowrap;" if ordered else ""
        rows.append(f"""
  <tr>
    <td {marker_width} valign="top" style="{marker_style} padding:{ROW_PAD} 6px {ROW_PAD} 0; font-family:{FONT_STACK}; font-size:13px; color:{FAINT};">{marker}</td>
    <td valign="top" style="padding:{ROW_PAD} 0; font-family:{FONT_STACK}; font-size:13px; color:{INK}; line-height:1.55;">{esc(text)}</td>
  </tr>""")
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:6px;">{"".join(rows)}</table>'


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

def render_lead_body(report: dict[str, Any], *, esc, ul_items) -> str:
    """
    Build the Lead-specific HTML body fragment -- enterprise document edition.

    Args:
        report:   Frozen daily-report document as a plain dict.
        esc:      HTML-escape callable supplied by the renderer.
        ul_items: Accepted for signature compatibility; unused (see
                  module docstring).
    """
    payload = report.get("payload") or {}
    rs = payload.get("recruitment_summary") or {}
    tp = payload.get("team_profile_review") or {}
    ld = payload.get("lead_recruitment_delivery") or {}

    # Display strings are prepared by report_email_renderer.build_email_presentation.
    # Do not format raw report_date / datetime values here.
    business_date = report.get("business_date_display")
    prepared_by = (
        report.get("prepared_by_display")
        or report.get("recruiter_name_display")
        or report.get("recruiter_name")
    )
    generated_at = report.get("generated_at_display")

    challenges = _items_text(payload.get("challenges_risks"))
    activities = _items_text(payload.get("key_activities"))
    plan = _items_text(payload.get("plan_for_tomorrow"))

    sourcing_rows = [
        ("Requirements Managed", _t(esc, rs.get("requirements_managed"))),
        ("Profiles Received", _t(esc, tp.get("profiles_received"))),
        ("Profiles Approved", _t(esc, tp.get("profiles_approved"))),
        ("Profiles Rejected", _t(esc, tp.get("profiles_rejected"))),
    ]
    delivery_rows = [
        ("Profiles Submitted", _t(esc, ld.get("profiles_submitted"))),
        ("Interviews", _t(esc, ld.get("interviews"))),
        ("Offers", _t(esc, ld.get("offers"))),
        ("Joinings", _t(esc, ld.get("joinings"))),
    ]

    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#ffffff;">
  <tr>
    <td>
      {_header(business_date, prepared_by, generated_at, esc)}
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="padding:0 32px;">
            {_section_heading("Summary")}
            {_metrics_group_label("Sourcing &amp; Screening")}
            {_metrics_table(sourcing_rows)}
            {_metrics_group_label("Client Delivery")}
            {_metrics_table(delivery_rows)}
            {_section_heading("Challenges &amp; Risks")}
            {_list(challenges, esc, "No challenges reported today.")}
            {_section_heading("Key Activities")}
            {_list(activities, esc, "No activities logged today.")}
            {_section_heading("Plan for Tomorrow")}
            {_list(plan, esc, "No items planned.", ordered=True)}
          </td>
        </tr>
      </table>
      {_footer()}
    </td>
  </tr>
</table>
"""