import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Loader2,
  ChevronLeft,
  ChevronRight,
  History,
  FileEdit,
  CheckCircle2,
  AlertCircle,
  Plus,
  RefreshCw,
  Eye,
} from "lucide-react";
import { listReports, getReport } from "../../services/dailyUpdates";
import {
  statusBadgeClass,
  statusLabel,
  todayIsoDate,
  loadSavedKind,
  kindLabel,
} from "../../lib/dailyUpdateSchema";
import ReportViewModal from "./ReportViewModal";

const STATUS_TABS = [
  { id: "", label: "All", icon: History },
  { id: "sent", label: "Sent", icon: CheckCircle2 },
  { id: "draft", label: "Not sent (draft)", icon: FileEdit },
  { id: "failed", label: "Failed", icon: AlertCircle },
];

function deliveryLabel(row) {
  if (row.status === "sent" || row.delivery?.sentAt) {
    return {
      text: row.delivery?.sentAt
        ? `Delivered ${new Date(row.delivery.sentAt).toLocaleString("en-IN")}`
        : "Delivered",
      className: "text-emerald-700",
    };
  }
  if (row.status === "failed" || row.delivery?.lastError) {
    return {
      text: row.delivery?.lastError
        ? `Email failed — ${row.delivery.lastError}`
        : "Email failed",
      className: "text-red-700",
    };
  }
  if (row.status === "draft") {
    return { text: "Not submitted yet", className: "text-amber-700" };
  }
  return { text: "—", className: "text-slate-500" };
}

/**
 * History for the report kind locked on Daily Reports (Recruiter OR Lead).
 * No kind picker here — choice is made only on the editor page.
 */
const ReportHistory = ({ onOpenReport, showHeader = true }) => {
  const navigate = useNavigate();
  const lockedKind = loadSavedKind();
  const [items, setItems] = useState([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [limit] = useState(20);
  const [loading, setLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState("");
  const [filterDate, setFilterDate] = useState("");
  const [counts, setCounts] = useState({
    all: null,
    draft: null,
    sent: null,
    failed: null,
  });
  const [countsLoading, setCountsLoading] = useState(true);
  const [openingId, setOpeningId] = useState(null);
  const [viewingReport, setViewingReport] = useState(null);

  const loadCounts = useCallback(async () => {
    if (!lockedKind) return;
    setCountsLoading(true);
    try {
      const base = { reportKind: lockedKind };
      const [allRes, draftRes, sentRes, failedRes] = await Promise.all([
        listReports({ page: 1, limit: 1, ...base }),
        listReports({ page: 1, limit: 1, status: "draft", ...base }),
        listReports({ page: 1, limit: 1, status: "sent", ...base }),
        listReports({ page: 1, limit: 1, status: "failed", ...base }),
      ]);
      setCounts({
        all: allRes.data?.total ?? 0,
        draft: draftRes.data?.total ?? 0,
        sent: sentRes.data?.total ?? 0,
        failed: failedRes.data?.total ?? 0,
      });
    } catch {
      setCounts({ all: null, draft: null, sent: null, failed: null });
    } finally {
      setCountsLoading(false);
    }
  }, [lockedKind]);

  useEffect(() => {
    if (lockedKind) loadCounts();
  }, [lockedKind, loadCounts]);

  useEffect(() => {
    if (!lockedKind) {
      setLoading(false);
      setItems([]);
      setTotal(0);
      return;
    }
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const params = { page, limit, reportKind: lockedKind };
        if (filterStatus) params.status = filterStatus;
        if (filterDate) params.reportDate = filterDate;
        const { data } = await listReports(params);
        if (!cancelled) {
          setItems(data.items ?? []);
          setTotal(data.total ?? 0);
        }
      } catch {
        if (!cancelled) {
          setItems([]);
          setTotal(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [lockedKind, page, limit, filterStatus, filterDate]);

  const totalPages = Math.max(1, Math.ceil(total / limit));

  const openRow = async (reportId, status) => {
    setOpeningId(reportId);
    try {
      const { data } = await getReport(reportId);
      if (onOpenReport) {
        onOpenReport(data);
        return;
      }
      // Sent → stay on history and show what was emailed (read-only)
      if (data.status === "sent" || status === "sent") {
        setViewingReport(data);
        return;
      }
      // Draft / failed → open Daily Reports to continue or resend
      navigate("/daily-update", {
        state: { report: data },
      });
    } catch {
      /* toast from interceptor */
    } finally {
      setOpeningId(null);
    }
  };

  const countFor = (statusId) => {
    if (statusId === "") return counts.all;
    return counts[statusId];
  };

  if (!lockedKind) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-14 text-center">
        <History className="mx-auto h-8 w-8 text-slate-300" />
        <p className="mt-3 text-sm font-semibold text-slate-800">
          Choose Recruiter or Lead first
        </p>
        <p className="mt-1 text-xs text-slate-500">
          Pick a report type on Daily Reports — history will then show only that
          type.
        </p>
        <button
          type="button"
          onClick={() => navigate("/daily-update")}
          className="mt-4 rounded-xl bg-[#14344a] px-4 py-2 text-sm font-semibold text-white"
        >
          Go to Daily Reports
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm sm:px-5">
        <span className="text-xs font-medium uppercase tracking-wide text-slate-500">
          Showing
        </span>
        <span className="inline-flex rounded-full bg-[#14344a] px-3 py-1 text-xs font-semibold text-white">
          {kindLabel(lockedKind)} only
        </span>
        <span className="text-xs text-slate-500">
          Change type on Daily Reports
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          {
            key: "sent",
            label: "Sent",
            hint: "Emailed successfully",
            value: counts.sent,
            icon: CheckCircle2,
            tone: "border-emerald-200 bg-emerald-50 text-emerald-900",
            iconTone: "text-emerald-600",
          },
          {
            key: "draft",
            label: "Not sent",
            hint: "Drafts still open",
            value: counts.draft,
            icon: FileEdit,
            tone: "border-amber-200 bg-amber-50 text-amber-900",
            iconTone: "text-amber-600",
          },
          {
            key: "failed",
            label: "Failed",
            hint: "Submitted, email failed",
            value: counts.failed,
            icon: AlertCircle,
            tone: "border-red-200 bg-red-50 text-red-900",
            iconTone: "text-red-600",
          },
          {
            key: "",
            label: "All reports",
            hint: `${kindLabel(lockedKind)} only`,
            value: counts.all,
            icon: History,
            tone: "border-slate-200 bg-white text-slate-900",
            iconTone: "text-slate-500",
          },
        ].map((card) => {
          const Icon = card.icon;
          const active = filterStatus === card.key && !filterDate;
          return (
            <button
              key={card.key || "all"}
              type="button"
              onClick={() => {
                setPage(1);
                setFilterStatus(card.key);
              }}
              className={`rounded-2xl border p-4 text-left transition ${card.tone} ${
                active ? "ring-2 ring-[#14344a]/40" : "hover:opacity-95"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-medium opacity-80">{card.label}</p>
                  <p className="mt-1 text-2xl font-bold tabular-nums">
                    {countsLoading || card.value == null ? "—" : card.value}
                  </p>
                  <p className="mt-1 text-[11px] opacity-70">{card.hint}</p>
                </div>
                <Icon className={`h-5 w-5 shrink-0 ${card.iconTone}`} />
              </div>
            </button>
          );
        })}
      </div>

      <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
        {showHeader && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-4 py-4 sm:px-6">
            <div>
              <h2 className="text-base font-semibold text-slate-900">
                Report history
              </h2>
              <p className="mt-0.5 text-xs text-slate-500">
                Showing {kindLabel(lockedKind)} reports only.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  loadCounts();
                  setPage(1);
                  setFilterStatus("");
                  setFilterDate("");
                }}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-200 px-3 text-xs font-medium text-slate-700 hover:bg-slate-50"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Refresh
              </button>
              <button
                type="button"
                onClick={() => navigate("/daily-update")}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-[#14344a] px-3 text-xs font-semibold text-white hover:bg-[#0f2a3c]"
              >
                <Plus className="h-3.5 w-3.5" />
                New / today&apos;s report
              </button>
            </div>
          </div>
        )}

        <div className="flex flex-col gap-3 border-b border-slate-100 px-4 py-3 sm:px-6">
          <div className="flex flex-wrap gap-2">
            {STATUS_TABS.map((tab) => {
              const Icon = tab.icon;
              const active = filterStatus === tab.id;
              const n = countFor(tab.id);
              return (
                <button
                  key={tab.id || "all-tab"}
                  type="button"
                  onClick={() => {
                    setPage(1);
                    setFilterStatus(tab.id);
                  }}
                  className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition ${
                    active
                      ? "border-[#14344a] bg-[#14344a] text-white"
                      : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tab.label}
                  {n != null && (
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[10px] tabular-nums ${
                        active ? "bg-white/20" : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {n}
                    </span>
                  )}
                </button>
              );
            })}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <input
              type="date"
              value={filterDate}
              max={todayIsoDate()}
              onChange={(e) => {
                setPage(1);
                setFilterDate(e.target.value);
              }}
              className="h-9 rounded-lg border border-slate-200 bg-white px-2 text-xs"
              title="Filter by report date"
            />
            {filterDate && (
              <button
                type="button"
                onClick={() => {
                  setPage(1);
                  setFilterDate("");
                }}
                className="h-9 rounded-lg border border-slate-200 px-3 text-xs text-slate-600 hover:bg-slate-50"
              >
                Clear date
              </button>
            )}
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading history…
          </div>
        ) : items.length === 0 ? (
          <div className="px-6 py-14 text-center">
            <History className="mx-auto h-8 w-8 text-slate-300" />
            <p className="mt-3 text-sm font-medium text-slate-700">
              No {kindLabel(lockedKind)} reports match these filters
            </p>
            <button
              type="button"
              onClick={() => navigate("/daily-update")}
              className="mt-4 rounded-xl bg-[#14344a] px-4 py-2 text-sm font-semibold text-white"
            >
              Go to Daily Reports
            </button>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[720px] text-left text-sm">
              <thead>
                <tr className="border-b border-slate-100 text-[10px] uppercase tracking-wide text-slate-400">
                  <th className="px-4 py-2.5 font-semibold sm:px-6">Date</th>
                  <th className="px-3 py-2.5 font-semibold">Kind</th>
                  <th className="px-3 py-2.5 font-semibold">Status</th>
                  <th className="px-3 py-2.5 font-semibold">Submitted</th>
                  <th className="px-3 py-2.5 font-semibold">Email / delivery</th>
                  <th className="px-3 py-2.5 font-semibold text-right">Action</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => {
                  const delivery = deliveryLabel(row);
                  const busy = openingId === row.reportId;
                  return (
                    <tr
                      key={row.reportId}
                      className="border-b border-slate-50 transition hover:bg-slate-50"
                    >
                      <td className="px-4 py-3 font-medium text-slate-900 sm:px-6">
                        {row.reportDate}
                      </td>
                      <td className="px-3 py-3 text-slate-600">
                        {kindLabel(row.reportKind)}
                      </td>
                      <td className="px-3 py-3">
                        <span
                          className={`inline-flex rounded-full border px-2 py-0.5 text-[11px] font-semibold ${statusBadgeClass(row.status)}`}
                        >
                          {statusLabel(row.status)}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-xs text-slate-500">
                        {row.submittedAt
                          ? new Date(row.submittedAt).toLocaleString("en-IN")
                          : "—"}
                      </td>
                      <td
                        className={`max-w-[280px] truncate px-3 py-3 text-xs ${delivery.className}`}
                        title={delivery.text}
                      >
                        {delivery.text}
                      </td>
                      <td className="px-3 py-3 text-right">
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => openRow(row.reportId, row.status)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                        >
                          {busy ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <Eye className="h-3.5 w-3.5" />
                          )}
                          {row.status === "draft" ? "Continue" : "View"}
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {totalPages > 1 && (
          <div className="flex items-center justify-between border-t border-slate-100 px-4 py-3 sm:px-6">
            <p className="text-xs text-slate-500">
              Page {page} of {totalPages} · {total} total
            </p>
            <div className="flex gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                className="rounded-lg border border-slate-200 p-1.5 disabled:opacity-40"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                className="rounded-lg border border-slate-200 p-1.5 disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {viewingReport && (
        <ReportViewModal
          report={viewingReport}
          onClose={() => setViewingReport(null)}
        />
      )}
    </div>
  );
};

export default ReportHistory;
