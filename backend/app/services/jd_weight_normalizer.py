"""
app/services/jd_weight_normalizer.py

Normalize LLM-produced requirement weights so they always sum to exactly 100.

Why this exists
---------------
The JD analyzer LLM is instructed to assign weights that sum to 100, but it
frequently hallucinates the math (e.g. returns weights that sum to 115 while
writing ``"total_weight": 100``).  When ``ats_scoring.py`` trusts these
inflated weights, candidates are scored out of 115+ points instead of 100,
then silently clamped — artificially inflating scores.

Design rules
------------
- Pure functions. No side effects.
- No asyncio, no DB, no HTTP.
- Safe to import from RQ worker process AND from migration scripts.
- Never raises for business failures other than mathematical impossibility.
- All logging is structured (event=... key=value).

Single source of truth
-----------------------
Both ``app/workers/jd_tasks.py`` and ``scripts/fix_weight_normalization.py``
import ``normalize_jd_weights`` from here.  Never duplicate this logic.

Algorithm: Three-Layer Defence
-------------------------------
Layer 1 — Convergence Guard:
    If ``num_requirements > 100``, normalization is mathematically impossible
    (minimum sum after max(1,...) floor = num_requirements > 100).
    Raises ValueError so the caller can mark the task as failed and retry.

Layer 2 — Scale + Floor:
    scale           = 100 / actual_sum
    new_weight      = max(1, round(weight * scale))
    Ensures no requirement collapses to 0 and gets silently dropped.

Layer 3 — Remainder Distribution:
    After floor, sum may differ from 100 due to rounding.
    Distribute ±1 to highest/lowest requirements until sum == 100.
    Never reduces any weight below 1.
    Convergence is guaranteed because Layer 1 ensures num_requirements ≤ 100.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Maximum number of requirements that can be normalised to sum=100
# (mathematical limit: each weight ≥ 1, so num_reqs > 100 is impossible)
_MAX_REQUIREMENTS: int = 100


def normalize_jd_weights(
    result:  dict[str, Any],
    job_id:  str,
) -> dict[str, Any]:
    """
    Normalise LLM-produced requirement weights so they sum to exactly 100.

    Returns a deep copy of ``result`` with corrected weights and
    ``total_weight`` set to 100.  The original dict is never mutated.

    Args:
        result:  The raw dict returned by ``analyze_jd()`` — i.e. the value
                 that will be stored as ``jd_analysis.result`` in MongoDB.
                 Expected keys: ``critical_requirements``,
                 ``secondary_requirements``, ``total_weight``.
        job_id:  Naukri job code (for log context only).

    Returns:
        A corrected copy of ``result`` where all weights sum to exactly 100.

    Raises:
        ValueError: If ``num_requirements > 100`` (convergence impossible),
                    if there are no requirements, or if all weights are zero.
    """
    result = copy.deepcopy(result)

    critical  = result.get("critical_requirements",  [])
    secondary = result.get("secondary_requirements", [])

    # all_reqs holds references into the deepcopy — mutations propagate back.
    all_reqs: list[dict[str, Any]] = critical + secondary

    # ── Layer 1: Convergence guard ─────────────────────────────────────────────
    num_reqs = len(all_reqs)

    if num_reqs == 0:
        raise ValueError(
            f"job_id={job_id}: LLM returned no requirements — "
            "cannot normalize weights."
        )

    if num_reqs > _MAX_REQUIREMENTS:
        raise ValueError(
            f"job_id={job_id}: LLM produced {num_reqs} requirements. "
            f"Normalization to 100 is mathematically impossible "
            f"(minimum post-floor sum = {num_reqs} > 100). "
            "Marking as failed so RQ retries the LLM call."
        )

    actual_sum: int = sum(int(r.get("weight", 0)) for r in all_reqs)

    if actual_sum == 0:
        raise ValueError(
            f"job_id={job_id}: All requirement weights are 0 — "
            "cannot normalize."
        )

    # ── No-op: already correct ─────────────────────────────────────────────────
    if actual_sum == 100:
        logger.info(
            "event=jd_weight_normalizer.no_op job_id=%s "
            "num_reqs=%d sum=100",
            job_id, num_reqs,
        )
        return result

    logger.warning(
        "event=jd_weight_normalizer.correcting "
        "job_id=%s num_reqs=%d original_sum=%d",
        job_id, num_reqs, actual_sum,
    )

    # ── Layer 2: Scale + Floor ─────────────────────────────────────────────────
    scale: float = 100.0 / actual_sum
    for req in all_reqs:
        raw = int(req.get("weight", 0))
        req["weight"] = max(1, round(raw * scale))

    # ── Layer 3: Remainder distribution ───────────────────────────────────────
    current_sum: int = sum(r["weight"] for r in all_reqs)
    remainder:   int = 100 - current_sum

    if remainder > 0:
        # Add 1 to highest-weight requirements first (least proportional distortion)
        sorted_desc = sorted(all_reqs, key=lambda r: r["weight"], reverse=True)
        for i in range(remainder):
            # Cycle in case remainder > num_reqs (edge case from heavy flooring)
            sorted_desc[i % num_reqs]["weight"] += 1

    elif remainder < 0:
        # Subtract 1 from lowest-weight requirements first, never below 1.
        # Convergence is guaranteed: Layer 1 ensures num_reqs ≤ 100,
        # so after flooring current_sum ≤ 100 + rounding_slack < 200,
        # and we always have requirements with weight > 1 to subtract from.
        sorted_asc  = sorted(all_reqs, key=lambda r: r["weight"])
        to_subtract = abs(remainder)
        for req in sorted_asc:
            if to_subtract == 0:
                break
            if req["weight"] > 1:
                req["weight"] -= 1
                to_subtract   -= 1

    # ── Overwrite total_weight ─────────────────────────────────────────────────
    result["total_weight"] = 100

    final_sum = sum(r["weight"] for r in all_reqs)
    logger.warning(
        "event=jd_weight_normalizer.corrected "
        "job_id=%s original_sum=%d final_sum=%d",
        job_id, actual_sum, final_sum,
    )

    # Defensive assertion — should never fire, but surfaces bugs immediately
    assert final_sum == 100, (
        f"normalize_jd_weights: final_sum={final_sum} != 100 for job_id={job_id}. "
        "This is a bug in the normalizer."
    )

    return result
