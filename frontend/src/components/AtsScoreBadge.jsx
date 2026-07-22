import React from "react";

/**
 * Visual ATS score badge with color coding and an inline mini-bar.
 *
 * Tiers:
 *   ≥ 75  → Emerald  (strong match)
 *   ≥ 50  → Amber    (moderate match)
 *   < 50  → Rose     (weak match)
 *   null  → Slate    (not yet scored)
 *
 * Props:
 *   score  – number | string | null | undefined
 *   size   – "sm" | "md" | "lg"
 *   showBar – boolean (default true for md/lg)
 */
const AtsScoreBadge = ({ score, size = "md", showBar }) => {
  const showMiniBar = showBar ?? size !== "sm";

  /* ── No score yet ── */
  if (score === null || score === undefined) {
    return (
      <div
        className={`inline-flex items-center gap-1.5 rounded-full border bg-slate-100 text-slate-400 border-slate-200 font-semibold ${
          size === "lg"
            ? "px-4 py-1.5 text-sm"
            : size === "sm"
            ? "px-2 py-0.5 text-[11px]"
            : "px-3 py-1 text-xs"
        }`}
      >
        <span className="h-1.5 w-1.5 rounded-full bg-slate-300" />
        Not scored
      </div>
    );
  }

  const scoreNum = typeof score === "string" ? parseFloat(score) : score;

  /* ── Color tier ── */
  let dotCls, textCls, borderCls, bgCls, barCls;
  if (scoreNum >= 75) {
    dotCls = "bg-emerald-500";
    textCls = "text-emerald-700";
    borderCls = "border-emerald-200";
    bgCls = "bg-emerald-50";
    barCls = "bg-emerald-500";
  } else if (scoreNum >= 50) {
    dotCls = "bg-amber-500";
    textCls = "text-amber-700";
    borderCls = "border-amber-200";
    bgCls = "bg-amber-50";
    barCls = "bg-amber-500";
  } else {
    dotCls = "bg-rose-500";
    textCls = "text-rose-700";
    borderCls = "border-rose-200";
    bgCls = "bg-rose-50";
    barCls = "bg-rose-500";
  }

  const sizeCls =
    size === "lg"
      ? "px-4 py-1.5 text-sm"
      : size === "sm"
      ? "px-2 py-0.5 text-[11px]"
      : "px-3 py-1 text-xs";

  return (
    <div className="flex flex-col items-end gap-1">
      <div
        className={`inline-flex items-center gap-1.5 rounded-full border font-semibold ${sizeCls} ${bgCls} ${textCls} ${borderCls}`}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${dotCls}`} />
        ATS {scoreNum.toFixed(1)}%
      </div>

      {/* Mini progress bar */}
      {showMiniBar && (
        <div className="h-1 w-16 rounded-full bg-slate-200 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${barCls}`}
            style={{ width: `${Math.min(scoreNum, 100)}%` }}
          />
        </div>
      )}
    </div>
  );
};

export default AtsScoreBadge;
