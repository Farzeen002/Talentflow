import { X } from "lucide-react";
import {
  RECRUITER_ENTRY_COLUMNS,
  LEAD_METRIC_SECTIONS,
  kindLabel,
  statusBadgeClass,
  statusLabel,
  displayRecipients,
  submissionStatusLabel,
} from "../../lib/dailyUpdateSchema";

/**
 * Read-only view of a sent (or any) report — used from Report History → View.
 */
const ReportViewModal = ({ report, onClose }) => {
  if (!report) return null;

  const recipients = displayRecipients(report);
  const isLead = report.reportKind === "lead";
  const entries = report.payload?.entries ?? [];
  const payload = report.payload ?? {};

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-900/40 p-0 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="report-view-title"
      onClick={onClose}
    >
      <div
        className="flex max-h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-t-2xl bg-white shadow-xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 border-b border-slate-200 px-4 py-4 sm:px-6">
          <div className="min-w-0">
            <h2
              id="report-view-title"
              className="text-base font-semibold text-slate-900 sm:text-lg"
            >
              Sent report
            </h2>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <span
                className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs font-semibold ${statusBadgeClass(report.status)}`}
              >
                {statusLabel(report.status)}
              </span>
              <span className="text-xs text-slate-600">
                {report.reportDate} · {kindLabel(report.reportKind)}
              </span>
              {report.delivery?.sentAt && (
                <span className="text-xs text-emerald-700">
                  Delivered{" "}
                  {new Date(report.delivery.sentAt).toLocaleString("en-IN")}
                </span>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl border border-slate-200 p-2 text-slate-600 hover:bg-slate-50"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4 sm:px-6 sm:py-5">
          <section className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Recipients
            </h3>
            <p className="mt-1 text-sm text-slate-800">
              <span className="font-medium text-slate-600">To:</span>{" "}
              {(recipients.to ?? []).join(", ") || "—"}
            </p>
            <p className="mt-0.5 text-sm text-slate-800">
              <span className="font-medium text-slate-600">CC:</span>{" "}
              {(recipients.cc ?? []).join(", ") || "—"}
            </p>
          </section>

          {isLead ? (
            <>
              {LEAD_METRIC_SECTIONS.map((section) => {
                const values = payload[section.key] ?? {};
                return (
                  <section
                    key={section.key}
                    className="rounded-xl border border-slate-200 bg-white p-4"
                  >
                    <h3 className="text-sm font-semibold text-slate-900">
                      {section.title}
                    </h3>
                    <dl className="mt-3 grid gap-2 sm:grid-cols-2">
                      {section.fields.map((f) => (
                        <div
                          key={f.key}
                          className="rounded-lg bg-slate-50 px-3 py-2"
                        >
                          <dt className="text-[11px] font-medium text-slate-500">
                            {f.label}
                          </dt>
                          <dd className="mt-0.5 text-sm font-semibold text-slate-900">
                            {values[f.key] ?? "—"}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </section>
                );
              })}
              {[
                { key: "keyActivities", title: "Key activities" },
                { key: "challengesRisks", title: "Challenges & risks" },
                { key: "planForTomorrow", title: "Plan for tomorrow" },
              ].map(({ key, title }) => {
                const items = payload[key] ?? [];
                return (
                  <section
                    key={key}
                    className="rounded-xl border border-slate-200 bg-white p-4"
                  >
                    <h3 className="text-sm font-semibold text-slate-900">
                      {title}
                    </h3>
                    {items.length === 0 ? (
                      <p className="mt-2 text-sm text-slate-500">None</p>
                    ) : (
                      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-slate-800">
                        {items.map((item, idx) => (
                          <li key={item.itemId ?? idx}>
                            {typeof item === "string"
                              ? item
                              : item.text ?? item.content ?? "—"}
                          </li>
                        ))}
                      </ul>
                    )}
                  </section>
                );
              })}
            </>
          ) : (
            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 bg-slate-50 px-4 py-2.5">
                <h3 className="text-sm font-semibold text-slate-900">
                  Candidate submissions
                </h3>
              </div>
              {entries.length === 0 ? (
                <p className="px-4 py-8 text-center text-sm text-slate-500">
                  No entries on this report.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full min-w-[800px] text-left text-xs">
                    <thead>
                      <tr className="border-b border-slate-100 text-[10px] uppercase tracking-wide text-slate-500">
                        {RECRUITER_ENTRY_COLUMNS.map((c) => (
                          <th key={c.key} className="px-3 py-2.5 font-semibold">
                            {c.label}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((entry) => (
                        <tr
                          key={entry.entryId}
                          className="border-b border-slate-50"
                        >
                          {RECRUITER_ENTRY_COLUMNS.map((col) => (
                            <td key={col.key} className="px-3 py-2 align-top">
                              {col.key === "submissionStatus"
                                ? submissionStatusLabel(entry[col.key])
                                : entry[col.key] || (
                                    <span className="text-slate-300">—</span>
                                  )}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </div>

        <div className="border-t border-slate-200 px-4 py-3 sm:px-6">
          <button
            type="button"
            onClick={onClose}
            className="w-full rounded-xl bg-[#14344a] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#0f2a3c] sm:w-auto"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
};

export default ReportViewModal;
