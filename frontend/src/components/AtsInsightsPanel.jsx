import { useRef, useState, useCallback, useEffect } from "react";
import {
  Loader2,
  AlertCircle,
  CheckCircle2,
  XCircle,
  MinusCircle,
  RefreshCw,
  Clock,
  Info,
} from "lucide-react";
import { toast } from "sonner";
import { calculateAts, getAtsStatus, fetchCandidateAtsScore } from "../services/ats";

/* ─── constants ─────────────────────────────────── */
const POLL_INTERVAL_MS = 5_000;
const POLL_MAX_ATTEMPTS = 20;

/* ─── tiny helpers ───────────────────────────────── */
const pct = (ratio) =>
  ratio != null ? `${Math.round(ratio * 100)}%` : "—";

const fmtDate = (iso) =>
  iso ? new Date(iso).toLocaleString("en-IN") : "—";

/* ─── skill chip ─────────────────────────────────── */
const SkillChip = ({ label, matched }) => (
  <span
    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium ${matched
      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
      : "border-red-200 bg-red-50 text-red-600"
      }`}
  >
    {matched ? (
      <CheckCircle2 className="h-3 w-3 shrink-0" />
    ) : (
      <XCircle className="h-3 w-3 shrink-0" />
    )}
    {label}
  </span>
);

/* ─── score ring ─────────────────────────────────── */
const ScoreRing = ({ score }) => {
  const color =
    score >= 70 ? "#10b981" : score >= 45 ? "#f59e0b" : "#ef4444";
  const radius = 36;
  const circ = 2 * Math.PI * radius;
  const dash = (score / 100) * circ;

  return (
    <div className="relative flex h-24 w-24 shrink-0 items-center justify-center">
      <svg className="-rotate-90" width="96" height="96">
        <circle
          cx="48"
          cy="48"
          r={radius}
          fill="none"
          stroke="#e2e8f0"
          strokeWidth="8"
        />
        <circle
          cx="48"
          cy="48"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="8"
          strokeDasharray={`${dash} ${circ}`}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute flex flex-col items-center">
        <span className="text-2xl font-bold text-slate-900">{score}</span>
        <span className="text-[10px] text-slate-500">/ 100</span>
      </div>
    </div>
  );
};

/* ─── stat tile ──────────────────────────────────── */
const StatTile = ({ label, value, sub }) => (
  <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-center">
    <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
    <p className="mt-1 text-lg font-bold text-slate-800">{value}</p>
    {sub && <p className="text-[10px] text-slate-400">{sub}</p>}
  </div>
);

/* ═══════════════════════════════════════════════════
   MAIN COMPONENT
═══════════════════════════════════════════════════ */
const AtsInsightsPanel = ({
  candidate,
  candidateId,
  jobId,
  atsData: atsDataProp,
  loading: loadingProp,
  error: errorProp,
  onAtsRefresh, // optional callback: parent can pass to force re-fetch
}) => {
  /* local state so we can update after run/re-run */
  const [atsData, setAtsData] = useState(atsDataProp);
  const [loading, setLoading] = useState(loadingProp ?? false);
  const [runError, setRunError] = useState(null);
  const [polling, setPolling] = useState(false);
  const pollRef = useRef(null);
  const [hideStaleWarning, setHideStaleWarning] = useState(false);

  /* keep in sync with parent prop changes */
  useEffect(() => {
    setAtsData(atsDataProp);
    setLoading(loadingProp ?? false);

    // Reset local stale-warning state when fresh data arrives
    if (!atsDataProp?.isStale) {
      setHideStaleWarning(false);
    }
  }, [atsDataProp, loadingProp]);

  /* ── polling logic ── */
  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    setPolling(false);
  }, []);

  const startPolling = useCallback(
    (jId, cId) => {
      let attempts = 0;
      setPolling(true);

      pollRef.current = setInterval(async () => {
        attempts += 1;

        if (attempts > POLL_MAX_ATTEMPTS) {
          stopPoll();
          toast.error("ATS scoring timed out. Please try again later.");
          return;
        }

        try {
          const status = await getAtsStatus(jId);

          if (status?.status === "completed") {
            stopPoll();
            // re-fetch the candidate's score
            try {
              const fresh = await fetchCandidateAtsScore(cId, jId);
              setAtsData(fresh);
              toast.success("ATS score updated!");
              onAtsRefresh?.();
            } catch {
              toast.error("Scoring finished but failed to reload score.");
            }
          } else if (status?.status === "failed") {
            stopPoll();
            toast.error("ATS scoring failed. Please try again.");
            setRunError("ATS scoring failed on the server.");
          }
          // still "processing" → keep polling
        } catch {
          // network blip — keep polling
        }
      }, POLL_INTERVAL_MS);
    },
    [stopPoll, onAtsRefresh]
  );

  /* ── trigger run / re-run ── */
  const handleRunAts = useCallback(async () => {
    const jId = jobId ?? candidate?.jobId ?? candidate?.job_id;
    const cId = candidateId;

    if (!jId || !cId) {
      toast.error("Missing job or candidate ID.");
      return;
    }

    setHideStaleWarning(true); // 👈 hide banner immediately

    setRunError(null);
    setLoading(true);

    try {
      await calculateAts(jId);
      setLoading(false);
      startPolling(jId, cId);
    } catch (err) {
      setHideStaleWarning(false); // restore if run failed

      setLoading(false);
      const msg =
        err?.response?.data?.detail ?? "Failed to start ATS calculation.";
      setRunError(msg);
      toast.error(msg);
    }
  }, [jobId, candidate, candidateId, startPolling]);

  /* ════════════════ RENDER STATES ════════════════ */

  /* global loading */
  if (loading || loadingProp) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <div className="flex items-center gap-3">
          <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
          <p className="text-sm text-slate-500">Loading ATS insights…</p>
        </div>
      </div>
    );
  }

  /* fetch error from parent */
  if (errorProp && !atsData) {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-5 shadow-sm">
        <div className="flex items-center gap-2 text-red-700">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <p className="text-sm font-semibold">Failed to load ATS data.</p>
        </div>
        <button
          onClick={handleRunAts}
          disabled={polling}
          className="mt-3 flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Retry
        </button>
      </div>
    );
  }

  const status = atsData?.status;

  /* ── status: processing ── */
  if (status === "processing" || polling) {
    return (
      <div className="rounded-2xl border border-blue-200 bg-blue-50 p-5 shadow-sm">
        <div className="flex items-center gap-3">
          <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
          <div>
            <p className="text-sm font-semibold text-blue-700">
              ATS score calculation is in progress.
            </p>
            <p className="mt-0.5 text-xs text-blue-500">
              This usually takes a few seconds. Results will appear automatically.
            </p>
          </div>
        </div>
      </div>
    );
  }

  /* ── status: failed ── */
  if (status === "failed") {
    return (
      <div className="rounded-2xl border border-red-200 bg-red-50 p-5 shadow-sm">
        <div className="flex items-center gap-2 text-red-700">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <p className="text-sm font-semibold">ATS score calculation failed.</p>
        </div>
        {runError && (
          <p className="mt-1 text-xs text-red-500">{runError}</p>
        )}
        <button
          onClick={handleRunAts}
          disabled={polling}
          className="mt-3 flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
        >
          <RefreshCw className="h-3.5 w-3.5" />
          Re-run ATS
        </button>
      </div>
    );
  }

  /* ── status: skipped ── */
  if (status === "skipped") {
    return (
      <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5 shadow-sm">
        <div className="flex items-center gap-2 text-slate-500">
          <Info className="h-5 w-5 shrink-0" />
          <p className="text-sm font-medium">
            Resume not yet processed. ATS cannot run until resume processing completes.
          </p>
        </div>
      </div>
    );
  }

  /* ── status: not_scored or no data ── */
  if (status === "not_scored" || !atsData) {
    return (
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2 text-slate-500">
          <MinusCircle className="h-5 w-5 shrink-0" />
          <p className="text-sm font-medium">
            This candidate has not been scored yet.
          </p>
        </div>
        <button
          onClick={handleRunAts}
          disabled={polling || loading}
          className="mt-3 flex items-center gap-1.5 rounded-lg bg-slate-800 px-4 py-2 text-xs font-medium text-white hover:bg-black disabled:opacity-50"
        >
          {polling ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> Running…
            </>
          ) : (
            <>
              <RefreshCw className="h-3.5 w-3.5" /> Run ATS
            </>
          )}
        </button>
      </div>
    );
  }

  /* ── status: completed ── */
  const bd = atsData?.scoreBreakdown ?? {};
  const {
    finalScore,
    criticalRatio,
    experienceYears,
    jdMinExp,
    matchedCritical,
    totalCritical,
    matchedSkills = [],
    missingSkills = [],
  } = bd;

  const expMet = experienceYears != null && jdMinExp != null
    ? experienceYears >= jdMinExp
    : null;

  return (
    <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">

      {/* ── stale warning ── */}
      {atsData.isStale && !hideStaleWarning && (
        <div className="flex items-center justify-between gap-3 rounded-t-2xl border-b border-amber-200 bg-amber-50 px-4 py-3">
          <div className="flex items-center gap-2 text-amber-700">
            <AlertCircle className="h-4 w-4 shrink-0" />
            <p className="text-xs font-medium">
              This ATS score was generated using an older Job Description version.
            </p>
          </div>
          <button
            onClick={handleRunAts}
            disabled={polling}
            className="flex shrink-0 items-center gap-1.5 rounded-lg border border-amber-300 bg-white px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100 disabled:opacity-50"
          >
            <RefreshCw className="h-3 w-3" />
            Re-run ATS
          </button>
        </div>
      )}

      {/* ── header ── */}
      <div className="flex items-center justify-between px-5 py-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">ATS Insights</h3>
          <p className="mt-0.5 text-xs text-slate-500">
            AI-powered resume match analysis
          </p>
        </div>
        {/* <button
          onClick={handleRunAts}
          disabled={polling}
          title="Re-run ATS scoring"
          className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-40"
        >
          {polling ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Re-run
        </button> */}
      </div>

      <div className="space-y-3 px-5 pb-5">

        {/* ── score + stats row ── */}
        <div className="flex flex-wrap items-center gap-5">
          <ScoreRing score={finalScore ?? 0} />
        </div>

        {/* ── metadata footer ── */}
        {/* <div className="flex flex-wrap gap-4 border-t border-slate-100 pt-3 text-xs text-slate-400">
          <span className="flex items-center gap-1">
            <Clock className="h-3.5 w-3.5" />
            Scored: {fmtDate(atsData.scoredAt)}
          </span>
          <span>JD Analysis v{atsData.jdAnalysisVersion ?? "—"}</span>
          {atsData.isStale && (
            <span className="font-medium text-amber-500">⚠ Stale score</span>
          )}
        </div> */}
      </div>
    </div>
  );
};

export default AtsInsightsPanel;