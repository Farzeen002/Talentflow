"""
app/services/ats_scoring.py

ATS score computation — pure math, no LLM, no I/O.

Responsibilities
----------------
  LLM (ats_evaluator.py)    → produces per-requirement confidence values + evidence
  THIS MODULE                → validates those values, computes the score, applies caps
  ats_tasks.py (worker)      → calls this after LLM evaluation, stores results

Pipeline (exactly as in the reference llm_handler.py):
  1. validate_matches()      → clamp confidences, re-anchor weights from JD, sanitize
  2. compute_raw_score()     → pure weighted sum: Σ(weight × confidence)
  3. post_process()          → hard caps and floors (experience gaps, critical coverage)
  4. compute_ats_score()     → public API: orchestrates 1–3, returns (score, breakdown)

Design rules
------------
  - Pure functions. No side effects.
  - No asyncio, no DB, no HTTP.
  - Safe to import from RQ worker process.
  - Never raises for business failures — returns safe defaults.
  - All logging is structured (event=... key=value).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# Constants
# ══════════════════════════════════════════════════════════════════════════════

# A requirement "counts" as meaningfully matched at partial evidence or better.
# Used for critical_ratio calculation (determines floors).
_PARTIAL_THRESHOLD: float = 0.65

# Below this confidence, a requirement is treated as unmatched (matched=False).
_MATCH_THRESHOLD: float = 0.25

# Max evidence items stored per requirement (mirrors reference max 3).
_MAX_EVIDENCE: int = 3


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Validate and sanitize LLM match output
# ══════════════════════════════════════════════════════════════════════════════

def _clamp_confidence(value: Any, requirement: str) -> float:
    """
    Coerce and clamp an LLM confidence value to [0.0, 1.0].

    Any value already in range is accepted — the 5-anchor scale is enforced
    by the prompt, not re-validated here (prompt violations are already a
    degraded result). Out-of-range values are clamped with a warning.

    Args:
        value:       Raw confidence from LLM response.
        requirement: Requirement name (for log context only).

    Returns:
        Float in [0.0, 1.0], rounded to 2 decimal places.
    """
    try:
        c = float(value)
    except (TypeError, ValueError):
        logger.warning(
            "event=ats_scoring.confidence_invalid "
            "requirement=%r raw=%r defaulting_to=0.0",
            requirement, value,
        )
        return 0.0

    if c < 0.0 or c > 1.0:
        clamped = max(0.0, min(1.0, c))
        logger.warning(
            "event=ats_scoring.confidence_out_of_range "
            "requirement=%r value=%.4f clamped_to=%.4f",
            requirement, c, clamped,
        )
        return round(clamped, 2)

    return round(c, 2)


def validate_matches(
    llm_evaluation: dict[str, Any],
    jd_analysis:    dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and normalize the LLM match output against the JD analysis.

    Key rules (adapted from reference validate_matches):
      - Weights are always taken from ``jd_analysis`` — never trusted from LLM.
        This prevents the LLM from inflating or deflating requirements.
      - Confidence is clamped to [0.0, 1.0].
      - ``matched`` is re-derived from confidence (>= _MATCH_THRESHOLD),
        not trusted from the LLM boolean.
      - Evidence is sanitized to a max of 3 strings.
      - experience_years is coerced to a non-negative integer.
      - roles is sanitized to a list of max 5 strings.

    Args:
        llm_evaluation: Dict returned by ``evaluate_resume_ats()``.
        jd_analysis:    The ``jd_analysis.result`` dict from the job document.

    Returns:
        Validated dict with keys:
          critical_matches, secondary_matches, experience_years, roles.
        Never raises — returns zero-match defaults on any structural failure.
    """
    # Build requirement → weight lookup from JD analysis (source of truth)
    weight_lookup: dict[str, int] = {}
    for req in jd_analysis.get("critical_requirements", []):
        weight_lookup[req["requirement"]] = req["weight"]
    for req in jd_analysis.get("secondary_requirements", []):
        weight_lookup[req["requirement"]] = req["weight"]

    def _validate_match_list(items: Any, label: str) -> list[dict]:
        if not isinstance(items, list):
            logger.warning(
                "event=ats_scoring.validate_matches.not_list label=%s", label
            )
            return []

        validated: list[dict] = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                logger.warning(
                    "event=ats_scoring.validate_matches.skip "
                    "label=%s idx=%d reason=not_dict",
                    label, i,
                )
                continue

            requirement = str(item.get("requirement", "")).strip()
            if not requirement:
                logger.warning(
                    "event=ats_scoring.validate_matches.skip "
                    "label=%s idx=%d reason=empty_requirement",
                    label, i,
                )
                continue

            # Weight: anchor to JD analysis. Use LLM value as fallback only.
            weight = weight_lookup.get(requirement)
            if weight is None:
                logger.warning(
                    "event=ats_scoring.validate_matches.weight_fallback "
                    "requirement=%r not_in_jd_analysis "
                    "using_llm_weight",
                    requirement,
                )
                try:
                    weight = max(1, int(item.get("weight", 5)))
                except (TypeError, ValueError):
                    weight = 5

            # Confidence — clamp + round
            confidence = _clamp_confidence(item.get("confidence", 0.0), requirement)

            # Matched — re-derived from confidence, never trusted from LLM
            matched = confidence >= _MATCH_THRESHOLD

            # Evidence — sanitize to list of strings, max 3
            raw_evidence = item.get("evidence", [])
            if isinstance(raw_evidence, list):
                evidence = [
                    str(e).strip() for e in raw_evidence
                    if e and str(e).strip()
                ][:_MAX_EVIDENCE]
            else:
                evidence = []

            if matched and not evidence:
                logger.warning(
                    "event=ats_scoring.validate_matches.matched_no_evidence "
                    "requirement=%r confidence=%.2f",
                    requirement, confidence,
                )

            validated.append({
                "requirement": requirement,
                "weight":      weight,
                "matched":     matched,
                "confidence":  confidence,
                "evidence":    evidence,
            })

        return validated

    if not llm_evaluation or not isinstance(llm_evaluation, dict):
        logger.warning(
            "event=ats_scoring.validate_matches.null_input "
            "returning zero-match defaults"
        )
        return {
            "critical_matches":  [],
            "secondary_matches": [],
            "experience_years":  0,
            "roles":             [],
        }

    critical_matches  = _validate_match_list(
        llm_evaluation.get("critical_matches", []),  "critical_matches"
    )
    secondary_matches = _validate_match_list(
        llm_evaluation.get("secondary_matches", []), "secondary_matches"
    )

    # experience_years — coerce to non-negative int
    try:
        experience_years = max(0, int(llm_evaluation.get("experience_years", 0) or 0))
    except (TypeError, ValueError):
        experience_years = 0

    # roles — list of strings, max 5
    raw_roles = llm_evaluation.get("roles", [])
    if not isinstance(raw_roles, list):
        raw_roles = []
    roles = [str(r).strip() for r in raw_roles if r and str(r).strip()][:5]

    logger.info(
        "event=ats_scoring.validate_matches.done "
        "critical_matched=%d/%d secondary_matched=%d/%d "
        "experience_years=%d roles=%s",
        sum(1 for m in critical_matches  if m["matched"]),
        len(critical_matches),
        sum(1 for m in secondary_matches if m["matched"]),
        len(secondary_matches),
        experience_years,
        roles,
    )

    return {
        "critical_matches":  critical_matches,
        "secondary_matches": secondary_matches,
        "experience_years":  experience_years,
        "roles":             roles,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Raw score via weighted sum
# ══════════════════════════════════════════════════════════════════════════════

def compute_raw_score(
    critical_matches:  list[dict],
    secondary_matches: list[dict],
) -> tuple[int, float]:
    """
    Compute the raw ATS score from validated match signals.

    Formula (direct weighted sum — identical to reference compute_score):
        score = Σ (weight × confidence)  for ALL requirements

    Because weights already sum to 100 and confidence ∈ [0.0, 1.0],
    the result is naturally on a 0–100 scale. No secondary adjustment
    factor is applied — that would be double-weighting.

    Args:
        critical_matches:  Validated critical match list from validate_matches().
        secondary_matches: Validated secondary match list from validate_matches().

    Returns:
        Tuple of:
          raw_score (int):       Integer 0–100.
          critical_ratio (float): Fraction of critical requirements met at
                                   confidence >= 0.65 (partial or better).
                                   Used by post_process() for floors/caps.
    """
    all_matches = critical_matches + secondary_matches

    if not all_matches:
        logger.warning(
            "event=ats_scoring.compute_raw.no_matches returning 0"
        )
        return 0, 0.0

    # Direct weighted sum — weights already sum to 100
    total_score: float = sum(
        m["weight"] * m["confidence"]
        for m in all_matches
    )

    # Critical ratio: fraction of critical reqs met at partial evidence or better
    matched_critical = sum(
        1 for m in critical_matches
        if m["confidence"] >= _PARTIAL_THRESHOLD
    )
    total_critical  = max(len(critical_matches), 1)
    critical_ratio  = matched_critical / total_critical

    raw_score = round(total_score)

    logger.info(
        "event=ats_scoring.compute_raw.done "
        "raw_score=%d critical_ratio=%.2f "
        "matched_critical=%d/%d",
        raw_score, critical_ratio, matched_critical, total_critical,
    )

    return raw_score, critical_ratio


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — Post-processor: hard caps and floors only
# ══════════════════════════════════════════════════════════════════════════════

def post_process(
    score:            int,
    experience_years: int,
    roles:            list[str],
    jd_min_exp:       int,
    critical_ratio:   float,
) -> tuple[int, list[str]]:
    """
    Apply hard caps and floors to the raw weighted score.

    Implements real-world hiring logic that the pure weighted sum cannot
    capture. Rules are applied in a strict order: floors first, then caps,
    then absolute bounds.

    Rules (mirrors reference post_process exactly):
        Floor 1: All critical requirements met (ratio == 1.0)
                 → minimum score of 78
        Floor 2: Most critical requirements met (ratio >= 0.85)
                 → minimum score of 70
        Cap 1:   Moderate experience gap (2–4 yrs below JD minimum)
                 → maximum score of 70
        Cap 2:   Severe experience gap (4+ yrs below JD minimum)
                 → maximum score of 55
        Cap 3:   Fresher with no roles detected
                 → maximum score of 20
        Bound:   Clamp final score to [0, 100]

    Args:
        score:            Raw score from compute_raw_score().
        experience_years: Candidate total relevant experience in years.
        roles:            Candidate job titles (empty = no work history detected).
        jd_min_exp:       Minimum experience years required by the JD.
        critical_ratio:   Fraction of critical reqs met at >= 0.65.

    Returns:
        Tuple of:
          final_score (int):        Final clamped integer score.
          applied_rules (list[str]): Human-readable list of rules that fired.
    """
    original     = score
    applied_rules: list[str] = []

    # ── Floors (applied first) ────────────────────────────────────────────────

    if critical_ratio == 1.0:
        if score < 78:
            score = 78
            applied_rules.append(
                f"Floor: all critical reqs met → raised from {original} to {score}"
            )

    elif critical_ratio >= 0.85:
        if score < 70:
            score = 70
            applied_rules.append(
                f"Floor: {critical_ratio:.0%} critical reqs met → raised from {original} to {score}"
            )

    # ── Caps (applied after floors) ───────────────────────────────────────────

    # Cap 1: Moderate experience gap (2 to 4 years below minimum)
    if jd_min_exp > 0 and experience_years < jd_min_exp - 2:
        if score > 70:
            before = score
            score  = min(score, 70)
            applied_rules.append(
                f"Cap: moderate exp gap ({experience_years}yr < "
                f"{jd_min_exp - 2}yr) → capped from {before} to {score}"
            )

    # Cap 2: Severe experience gap (4+ years below minimum)
    if jd_min_exp > 0 and experience_years < jd_min_exp - 4:
        if score > 55:
            before = score
            score  = min(score, 55)
            applied_rules.append(
                f"Cap: severe exp gap ({experience_years}yr < "
                f"{jd_min_exp - 4}yr) → capped from {before} to {score}"
            )

    # Cap 3: Fresher — no experience and no roles detected
    if experience_years == 0 and not roles:
        if score > 20:
            before = score
            score  = min(score, 20)
            applied_rules.append(
                f"Cap: no experience/roles detected → capped from {before} to {score}"
            )

    # ── Absolute bounds ───────────────────────────────────────────────────────
    score = max(0, min(100, score))

    if applied_rules:
        logger.info(
            "event=ats_scoring.post_process.rules_applied "
            "original=%d final=%d rules=%s",
            original, score, applied_rules,
        )
    else:
        logger.info(
            "event=ats_scoring.post_process.no_change score=%d", score
        )

    return score, applied_rules


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def compute_ats_score(
    llm_evaluation: dict[str, Any],
    jd_analysis:    dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    """
    Full ATS scoring pipeline — pure math, no LLM, no I/O.

    Called by ``ats_tasks.calculate_ats_task()`` immediately after
    ``evaluate_resume_ats()`` returns the match signals.

    Pipeline:
        1. validate_matches()   → anchors weights to JD, clamps confidences
        2. compute_raw_score()  → Σ(weight × confidence), derives critical_ratio
        3. post_process()       → hard caps and floors
        4. Build score_breakdown for MongoDB storage

    Args:
        llm_evaluation: Dict returned by ``evaluate_resume_ats()``.
                        Expected keys: critical_matches, secondary_matches,
                        experience_years, roles.
        jd_analysis:    The ``jd_analysis.result`` dict from the job document.
                        Used for weight anchoring and experience minimum.

    Returns:
        Tuple of:
          score (float):          Final integer score as float (0.0 – 100.0).
          score_breakdown (dict): Full breakdown for MongoDB storage:
            - raw_score:          Score before post-processing.
            - final_score:        Score after post-processing.
            - critical_ratio:     Fraction of critical reqs met at >= 0.65.
            - matched_critical:   Count of critical reqs met.
            - total_critical:     Total critical requirements.
            - matched_skills:     Requirements met at >= 0.65 confidence.
            - missing_skills:     Critical requirements with confidence < 0.25.
            - post_process_rules: List of rules that fired during post-processing.
            - jd_min_exp:         JD minimum experience used for cap logic.
            - experience_years:   Candidate experience used for cap logic.
    """
    # ── Step 1: Validate ──────────────────────────────────────────────────────
    validated = validate_matches(llm_evaluation, jd_analysis)

    critical_matches  = validated["critical_matches"]
    secondary_matches = validated["secondary_matches"]
    experience_years  = validated["experience_years"]
    roles             = validated["roles"]

    # ── Step 2: Raw score ─────────────────────────────────────────────────────
    raw_score, critical_ratio = compute_raw_score(critical_matches, secondary_matches)

    # ── Step 3: Post-process ──────────────────────────────────────────────────
    jd_min_exp   = (jd_analysis.get("experience_required") or {}).get("min", 0)
    final_score, applied_rules = post_process(
        score=            raw_score,
        experience_years= experience_years,
        roles=            roles,
        jd_min_exp=       jd_min_exp,
        critical_ratio=   critical_ratio,
    )

    # ── Step 4: Build breakdown ───────────────────────────────────────────────
    matched_skills = [
        m["requirement"]
        for m in critical_matches + secondary_matches
        if m["confidence"] >= _PARTIAL_THRESHOLD
    ]
    missing_skills = [
        m["requirement"]
        for m in critical_matches
        if m["confidence"] < _MATCH_THRESHOLD
    ]

    matched_critical = sum(
        1 for m in critical_matches if m["confidence"] >= _PARTIAL_THRESHOLD
    )

    score_breakdown: dict[str, Any] = {
        "raw_score":          raw_score,
        "final_score":        final_score,
        "critical_ratio":     round(critical_ratio, 4),
        "matched_critical":   matched_critical,
        "total_critical":     len(critical_matches),
        "matched_skills":     matched_skills,
        "missing_skills":     missing_skills,
        "post_process_rules": applied_rules,
        "jd_min_exp":         jd_min_exp,
        "experience_years":   experience_years,
    }

    logger.info(
        "event=ats_scoring.compute_ats_score.done "
        "raw=%d final=%d critical_ratio=%.2f "
        "matched=%d missing=%d",
        raw_score, final_score, critical_ratio,
        len(matched_skills), len(missing_skills),
    )

    return float(final_score), score_breakdown
