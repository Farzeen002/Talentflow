import { Loader2, Users, FileSpreadsheet, AlertCircle } from "lucide-react";
import { PERFORMANCE_SUMMARY_COLUMNS } from "../../lib/dailyUpdateSchema";

const textareaCls =
  "w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100";

export default function HrConsolidatedView({
  consolidated,
  loading,
  error,
  reportDate,
  hrNotes,
  setHrNotes,
  errors,
  canEdit,
}) {
  if (loading) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-slate-500">
        <Loader2 className="h-5 w-5 animate-spin" />
        Loading team submissions…
      </div>
    );
  }

  const performanceRows = consolidated?.performanceSummary ?? [];
  const submissionRows = consolidated?.allSubmissions ?? [];
  const teamLeadReports = consolidated?.teamLeadReports ?? [];
  const stats = consolidated?.stats ?? {};

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-violet-200 bg-violet-50 px-5 py-4">
        <div className="flex items-start gap-3">
          <Users className="mt-0.5 h-5 w-5 text-violet-700" />
          <div>
            <p className="text-sm font-semibold text-violet-900">HR consolidated report</p>
            <p className="mt-1 text-xs text-violet-800">
              Tracks every recruiter and team lead for{" "}
              {reportDate
                ? new Date(`${reportDate}T12:00:00`).toLocaleDateString("en-GB")
                : "today"}
              . The 7 PM PDF merges this sheet + team lead summaries and emails management.
            </p>
          </div>
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
          {error}
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-4">
        {[
          ["Recruiters submitted", stats.recruitersSubmitted ?? performanceRows.length],
          ["Candidate rows", stats.totalSubmissions ?? submissionRows.length],
          ["Team lead reports", stats.teamLeadsSubmitted ?? teamLeadReports.length],
          ["Pending", stats.pendingRecruiters ?? "—"],
        ].map(([label, val]) => (
          <div key={label} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <p className="text-2xl font-bold text-slate-900">{val}</p>
            <p className="mt-1 text-xs text-slate-500">{label}</p>
          </div>
        ))}
      </div>

      {/* Excel-style performance table */}
      <section className="rounded-3xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="bg-amber-400 px-6 py-3">
          <h2 className="text-sm font-bold text-amber-950 flex items-center gap-2">
            <FileSpreadsheet className="h-4 w-4" />
            Recruiter performance summary (all team)
          </h2>
        </div>
        <div className="overflow-x-auto p-4">
          {performanceRows.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-500">
              No recruiter KPI rows yet for this date.
            </p>
          ) : (
            <table className="min-w-[900px] w-full border-collapse text-sm">
              <thead>
                <tr className="bg-amber-100 text-xs font-semibold text-amber-950">
                  <th className="border border-amber-200 px-3 py-2 text-left">Recruiters</th>
                  {PERFORMANCE_SUMMARY_COLUMNS.map((col) => (
                    <th key={col.key} className="border border-amber-200 px-2 py-2 text-center">
                      {col.short}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {performanceRows.map((row, i) => (
                  <tr key={row.recruiterName ?? i}>
                    <td className="border border-slate-200 px-3 py-2 font-medium">
                      {row.onLeave ? "Leave" : row.recruiterName ?? "—"}
                    </td>
                    {PERFORMANCE_SUMMARY_COLUMNS.map((col) => (
                      <td
                        key={col.key}
                        className="border border-slate-200 px-2 py-2 text-center"
                      >
                        {row[col.key] ?? row.performanceSummary?.[col.key] ?? 0}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {/* All candidate submissions */}
      <section className="rounded-3xl border border-slate-200 bg-white shadow-sm overflow-hidden">
        <div className="bg-red-600 px-6 py-3">
          <h2 className="text-sm font-bold text-white">All candidate submissions</h2>
        </div>
        <div className="overflow-x-auto p-4">
          {submissionRows.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-500">
              No candidate rows submitted yet.
            </p>
          ) : (
            <table className="min-w-[1000px] w-full border-collapse text-sm">
              <thead>
                <tr className="bg-red-50 text-xs font-semibold text-red-900">
                  <th className="border border-red-100 px-2 py-2">Recruiter</th>
                  <th className="border border-red-100 px-2 py-2">Date</th>
                  <th className="border border-red-100 px-2 py-2">Requirement</th>
                  <th className="border border-red-100 px-2 py-2">Candidate</th>
                  <th className="border border-red-100 px-2 py-2">Job Title</th>
                  <th className="border border-red-100 px-2 py-2">Client</th>
                  <th className="border border-red-100 px-2 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {submissionRows.map((row, i) => (
                  <tr key={i} className="hover:bg-slate-50">
                    <td className="border border-slate-200 px-2 py-2">{row.recruiterName}</td>
                    <td className="border border-slate-200 px-2 py-2">{row.submissionDate}</td>
                    <td className="border border-slate-200 px-2 py-2">
                      {row.requirementCode ?? row.requirements}
                    </td>
                    <td className="border border-slate-200 px-2 py-2">{row.candidateName}</td>
                    <td className="border border-slate-200 px-2 py-2">{row.jobTitle}</td>
                    <td className="border border-slate-200 px-2 py-2">{row.client}</td>
                    <td className="border border-slate-200 px-2 py-2">{row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {/* Team lead narratives */}
      {teamLeadReports.length > 0 && (
        <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm space-y-4">
          <h2 className="text-sm font-semibold text-slate-900">Team lead updates</h2>
          {teamLeadReports.map((tl, i) => (
            <div key={i} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
              <p className="font-semibold text-slate-900">{tl.recruiterName ?? tl.authorName}</p>
              <p className="mt-1 text-xs text-slate-500">
                Overall: {tl.teamLeadReport?.overallStatus ?? tl.overallStatus ?? "—"}
              </p>
              <p className="mt-2 text-sm text-slate-700 whitespace-pre-wrap">
                {tl.teamLeadReport?.keyActivitiesCompleted ?? tl.keyActivitiesCompleted ?? "—"}
              </p>
            </div>
          ))}
        </section>
      )}

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <label className="mb-2 block text-sm font-semibold text-slate-900">
          HR notes (included in consolidated PDF to management)
        </label>
        <textarea
          rows={4}
          disabled={!canEdit}
          value={hrNotes}
          onChange={(e) => setHrNotes(e.target.value)}
          placeholder="Optional HR commentary before final 7 PM send…"
          className={textareaCls}
        />
        {errors.hrNotes && <p className="mt-1 text-xs text-red-600">{errors.hrNotes}</p>}
      </section>
    </div>
  );
}
