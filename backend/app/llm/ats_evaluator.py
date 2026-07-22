"""
app/llm/ats_evaluator.py

ATS resume-to-JD matching evaluation.

Usage
-----
    from app.llm.ats_evaluator import evaluate_resume_ats
    from app.llm.factory import get_llm_provider
    from app.config import get_settings

    provider = get_llm_provider(get_settings())
    llm_eval = evaluate_resume_ats(jd_analysis, cleaned_resume_text, provider)
    # llm_eval is a dict with critical_matches, secondary_matches,
    # experience_years, and roles keys — ready to pass to compute_ats_score().

Design notes
------------
- Mirrors jd_analyzer.py exactly: one function, one prompt constant, no class.
- The prompt is a .format() template — {jd_analysis_json} and {resume_text}
  are the two insertion points. {{ and }} in the template are literal { } in
  the final string (Python .format() escaping).
- system_prompt is intentionally minimal: the user-content prompt is
  self-contained and carries all instructions. This matches the pattern in
  jd_analyzer.py where the full prompt is the system message.
- Validation: LLM response must contain expected keys. If it is missing
  required keys, raises ValueError so the worker can mark the candidate
  as 'failed' rather than storing garbage.
- No scoring logic lives here. The LLM returns MATCH SIGNALS only.
  Scoring is computed deterministically in app/services/ats_scoring.py.
"""

from __future__ import annotations

import json
import logging

from app.llm.base import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

# ── Required top-level keys in the LLM response ──────────────────────────────
_REQUIRED_KEYS = frozenset({
    "critical_matches",
    "secondary_matches",
    "experience_years",
    "roles",
})

# ── ATS Resume Match Prompt ───────────────────────────────────────────────────
# This is the system-role instruction. The user content will be empty —
# all context (JD analysis + resume text) is injected into this prompt
# via .format() before sending. This mirrors jd_analyzer.py's pattern.

RESUME_MATCH_PROMPT: str = """\
You are a senior technical recruiter with 15 years of experience
evaluating candidates across all industries and job functions.

Your task is to evaluate a candidate's resume against a set of
pre-analyzed job requirements and produce evidence-based
confidence scores for each requirement.

You are NOT scoring. You are MATCHING.
The score will be computed mathematically from your confidence values.
Your job is to find and report evidence — nothing more.

====================
JD ANALYSIS INPUT
====================
The job has already been analyzed. Here are the requirements:

{jd_analysis_json}

====================
CONFIDENCE SCALE — MANDATORY
====================
For each requirement, assign a confidence value using ONLY
the following anchored ranges. No other values are permitted.

ANCHOR       VALID RANGE    DEFINITION
──────────────────────────────────────────────────────────────────
1.00         0.90 – 1.00    EXACT DIRECT EVIDENCE
                            Resume explicitly names the exact skill,
                            tool, or process required.
                            Example:
                              Req: "Splunk monitoring"
                              Resume: "monitored applications using Splunk"
                              → 0.95

0.85         0.75 – 0.89    STRONG SEMANTIC EQUIVALENT
                            Different name, same function, same domain.
                            Candidate clearly has the capability,
                            just using a different tool or term.
                            Example:
                              Req: "monitoring tools"
                              Resume: "AppDynamics, Dynatrace"
                              → 0.85
                            Example:
                              Req: "CI/CD pipeline integration"
                              Resume: "Azure DevOps Pipelines"
                              → 0.82

0.65         0.55 – 0.74    PARTIAL TRANSFERABLE EVIDENCE
                            Related experience that partially covers
                            the requirement. Real skill, but not full match.
                            Example:
                              Req: "Linux production ownership"
                              Resume: "Linux mentioned in responsibilities
                                       but no production ownership stated"
                              → 0.65
                            Example:
                              Req: "PostgreSQL database management"
                              Resume: "MySQL and Oracle experience"
                              → 0.60

0.40         0.25 – 0.54    WEAK ADJACENT EVIDENCE
                            Peripheral mention. Candidate touched this
                            area but has no demonstrated depth.
                            Example:
                              Req: "Docker container management"
                              Resume: "exposure to containerization"
                              → 0.40
                            Example:
                              Req: "Incident management"
                              Resume: "participated in incident calls"
                              → 0.35

0.00         0.00 – 0.24    NO MEANINGFUL EVIDENCE
                            Not mentioned, not implied, not adjacent.
                            Example:
                              Req: "Oracle database"
                              Resume: no database mention at all
                              → 0.00

VALIDATION RULES:
- You MUST use a value within one of the 5 ranges above.
- Values like 0.5, 0.7, 0.9, 0.3 are valid IF they fall within a range.
- Values like 0.55, 0.67, 0.82 are valid — use the range boundaries as guides.
- Never assign a value outside 0.00–1.00.
- When uncertain between two anchors, use the LOWER anchor.
  It is better to under-credit than to over-credit.

====================
EVIDENCE RULES
====================
For every requirement where matched=true:
- Quote EXACT text from the resume as evidence
- Minimum 1 evidence item, maximum 3
- Evidence must be direct quotes or close paraphrases
- Evidence must justify the confidence value you assigned
- If you cannot find evidence, set matched=false and confidence=0.0

For requirements where matched=false:
- Set confidence to 0.0
- Leave evidence as empty array []
- Do NOT hallucinate evidence

====================
EXPERIENCE EXTRACTION
====================
Extract the candidate's total relevant experience in years.

Rules:
- Use dates/durations from the resume only
- Calculate from earliest relevant role to present
- Round DOWN to nearest integer
- If no dates found → 0
- Include internships only if they are the only experience

====================
MATCHING STEPS — FOLLOW IN ORDER
====================

Step 1: Read the full resume carefully before matching anything.
        Understand the candidate's domain, trajectory, and depth.

Step 2: For each critical requirement:
        - Search the ENTIRE resume (all sections)
        - Find the strongest evidence available
        - Assign confidence using the scale above
        - Quote evidence if matched

Step 3: For each secondary requirement:
        - Same process as Step 2

Step 4: Extract experience_years from dates in the resume.

Step 5: Extract the candidate's primary roles (job titles only).

Step 6: Self-check before output:
        - Is every confidence value within a valid range?
        - Does every matched=true item have evidence?
        - Does every matched=false item have confidence=0.0?
        - Are requirement names exactly as given in the JD analysis?
        If any check fails, correct before outputting.

====================
OUTPUT SCHEMA
====================
Return ONLY valid JSON. No markdown fences. No explanations.
No text before or after the JSON.

{{
  "critical_matches": [
    {{
      "requirement": "Linux/Unix production administration",
      "weight": 25,
      "matched": true,
      "confidence": 0.95,
      "evidence": [
        "4 years Linux/Unix production support at ICICI Securities",
        "24x7 incident resolution on Linux servers"
      ]
    }},
    {{
      "requirement": "incident and problem management",
      "weight": 22,
      "matched": true,
      "confidence": 0.90,
      "evidence": [
        "Incident management member with AppDynamics monitoring",
        "ITIL for Incident management, Change management, Problem Management"
      ]
    }}
  ],
  "secondary_matches": [
    {{
      "requirement": "ITIL framework knowledge",
      "weight": 12,
      "matched": true,
      "confidence": 0.85,
      "evidence": [
        "Good understanding of ITIL for Incident management"
      ]
    }}
  ],
  "experience_years": 3,
  "roles": ["Software Support Engineer", "Application Support Engineer"]
}}

Fields:
- critical_matches: array — one entry per critical requirement
  - requirement: string — EXACT name from JD analysis (do not rephrase)
  - weight: integer — EXACT weight from JD analysis (do not change)
  - matched: boolean — true if confidence >= 0.25
  - confidence: float — value within anchored ranges above
  - evidence: array of strings — direct quotes from resume
- secondary_matches: array — one entry per secondary requirement
  - same structure as critical_matches
- experience_years: integer — total relevant experience
- roles: array of strings — candidate job titles only

====================
RESUME INPUT
====================
{resume_text}
"""


# ══════════════════════════════════════════════════════════════════════════════
# Public function
# ══════════════════════════════════════════════════════════════════════════════

def evaluate_resume_ats(
    jd_analysis:          dict,
    cleaned_resume_text:  str,
    provider:             LLMProvider,
) -> dict:
    """
    Send the JD analysis + cleaned resume text to the LLM and return
    evidence-based match signals for each requirement.

    The LLM does NOT produce a final score. It produces per-requirement
    confidence values and evidence quotes. The deterministic score is
    computed separately by ``compute_ats_score()`` in ats_scoring.py.

    The prompt is self-contained — both JD analysis and resume text are
    injected into the system prompt via .format() before the call.

    Args:
        jd_analysis:         The ``jd_analysis.result`` dict from the job
                             document. Must contain ``critical_requirements``
                             and ``secondary_requirements`` arrays.
        cleaned_resume_text: Resume text after ``clean_resume_text()`` was
                             applied. Must be non-empty.
        provider:            A configured LLMProvider (sync).

    Returns:
        A dict with keys:
          - ``critical_matches``  — list of per-requirement match dicts
          - ``secondary_matches`` — list of per-requirement match dicts
          - ``experience_years``  — int
          - ``roles``             — list of str

    Raises:
        ValueError: If ``cleaned_resume_text`` is empty, or if the LLM
                    response is missing required keys.
        LLMProviderError: Propagated from the provider after retry exhaustion.
    """
    if not cleaned_resume_text or not cleaned_resume_text.strip():
        raise ValueError(
            "evaluate_resume_ats called with empty resume text — "
            "caller should guard before calling."
        )

    # Serialise jd_analysis to pretty JSON for the prompt
    jd_analysis_json = json.dumps(jd_analysis, indent=2, ensure_ascii=False)

    # Build the formatted prompt (injects jd_analysis_json + resume_text)
    formatted_prompt = RESUME_MATCH_PROMPT.format(
        jd_analysis_json=jd_analysis_json,
        resume_text=cleaned_resume_text,
    )

    logger.info(
        "event=ats_eval.started "
        "resume_len=%d jd_requirements=%d",
        len(cleaned_resume_text),
        len(jd_analysis.get("critical_requirements", []))
        + len(jd_analysis.get("secondary_requirements", [])),
    )

    try:
        # Formatted prompt acts as the full system instruction.
        # User content is empty — all context is already in the system prompt.
        result = provider.complete_sync(
            system_prompt=formatted_prompt,
            user_content="Evaluate the resume against the JD requirements above.",
        )
    except LLMProviderError:
        logger.exception("event=ats_eval.provider_error")
        raise

    # ── Validate required top-level keys ─────────────────────────────────────
    missing = _REQUIRED_KEYS - set(result.keys())
    if missing:
        raise ValueError(
            f"LLM response missing required keys: {sorted(missing)}. "
            f"Got keys: {sorted(result.keys())}"
        )

    logger.info(
        "event=ats_eval.completed "
        "critical_matches=%d secondary_matches=%d experience_years=%s",
        len(result.get("critical_matches", [])),
        len(result.get("secondary_matches", [])),
        result.get("experience_years"),
    )

    return result
