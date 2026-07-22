"""
app/llm/resume_cleaner.py

Resume soft cleaner for ATS scoring pipeline.

Takes raw extracted resume text (UTF-8 string from GCS extracted.txt)
and returns cleaned text ready for LLM evaluation.

Design rules:
  - Only removes what is 100% useless for matching:
      encoding artifacts, PII (email/phone), decorative separators,
      excess whitespace, non-ASCII garbage from bad PDF extraction.
  - Never strips skill names, experience descriptions, or any content
      that a recruiter would read.
  - Pure function — no I/O, no DB, no side effects.
  - Called inside ats_tasks.py worker per candidate.
  - Result is ephemeral — never stored, never uploaded.

Public interface (as agreed in implementation plan):
    clean_resume_text(text: str) -> str
"""

from __future__ import annotations

import re


def clean_resume_text(text: str) -> str:
    """
    Soft-clean raw resume text for LLM ATS evaluation.

    Removes encoding artifacts, PII fields (email, phone), decorative
    separator lines, excess whitespace, and non-printable characters.
    Content that carries semantic signal is never removed.

    Args:
        text: Raw UTF-8 string read from GCS ``extracted.txt``.

    Returns:
        Cleaned string, ready to pass to the LLM prompt.
        Always a string — never raises.
    """

    # ── 1. Decode common encoding artifacts from PDF extraction ──────────────
    encoding_fixes = {
        '\x00': '',       # null bytes
        '\x0c': '\n',     # form feed → newline
        '\xa0': ' ',      # non-breaking space → regular space
        '\u2022': '',     # bullet •
        '\u2023': '',     # bullet ‣
        '\u25cf': '',     # bullet ●
        '\u25aa': '',     # bullet ▪
        '\u2013': '-',    # en dash → hyphen
        '\u2014': '-',    # em dash → hyphen
        '\u2018': "'",    # left single quote
        '\u2019': "'",    # right single quote
        '\u201c': '"',    # left double quote
        '\u201d': '"',    # right double quote
        '\ufffd': '',     # replacement char (garbled text)
        '\u00a0': ' ',    # another non-breaking space variant
    }
    for bad_char, replacement in encoding_fixes.items():
        text = text.replace(bad_char, replacement)

    # ── 2. Strip PII — name/phone/email not needed for ATS scoring ───────────
    text = re.sub(r'[\w\.-]+@[\w\.-]+\.\w+', '', text)                          # email
    text = re.sub(r'(\+91[\s\-]?)?\d{10}', '', text)                            # Indian mobile
    text = re.sub(r'(\+\d{1,3}[\s\-]?)?\(?\d{3}\)?[\s\-]\d{3}[\s\-]\d{4}',    # intl phone
                  '', text)

    # ── 3. Remove decorative / separator lines ───────────────────────────────
    text = re.sub(r'[-=_*~#|]{3,}', '', text)                                   # --- === ___ etc
    text = re.sub(r'[•\-\*]\s*$', '', text, flags=re.MULTILINE)                 # lone bullet

    # ── 4. Collapse excess whitespace / blank lines ──────────────────────────
    text = re.sub(r'[ \t]+', ' ', text)                                          # tabs/spaces → one
    text = re.sub(r'\n{3,}', '\n\n', text)                                       # 3+ newlines → 2
    text = re.sub(r'^\s+$', '', text, flags=re.MULTILINE)                        # whitespace-only lines

    # ── 5. Remove residual non-ASCII garbage (bad PDF parse encoding junk) ───
    text = re.sub(r'[^\x09\x0A\x0D\x20-\x7E]', ' ', text)                      # keep printable ASCII
    text = re.sub(r' {2,}', ' ', text)                                           # fix double spaces

    # ── 6. Strip leading/trailing whitespace per line ────────────────────────
    lines = [line.strip() for line in text.splitlines()]
    text = '\n'.join(lines)

    return text.strip()
