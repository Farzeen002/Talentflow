import { Activity, Loader2 } from "lucide-react";
import { computeAtsRunBreakdown } from "../lib/atsHelpers";

/**
 * Opens ATS progress modal — shows done / in queue / remaining on the button.
 */
const AtsProgressButton = ({
  atsStatus,
  loading = false,
  onClick,
  className = "",
}) => {
  console.log("ATS STATUS:", atsStatus);

  console.log(
    "BREAKDOWN:",
    computeAtsRunBreakdown(atsStatus)
  );
  const breakdown = computeAtsRunBreakdown(atsStatus);
  const showCounts = breakdown.total > 0 || breakdown.isActive;

  return (
    <button
      type="button"
      onClick={onClick}
      id="check-ats-progress-btn"
      className={`inline-flex items-center gap-2 rounded-xl border border-[#14344a]/25 bg-[#14344a]/5 px-4 py-2.5 text-sm font-medium text-[#14344a] transition hover:bg-[#14344a]/10 ${className}`}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Activity
          className={`h-4 w-4 ${breakdown.isActive ? "animate-pulse" : ""}`}
        />
      )}
      <span>Check ATS progress</span>
      {showCounts && (
        <span className="rounded-full bg-white/80 px-2 py-0.5 text-xs font-semibold text-[#14344a] tabular-nums">
          {breakdown.isActive && breakdown.inQueue > 0
            ? `${breakdown.inQueue} in queue`
            : `${breakdown.done} done · ${breakdown.remaining} left`}
        </span>
      )}
    </button>
  );
};

export default AtsProgressButton;
