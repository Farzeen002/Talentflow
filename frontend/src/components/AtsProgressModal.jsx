import { useEffect, useState, useRef } from "react";
import {
  X,
  Loader2,
  CheckCircle2,
  AlertCircle,
  RotateCcw,
  Clock,
  Zap,
  TrendingUp,
  RefreshCw,
  ListOrdered,
  Hourglass,
} from "lucide-react";
import { toast } from "sonner";
import { rerunAts } from "../services/ats";
import {
  computeAtsRunBreakdown,
  getAtsStatusConfig,
  normalizeAtsStatus,
} from "../lib/atsHelpers";

const STATUS_ICONS = {
  idle: Clock,
  queued: ListOrdered,
  processing: Zap,
  completed: CheckCircle2,
  partially_failed: AlertCircle,
  failed: AlertCircle,
};

const ProgressRing = ({ percent = 0, status }) => {
  const r = 52;
  const circ = 2 * Math.PI * r;
  const dash = circ - (percent / 100) * circ;
  const ringColor =
    status === "completed"
      ? "#22c55e"
      : status === "failed"
        ? "#ef4444"
        : status === "partially_failed"
          ? "#f59e0b"
          : "#6366f1";

  return (
    <svg width="120" height="120" viewBox="0 0 120 120" className="-rotate-90">
      <circle cx="60" cy="60" r={r} fill="none" stroke="#e2e8f0" strokeWidth="10" />
      <circle
        cx="60"
        cy="60"
        r={r}
        fill="none"
        stroke={ringColor}
        strokeWidth="10"
        strokeLinecap="round"
        strokeDasharray={circ}
        strokeDashoffset={dash}
        style={{ transition: "stroke-dashoffset 0.6s ease" }}
      />
    </svg>
  );
};

const CountCard = ({ label, value, sub, icon: Icon, variant = "slate" }) => {
  const styles = {
    emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
    violet: "border-violet-200 bg-violet-50 text-violet-900",
    amber: "border-amber-200 bg-amber-50 text-amber-900",
    slate: "border-slate-200 bg-slate-50 text-slate-900",
    rose: "border-rose-200 bg-rose-50 text-rose-900",
  };
  return (
    <div className={`rounded-2xl border p-4 ${styles[variant] ?? styles.slate}`}>
      <div className="flex items-center gap-2 text-xs font-medium opacity-80">
        {Icon && <Icon className="h-4 w-4" />}
        {label}
      </div>
      <p className="mt-2 text-3xl font-bold tabular-nums">{value}</p>
      {sub && <p className="mt-1 text-xs opacity-75">{sub}</p>}
    </div>
  );
};

const DetailRow = ({ label, value }) => (
  <div className="flex justify-between gap-4 border-b border-slate-100 py-2.5 text-sm last:border-0">
    <span className="text-slate-500">{label}</span>
    <span className="font-medium text-slate-800 text-right">{value}</span>
  </div>
);

const AtsProgressModal = ({
  isOpen,
  onClose,
  atsStatus,
  loading,
  error,
  jobId,
  onRerun,
  onRefresh,
}) => {
  const [rerunLoading, setRerunLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const onRefreshRef = useRef(onRefresh);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    onRefreshRef.current = onRefresh;
  }, [onRefresh]);

  useEffect(() => {
    if (isOpen && !wasOpenRef.current) {
      onRefreshRef.current?.();
    }
    wasOpenRef.current = isOpen;
  }, [isOpen]);

  if (!isOpen) return null;

  const normalizedAtsStatus = normalizeAtsStatus(atsStatus);
  const status = normalizedAtsStatus?.status ?? "idle";
  const cfg = getAtsStatusConfig(status);
  const breakdown = computeAtsRunBreakdown(normalizedAtsStatus);
  const StatusIcon = STATUS_ICONS[status] ?? Clock;

  const handleRefresh = async () => {
    if (!onRefresh) return;
    setRefreshing(true);
    await onRefresh();
    setRefreshing(false);
  };

  const handleRerun = async () => {
    try {
      setRerunLoading(true);
      await rerunAts(jobId);
      toast.success("Full ATS re-run triggered. Processing in background…");
      onRerun?.();
      onRefresh?.();
    } catch (err) {
      const msg = err?.response?.data?.detail ?? "Failed to trigger ATS re-run";
      toast.error(msg);
    } finally {
      setRerunLoading(false);
    }
  };

  const fmt = (iso) =>
    iso
      ? new Date(iso).toLocaleString("en-IN", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
      })
      : "—";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 border-b border-slate-100 px-6 py-5">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <div
                className={`flex h-11 w-11 items-center justify-center rounded-xl border ${cfg.badgeCls}`}
              >
                <StatusIcon
                  className={`h-5 w-5 ${breakdown.isActive ? "animate-pulse" : ""}`}
                />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-slate-900">
                  ATS run status
                </h2>
                <p className="mt-0.5 text-sm text-slate-500">{cfg.sublabel}</p>
                <span
                  className={`mt-2 inline-flex rounded-full border px-2.5 py-0.5 text-xs font-semibold ${cfg.badgeCls}`}
                >
                  {cfg.label}
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={handleRefresh}
                disabled={refreshing || loading}
                className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50 disabled:opacity-50"
                title="Refresh status now"
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 ${refreshing || loading ? "animate-spin" : ""}`}
                />
                Refresh
              </button>
              <button
                type="button"
                onClick={onClose}
                className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
          </div>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {breakdown.isActive && (
            <p className="rounded-xl border border-[#14344a]/15 bg-[#14344a]/5 px-4 py-2.5 text-xs text-[#14344a]">
              Auto-refreshing every 3 seconds while scoring is in progress.
            </p>
          )}

          {!breakdown.isActive && breakdown.skippedExisting > 0 && (
            <div className="rounded-xl border border-blue-200 bg-blue-50 px-4 py-3">
              <p className="text-sm text-blue-800">
                {breakdown.done === 0
                  ? `All ${breakdown.skippedExisting} candidate${breakdown.skippedExisting > 1 ? "s" : ""
                  } already have valid ATS scores. No recalculation was required.`
                  : `${breakdown.skippedExisting} candidate${breakdown.skippedExisting > 1 ? "s were" : " was"
                  } skipped because valid ATS scores already exist.`}
              </p>
            </div>
          )}

          {/* Done · In queue · Remaining */}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <CountCard
              label="New ATS Scores"
              value={breakdown.done}
              sub={
                breakdown.done > 0
                  ? "Calculated in this run"
                  : "No new ATS scores generated"
              }
              icon={CheckCircle2}
              variant="emerald"
            />

            <CountCard
              label="Already Scored"
              value={breakdown.skippedExisting}
              sub="Valid ATS scores already existed"
              icon={TrendingUp}
              variant="violet"
            />

            <CountCard
              label={
                status === "processing" || status === "queued"
                  ? "Remaining"
                  : "Failed"
              }
              value={
                status === "processing" || status === "queued"
                  ? breakdown.remaining
                  : breakdown.failed
              }
              sub={
                status === "processing"
                  ? "Still being processed"
                  : status === "queued"
                    ? "Waiting to be processed"
                    : "Candidates that could not be scored"
              }
              icon={
                status === "processing" || status === "queued"
                  ? Hourglass
                  : AlertCircle
              }
              variant={
                status === "processing" || status === "queued"
                  ? "amber"
                  : "rose"
              }
            />
          </div>

          {/* Progress ring */}
          {breakdown.total > 0 && (
            <div className="flex flex-wrap items-center gap-6 rounded-2xl border border-slate-200 bg-slate-50 p-5">
              <div className="relative shrink-0">
                <ProgressRing
                  percent={breakdown.progressPercent}
                  status={status}
                />
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                  {status === "queued" && breakdown.inQueue > 0 ? (
                    <Loader2 className="h-6 w-6 animate-spin text-violet-500" />
                  ) : (
                    <>
                      <span className="text-2xl font-bold text-slate-900">
                        {breakdown.progressPercent}%
                      </span>
                      <span className="text-[10px] text-slate-500">complete</span>
                    </>
                  )}
                </div>
              </div>
              <div className="min-w-[200px] flex-1 space-y-2">
                <div className="flex justify-between text-xs text-slate-500">
                  <span>Overall progress</span>
                  <span className="font-semibold text-slate-800">
                    {breakdown.accounted} / {breakdown.total} accounted for
                  </span>
                </div>
                <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-200">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-[#14344a] to-[#1e455e] transition-all duration-700"
                    style={{ width: `${breakdown.progressPercent}%` }}
                  />
                </div>
                {atsStatus?.mode && (
                  <p className="flex items-center gap-1.5 text-xs text-slate-600">
                    <TrendingUp className="h-3.5 w-3.5 text-[#14344a]" />
                    {atsStatus.mode === "incremental"
                      ? "Incremental - only candidates without ATS scores are processed. Existing scores are reused automatically."
                      : "Force re-run - all candidates rescored"}
                  </p>
                )}
              </div>
            </div>
          )}

          {status === "queued" && breakdown.total === 0 && (
            <div className="flex items-center gap-3 rounded-2xl border border-violet-200 bg-violet-50 px-4 py-4">
              <Loader2 className="h-6 w-6 shrink-0 animate-spin text-violet-600" />
              <div>
                <p className="text-sm font-medium text-violet-900">
                  Job is queued
                </p>
                <p className="mt-0.5 text-xs text-violet-700">
                  Candidate counts will appear when the worker picks up this run.
                </p>
              </div>
            </div>
          )}

          {/* Full API breakdown */}
          <div className="rounded-2xl border border-slate-200 bg-white p-4">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
              Run details
            </h3>
            <DetailRow label="Run status" value={cfg.label} />
            <DetailRow
              label="Total candidates (this run)"
              value={breakdown.total}
            />
            <DetailRow
              label="Processed (scored successfully)"
              value={breakdown.done}
            />
            <DetailRow label="Failed (errors)" value={breakdown.failed} />
            <DetailRow
              label="Skipped (score already valid)"
              value={breakdown.skippedExisting}
            />
            <DetailRow
              label="Skipped (no resume)"
              value={breakdown.skippedNoResume}
            />
            <DetailRow label="Started at" value={fmt(atsStatus?.triggeredAt)} />
            <DetailRow
              label="Completed at"
              value={fmt(atsStatus?.completedAt)}
            />
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              {error}
            </div>
          )}
        </div>

        <div className="shrink-0 flex flex-wrap gap-2 border-t border-slate-100 px-6 py-4">
          {/* {breakdown.isEnded && (
            <button
              type="button"
              onClick={handleRerun}
              disabled={rerunLoading}
              className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl border border-[#14344a]/25 bg-[#14344a]/5 px-4 py-2.5 text-sm font-medium text-[#14344a] hover:bg-[#14344a]/10 disabled:opacity-50"
            >
              {rerunLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="h-4 w-4" />
              )}
              Force re-run
            </button>
          )} */}
          <button
            type="button"
            onClick={onClose}
            className="flex-1 rounded-xl bg-[#14344a] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#0f2a3c]"
          >
            {breakdown.isActive ? "Close (keeps updating)" : "Close"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default AtsProgressModal;
