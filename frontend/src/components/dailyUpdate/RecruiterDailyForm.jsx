import { useEffect, useState } from "react";
import { Plus, Trash2, Pencil, Lock, Unlock } from "lucide-react";
import {
  EMPTY_RECRUITER_SUBMISSION,
  RECRUITER_SUBMISSION_COLUMNS,
  RECRUITER_STATUS_OPTIONS,
} from "../../lib/dailyUpdateSchema";

const inputCls =
  "h-9 w-full min-w-0 rounded-lg border border-slate-300 bg-white px-2.5 text-sm text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-500";

export default function RecruiterDailyForm({
  recruiterName,
  reportDate,
  onLeave,
  setOnLeave,
  submissions,
  setSubmissions,
  errors,
  canEdit,
}) {
  /** Row indices currently unlocked for editing. New rows start unlocked. */
  const [unlockedRows, setUnlockedRows] = useState(() => new Set([0]));

  // Reset lock state when switching report date — first empty row starts editable.
  useEffect(() => {
    setUnlockedRows(canEdit && !onLeave ? new Set([0]) : new Set());
  }, [reportDate, canEdit, onLeave]);

  const isRowEditable = (idx) =>
    canEdit && !onLeave && unlockedRows.has(idx);

  const handleChange = (idx, key, value) => {
    if (!isRowEditable(idx)) return;
    setSubmissions((prev) => {
      const next = [...prev];
      next[idx] = { ...next[idx], [key]: value };
      return next;
    });
  };

  const unlockRow = (idx) => {
    if (!canEdit || onLeave) return;
    setUnlockedRows((prev) => new Set(prev).add(idx));
  };

  const lockRow = (idx) => {
    setUnlockedRows((prev) => {
      const next = new Set(prev);
      next.delete(idx);
      return next;
    });
  };

  const addRow = () => {
    setSubmissions((prev) => {
      const next = [...prev, EMPTY_RECRUITER_SUBMISSION()];
      setUnlockedRows((u) => new Set(u).add(next.length - 1));
      return next;
    });
  };

  const removeRow = (idx) => {
    setSubmissions((prev) => {
      if (prev.length <= 1) return prev;
      return prev.filter((_, i) => i !== idx);
    });
    setUnlockedRows((prev) => {
      const next = new Set();
      [...prev].forEach((i) => {
        if (i < idx) next.add(i);
        else if (i > idx) next.add(i - 1);
      });
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-slate-200 bg-slate-50 px-5 py-4">
        <p className="text-sm text-slate-700">
          <span className="font-semibold">Format:</span> Same as your daily email to management —
          one row per candidate submitted today. Fill a row, then lock it. Use Edit to change a
          locked row.
        </p>
        <label className="mt-3 flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={onLeave}
            disabled={!canEdit}
            onChange={(e) => setOnLeave(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300"
          />
          On leave today (no submissions)
        </label>
      </div>

      {errors.submissions && (
        <p className="text-xs text-red-600">{errors.submissions}</p>
      )}

      <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-slate-200 bg-[#14344a] px-6 py-3">
          <div>
            <h2 className="text-sm font-bold text-white">Candidate submissions</h2>
            <p className="text-xs text-slate-300">
              {recruiterName} ·{" "}
              {reportDate
                ? new Date(`${reportDate}T12:00:00`).toLocaleDateString("en-GB")
                : "—"}
            </p>
          </div>
          {canEdit && !onLeave && (
            <button
              type="button"
              onClick={addRow}
              className="inline-flex items-center gap-1.5 rounded-lg bg-white/15 px-3 py-1.5 text-xs font-medium text-white hover:bg-white/25"
            >
              <Plus className="h-3.5 w-3.5" />
              Add row
            </button>
          )}
        </div>

        <div className="overflow-x-auto p-4">
          <table className="min-w-[1280px] w-full border-collapse text-sm">
            <thead>
              <tr className="bg-slate-100 text-xs font-semibold text-slate-600">
                <th className="border border-slate-200 px-2 py-2 text-left">Date</th>
                <th className="border border-slate-200 px-2 py-2 text-left">Recruiter</th>
                {RECRUITER_SUBMISSION_COLUMNS.map((col) => (
                  <th key={col.key} className="border border-slate-200 px-2 py-2 text-left">
                    {col.label}
                    {col.required && <span className="text-red-500"> *</span>}
                  </th>
                ))}
                <th className="w-20 border border-slate-200 px-2 py-2 text-center">Edit</th>
                <th className="w-20 border border-slate-200 px-2 py-2 text-center">Lock</th>
                <th className="w-10 border border-slate-200" />
              </tr>
            </thead>
            <tbody>
              {submissions.map((row, idx) => {
                const editable = isRowEditable(idx);
                const locked = !editable;

                return (
                  <tr
                    key={idx}
                    className={locked ? "bg-slate-50/80" : "bg-white"}
                  >
                    <td className="border border-slate-200 bg-slate-50 px-2 py-2 text-slate-600">
                      {reportDate
                        ? new Date(`${reportDate}T12:00:00`).toLocaleDateString("en-GB")
                        : "—"}
                    </td>
                    <td className="border border-slate-200 bg-slate-50 px-2 py-2 font-medium text-slate-800">
                      {recruiterName || "—"}
                    </td>
                    {RECRUITER_SUBMISSION_COLUMNS.map((col) => {
                      const rowErr = errors.submissionRowErrors?.[idx]?.[col.key];
                      return (
                        <td
                          key={col.key}
                          className="border border-slate-200 px-2 py-1.5 align-top"
                        >
                          {col.type === "status-select" ? (
                            <select
                              value={row[col.key]}
                              disabled={!editable}
                              onChange={(e) => handleChange(idx, col.key, e.target.value)}
                              className={inputCls}
                            >
                              <option value="">Status</option>
                              {RECRUITER_STATUS_OPTIONS.map((opt) => (
                                <option key={opt} value={opt}>
                                  {opt}
                                </option>
                              ))}
                            </select>
                          ) : (
                            <input
                              type={col.type === "email" ? "email" : "text"}
                              disabled={!editable}
                              value={row[col.key]}
                              onChange={(e) => handleChange(idx, col.key, e.target.value)}
                              className={inputCls}
                            />
                          )}
                          {rowErr && (
                            <p className="mt-0.5 text-[10px] text-red-600">{rowErr}</p>
                          )}
                        </td>
                      );
                    })}

                    {/* Edit column */}
                    <td className="border border-slate-200 px-2 py-1.5 text-center">
                      <button
                        type="button"
                        title={locked ? "Unlock row to edit" : "Already editing"}
                        disabled={!canEdit || onLeave || editable}
                        onClick={() => unlockRow(idx)}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        Edit
                      </button>
                    </td>

                    {/* Lock column */}
                    <td className="border border-slate-200 px-2 py-1.5 text-center">
                      {locked ? (
                        <span
                          className="inline-flex items-center gap-1 rounded-full bg-slate-200 px-2.5 py-1 text-[11px] font-medium text-slate-600"
                          title="Row is locked — click Edit to change"
                        >
                          <Lock className="h-3 w-3" />
                          Locked
                        </span>
                      ) : (
                        <button
                          type="button"
                          title="Lock row (read-only until Edit)"
                          disabled={!canEdit || onLeave}
                          onClick={() => lockRow(idx)}
                          className="inline-flex items-center gap-1 rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-40"
                        >
                          <Unlock className="h-3.5 w-3.5" />
                          Lock
                        </button>
                      )}
                    </td>

                    <td className="border border-slate-200 px-1 py-1.5">
                      {canEdit && !onLeave && submissions.length > 1 && (
                        <button
                          type="button"
                          onClick={() => removeRow(idx)}
                          className="rounded-lg p-1.5 text-slate-400 hover:bg-red-50 hover:text-red-600"
                          title="Remove row"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
