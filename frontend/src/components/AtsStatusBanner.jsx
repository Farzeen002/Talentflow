import { Loader2 } from "lucide-react";
import {
  formatAtsProgress,
  getAtsStatusConfig,
  isAtsRunActive,
  normalizeAtsStatus,
} from "../lib/atsHelpers";

/**
 * Recruiter-facing ATS run status (job-level).
 */
const AtsStatusBanner = ({
  atsStatus,
  loading = false,
  compact = false,
  onViewDetails,
  className = "",
}) => {
  const normalizedStatus = normalizeAtsStatus(atsStatus);
  const status = normalizedStatus?.status ?? "idle";
  const cfg = getAtsStatusConfig(status);
  const progress = formatAtsProgress(normalizedStatus);
  const active = isAtsRunActive(status);

  if (compact) {
    return (
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${cfg.badgeCls} ${className}`}
        title={cfg.sublabel}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${cfg.dotCls}`} />
        {loading && !atsStatus ? (
          <Loader2 className="h-3 w-3 animate-spin" />
        ) : (
          cfg.label
        )}
        {progress && active && (
          <span className="opacity-80">· {progress}</span>
        )}
      </span>
    );
  }

  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 ${cfg.badgeCls} ${className}`}
    >
      <div className="flex min-w-0 items-start gap-3">
        <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${cfg.dotCls}`} />
        <div className="min-w-0">
          <p className="text-sm font-semibold">
            ATS status: {loading && !atsStatus ? "Loading…" : cfg.label}
          </p>
          <p className="mt-0.5 text-xs opacity-90">{cfg.sublabel}</p>
          {progress && (
            <p className="mt-1 text-xs font-medium opacity-80">
              Progress: {progress}
              {normalizedStatus?.failedCandidates > 0 &&
                ` · ${normalizedStatus.failedCandidates} failed`}
            </p>
          )}
        </div>
      </div>
      {onViewDetails && (
        <button
          type="button"
          onClick={onViewDetails}
          className="shrink-0 rounded-lg border border-current/20 bg-white/60 px-3 py-1.5 text-xs font-medium hover:bg-white"
        >
          View details
        </button>
      )}
    </div>
  );
};

export default AtsStatusBanner;
