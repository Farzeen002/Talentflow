import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Loader2,
  Table2,
  CalendarDays,
  Download,
  RefreshCw,
  AlertCircle,
} from "lucide-react";
import {
  getDailyUpdateByDate,
  listRecentDailyUpdates,
} from "../../services/dailyUpdates";
import {
  RECRUITER_SUBMISSION_COLUMNS,
  hydrateRecruiterFromApi,
} from "../../lib/dailyUpdateSchema";

function currentMonthValue() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function monthLabel(yyyyMm) {
  if (!yyyyMm) return "—";
  const [y, m] = yyyyMm.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleString("en-IN", {
    month: "long",
    year: "numeric",
  });
}

function formatDisplayDate(iso) {
  if (!iso) return "—";
  return new Date(`${iso}T12:00:00`).toLocaleDateString("en-GB");
}

function escapeCsv(value) {
  const s = value == null ? "" : String(value);
  if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

/**
 * Excel-style history / progress sheet for recruiters.
 * Filters by month (and optional single date) and flattens daily submission rows.
 */
export default function RecruiterProgressSheet({
  recruiterName,
  onOpenDate,
}) {
  const [month, setMonth] = useState(currentMonthValue);
  const [dayFilter, setDayFilter] = useState("");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastLoadedAt, setLastLoadedAt] = useState(null);

  const loadSheet = useCallback(async () => {
    if (!month) return;
    setLoading(true);
    setError("");
    try {
      // Pull a wide recent window, then expand days in the selected month.
      const { data } = await listRecentDailyUpdates(120, {
        reportType: "recruiter",
        skipErrorToast: true,
      });
      const summaries = (data.updates ?? data ?? []).filter(
        (u) => u?.reportDate && String(u.reportDate).startsWith(month)
      );

      // Prefer summary dates when history API works; otherwise probe month days.
      const [year, mon] = month.split("-").map(Number);
      const daysInMonth = new Date(year, mon, 0).getDate();
      const dates = new Set(summaries.map((u) => u.reportDate));
      if (summaries.length === 0) {
        for (let d = 1; d <= daysInMonth; d++) {
          const iso = `${month}-${String(d).padStart(2, "0")}`;
          if (new Date(`${iso}T12:00:00`) > new Date()) break;
          dates.add(iso);
        }
      }

      const sortedDates = [...dates].sort();
      const flat = [];

      await Promise.all(
        sortedDates.map(async (date) => {
          try {
            const { data: dayData } = await getDailyUpdateByDate(date, "recruiter", {
              skipErrorToast: true,
            });
            const update = dayData.update ?? dayData;
            if (!update || (!update.id && !update.submissions && !update.onLeave)) {
              return;
            }
            const summary = summaries.find((s) => s.reportDate === date);
            const hydrated = hydrateRecruiterFromApi(update);
            const emailedAt =
              dayData.emailedAt ?? summary?.emailedAt ?? update.emailedAt ?? null;

            if (hydrated.onLeave) {
              flat.push({
                reportDate: date,
                onLeave: true,
                emailedAt,
                recruiterName: update.recruiterName ?? recruiterName,
                requirementCode: "—",
                candidateName: "(On leave)",
                jobTitle: "—",
                candidateEmail: "",
                candidatePhone: "",
                locationBranch: "",
                client: "—",
                status: "On leave",
              });
              return;
            }

            const list = (hydrated.submissions ?? []).filter(
              (r) => r.candidateName?.trim() || r.requirementCode?.trim()
            );
            if (list.length === 0) return;

            list.forEach((sub) => {
              flat.push({
                reportDate: date,
                onLeave: false,
                emailedAt,
                recruiterName:
                  sub.recruiterName || update.recruiterName || recruiterName,
                ...sub,
              });
            });
          } catch (err) {
            // 404 = no report that day — skip silently
            if (err?.response?.status && err.response.status !== 404) {
              console.warn("Progress sheet day load failed", date, err);
            }
          }
        })
      );

      flat.sort((a, b) => {
        if (a.reportDate === b.reportDate) {
          return (a.candidateName || "").localeCompare(b.candidateName || "");
        }
        return a.reportDate < b.reportDate ? 1 : -1;
      });

      setRows(flat);
      setLastLoadedAt(new Date());
      if (flat.length === 0) {
        setError(
          `No submitted rows found for ${monthLabel(month)}. Submit a daily update first, or pick another month.`
        );
      }
    } catch {
      setRows([]);
      setError(
        "Could not load progress data. The daily-updates history API may not be available yet."
      );
    } finally {
      setLoading(false);
    }
  }, [month, recruiterName]);

  useEffect(() => {
    loadSheet();
  }, [loadSheet]);

  const visibleRows = useMemo(() => {
    if (!dayFilter) return rows;
    return rows.filter((r) => r.reportDate === dayFilter);
  }, [rows, dayFilter]);

  const stats = useMemo(() => {
    const days = new Set(visibleRows.map((r) => r.reportDate));
    const onLeaveDays = new Set(
      visibleRows.filter((r) => r.onLeave).map((r) => r.reportDate)
    );
    return {
      days: days.size,
      submissions: visibleRows.filter((r) => !r.onLeave).length,
      onLeaveDays: onLeaveDays.size,
    };
  }, [visibleRows]);

  const dayOptions = useMemo(() => {
    const set = new Set(rows.map((r) => r.reportDate));
    return [...set].sort().reverse();
  }, [rows]);

  const exportCsv = () => {
    const headers = [
      "Date",
      "Recruiter",
      ...RECRUITER_SUBMISSION_COLUMNS.map((c) => c.label),
      "Email status",
    ];
    const lines = [headers.join(",")];
    visibleRows.forEach((r) => {
      lines.push(
        [
          formatDisplayDate(r.reportDate),
          r.recruiterName,
          ...RECRUITER_SUBMISSION_COLUMNS.map((c) => r[c.key] ?? ""),
          r.emailedAt ? "Emailed" : "Pending",
        ]
          .map(escapeCsv)
          .join(",")
      );
    });
    const blob = new Blob([lines.join("\n")], {
      type: "text/csv;charset=utf-8;",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `daily-progress-${month}${dayFilter ? `-${dayFilter}` : ""}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-4">
      <section className="rounded-3xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-base font-semibold text-slate-900">
              <Table2 className="h-5 w-5 text-emerald-700" />
              Progress sheet
            </h2>
            <p className="mt-1 text-sm text-slate-500">
              Excel-style view of your submitted candidate rows for{" "}
              <span className="font-medium text-slate-700">
                {monthLabel(month)}
              </span>
              {recruiterName ? ` · ${recruiterName}` : ""}.
            </p>
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600">
                Month
              </label>
              <input
                type="month"
                value={month}
                max={currentMonthValue()}
                onChange={(e) => {
                  setMonth(e.target.value);
                  setDayFilter("");
                }}
                className="h-9 rounded-lg border border-slate-300 px-2.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-slate-600">
                Date (optional)
              </label>
              <select
                value={dayFilter}
                onChange={(e) => setDayFilter(e.target.value)}
                className="h-9 min-w-[10rem] rounded-lg border border-slate-300 bg-white px-2.5 text-sm outline-none focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
              >
                <option value="">All days in month</option>
                {dayOptions.map((d) => (
                  <option key={d} value={d}>
                    {formatDisplayDate(d)}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={loadSheet}
              disabled={loading}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
            <button
              type="button"
              onClick={exportCsv}
              disabled={visibleRows.length === 0}
              className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-emerald-700 px-3 text-sm font-medium text-white hover:bg-emerald-800 disabled:opacity-50"
            >
              <Download className="h-3.5 w-3.5" />
              Export Excel (CSV)
            </button>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-3 gap-2 sm:gap-3">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 px-3 py-3 text-center">
            <p className="text-lg font-bold text-slate-900">{stats.days}</p>
            <p className="text-[10px] text-slate-500 sm:text-xs">Days with data</p>
          </div>
          <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-3 py-3 text-center">
            <p className="text-lg font-bold text-emerald-800">{stats.submissions}</p>
            <p className="text-[10px] text-emerald-700 sm:text-xs">Candidate rows</p>
          </div>
          <div className="rounded-2xl border border-amber-200 bg-amber-50 px-3 py-3 text-center">
            <p className="text-lg font-bold text-amber-800">{stats.onLeaveDays}</p>
            <p className="text-[10px] text-amber-700 sm:text-xs">On-leave days</p>
          </div>
        </div>
      </section>

      <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-emerald-800 bg-emerald-800 px-4 py-2.5 sm:px-6">
          <div className="flex items-center gap-2 text-white">
            <CalendarDays className="h-4 w-4" />
            <span className="text-sm font-semibold">
              {monthLabel(month)}
              {dayFilter ? ` · ${formatDisplayDate(dayFilter)}` : ""}
            </span>
          </div>
          <span className="text-xs text-emerald-100">
            {visibleRows.length} row{visibleRows.length === 1 ? "" : "s"}
            {lastLoadedAt
              ? ` · updated ${lastLoadedAt.toLocaleTimeString("en-IN", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}`
              : ""}
          </span>
        </div>

        {loading ? (
          <div className="flex items-center justify-center gap-2 py-16 text-slate-500">
            <Loader2 className="h-5 w-5 animate-spin" />
            Loading sheet…
          </div>
        ) : visibleRows.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-center">
            <AlertCircle className="h-8 w-8 text-slate-300" />
            <p className="text-sm font-medium text-slate-600">
              {error || "No data for this filter."}
            </p>
            <p className="max-w-md text-xs text-slate-400">
              Switch to “Submit update”, add rows for a date, and submit — they will
              appear here under that month.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-[1100px] w-full border-collapse text-sm">
              <thead>
                <tr className="bg-emerald-50 text-xs font-semibold uppercase tracking-wide text-emerald-900">
                  <th className="sticky left-0 z-10 border border-emerald-100 bg-emerald-50 px-3 py-2.5 text-left">
                    Date
                  </th>
                  <th className="border border-emerald-100 px-3 py-2.5 text-left">
                    Recruiter
                  </th>
                  {RECRUITER_SUBMISSION_COLUMNS.map((col) => (
                    <th
                      key={col.key}
                      className="border border-emerald-100 px-3 py-2.5 text-left"
                    >
                      {col.label}
                    </th>
                  ))}
                  <th className="border border-emerald-100 px-3 py-2.5 text-left">
                    Mail
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleRows.map((row, idx) => (
                  <tr
                    key={`${row.reportDate}-${idx}`}
                    className={
                      idx % 2 === 0
                        ? "bg-white hover:bg-emerald-50/40"
                        : "bg-slate-50/80 hover:bg-emerald-50/40"
                    }
                  >
                    <td className="sticky left-0 z-10 border border-slate-200 bg-inherit px-3 py-2 font-medium text-slate-800">
                      {onOpenDate ? (
                        <button
                          type="button"
                          onClick={() => onOpenDate(row.reportDate)}
                          className="text-left text-emerald-800 underline-offset-2 hover:underline"
                          title="Open this date in Submit update"
                        >
                          {formatDisplayDate(row.reportDate)}
                        </button>
                      ) : (
                        formatDisplayDate(row.reportDate)
                      )}
                    </td>
                    <td className="border border-slate-200 px-3 py-2 text-slate-700">
                      {row.recruiterName || "—"}
                    </td>
                    {RECRUITER_SUBMISSION_COLUMNS.map((col) => (
                      <td
                        key={col.key}
                        className={`border border-slate-200 px-3 py-2 text-slate-700 ${
                          row.onLeave ? "italic text-slate-400" : ""
                        }`}
                      >
                        {row[col.key] || "—"}
                      </td>
                    ))}
                    <td className="border border-slate-200 px-3 py-2">
                      {row.emailedAt ? (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-800">
                          Emailed
                        </span>
                      ) : (
                        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold text-amber-800">
                          Pending
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
