import { useState } from "react";
import { Loader2, Plus, Trash2, Pencil, Check, X } from "lucide-react";
import {
  RECRUITER_ENTRY_COLUMNS,
  EMPTY_ENTRY_DRAFT,
  SUBMISSION_STATUS_OPTIONS,
  submissionStatusLabel,
} from "../../lib/dailyUpdateSchema";
import { addEntry, updateEntry, deleteEntry } from "../../services/dailyUpdates";
import { toast } from "sonner";

/**
 * Recruiter report — entry CRUD persists immediately via API.
 * Creates a draft on first edit via ensureDraft().
 */
const RecruiterReportForm = ({ report, canEdit, ensureDraft, onReportChange }) => {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState(EMPTY_ENTRY_DRAFT());
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState(null);
  const [busyId, setBusyId] = useState(null);

  const entries = report?.payload?.entries ?? [];

  const resolveReportId = async () => {
    if (report?.reportId) return report.reportId;
    if (!ensureDraft) return null;
    const opened = await ensureDraft();
    return opened?.reportId ?? null;
  };

  const handleAdd = async () => {
    if (!canEdit) return;
    const hasAny = Object.values(draft).some(
      (v) => v !== "" && v != null
    );
    if (!hasAny) {
      toast.error("Fill at least one field before adding.");
      return;
    }
    setAdding(true);
    try {
      const reportId = await resolveReportId();
      if (!reportId) return;
      const body = {};
      for (const [k, v] of Object.entries(draft)) {
        if (v !== "" && v != null) body[k] = String(v).trim();
      }
      const { data } = await addEntry(reportId, body);
      onReportChange(data);
      setDraft(EMPTY_ENTRY_DRAFT());
      toast.success("Row saved");
    } catch (err) {
      const msg =
        err?.response?.data?.detail ||
        err?.message ||
        "Failed to add entry";
      toast.error(typeof msg === "string" ? msg : "Failed to add entry");
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (entry) => {
    setEditingId(entry.entryId);
    setEditDraft({
      jobId: entry.jobId ?? "",
      candidateName: entry.candidateName ?? "",
      jobName: entry.jobName ?? "",
      candidateContactNumber: entry.candidateContactNumber ?? "",
      candidateEmail: entry.candidateEmail ?? "",
      poc: entry.poc ?? "",
      client: entry.client ?? "",
      submissionStatus: entry.submissionStatus ?? "",
      remarks: entry.remarks ?? "",
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft(null);
  };

  const saveEdit = async () => {
    if (!editingId || !canEdit) return;
    const reportId = await resolveReportId();
    if (!reportId) return;
    setBusyId(editingId);
    try {
      const body = {};
      for (const [k, v] of Object.entries(editDraft)) {
        body[k] = v === "" ? null : String(v).trim();
      }
      const { data } = await updateEntry(reportId, editingId, body);
      onReportChange(data);
      cancelEdit();
      toast.success("Row updated");
    } catch (err) {
      const msg =
        err?.response?.data?.detail ||
        err?.message ||
        "Failed to update entry";
      toast.error(typeof msg === "string" ? msg : "Failed to update entry");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (entryId) => {
    if (!canEdit) return;
    if (!window.confirm("Delete this candidate entry?")) return;
    const reportId = await resolveReportId();
    if (!reportId) return;
    setBusyId(entryId);
    try {
      const { data } = await deleteEntry(reportId, entryId);
      onReportChange(data);
      toast.success("Row deleted");
    } catch (err) {
      const msg =
        err?.response?.data?.detail ||
        err?.message ||
        "Failed to delete entry";
      toast.error(typeof msg === "string" ? msg : "Failed to delete entry");
    } finally {
      setBusyId(null);
    }
  };

  const fieldInput = (obj, setObj, col, disabled) => {
    if (col.type === "status") {
      return (
        <select
          disabled={disabled}
          value={obj[col.key] ?? ""}
          onChange={(e) => setObj({ ...obj, [col.key]: e.target.value })}
          className="h-9 w-full min-w-[9rem] rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:border-[#14344a]/40"
        >
          <option value="">Select…</option>
          {SUBMISSION_STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      );
    }
    return (
      <input
        type={col.type === "email" ? "email" : "text"}
        disabled={disabled}
        value={obj[col.key] ?? ""}
        onChange={(e) => setObj({ ...obj, [col.key]: e.target.value })}
        className="h-9 w-full min-w-[7rem] rounded-lg border border-slate-200 bg-white px-2 text-xs outline-none focus:border-[#14344a]/40 disabled:bg-slate-50"
        placeholder={col.label}
      />
    );
  };

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 bg-[#14344a] px-4 py-3 sm:px-6">
        <h3 className="text-sm font-semibold text-white">Candidate submissions</h3>
        <p className="mt-0.5 text-xs text-slate-300">
          Each add / edit / delete saves immediately. Incomplete rows are OK until submit.
        </p>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[960px] text-left text-xs">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50 text-[10px] uppercase tracking-wide text-slate-500">
              {RECRUITER_ENTRY_COLUMNS.map((c) => (
                <th key={c.key} className="px-3 py-2.5 font-semibold">
                  {c.label}
                  {c.required ? " *" : ""}
                </th>
              ))}
              {canEdit && <th className="px-3 py-2.5 font-semibold">Actions</th>}
            </tr>
          </thead>
          <tbody>
            {entries.map((entry) => {
              const isEditing = editingId === entry.entryId;
              const busy = busyId === entry.entryId;
              return (
                <tr key={entry.entryId} className="border-b border-slate-50">
                  {RECRUITER_ENTRY_COLUMNS.map((col) => (
                    <td key={col.key} className="px-3 py-2 align-top">
                      {isEditing
                        ? fieldInput(editDraft, setEditDraft, col, busy)
                        : col.key === "submissionStatus"
                          ? submissionStatusLabel(entry[col.key])
                          : entry[col.key] || (
                              <span className="text-slate-300">—</span>
                            )}
                    </td>
                  ))}
                  {canEdit && (
                    <td className="px-3 py-2 align-top">
                      <div className="flex items-center gap-1">
                        {isEditing ? (
                          <>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={saveEdit}
                              className="rounded-lg bg-[#14344a] p-1.5 text-white hover:bg-[#0f2a3c] disabled:opacity-50"
                            >
                              {busy ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Check className="h-3.5 w-3.5" />
                              )}
                            </button>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={cancelEdit}
                              className="rounded-lg border border-slate-200 p-1.5 text-slate-600 hover:bg-slate-50"
                            >
                              <X className="h-3.5 w-3.5" />
                            </button>
                          </>
                        ) : (
                          <>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => startEdit(entry)}
                              className="rounded-lg border border-slate-200 p-1.5 text-slate-600 hover:bg-slate-50"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </button>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => remove(entry.entryId)}
                              className="rounded-lg border border-red-100 p-1.5 text-red-600 hover:bg-red-50"
                            >
                              {busy ? (
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                              ) : (
                                <Trash2 className="h-3.5 w-3.5" />
                              )}
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  )}
                </tr>
              );
            })}

            {canEdit && (
              <tr className="bg-slate-50/80">
                {RECRUITER_ENTRY_COLUMNS.map((col) => (
                  <td key={col.key} className="px-3 py-2 align-top">
                    {fieldInput(draft, setDraft, col, adding)}
                  </td>
                ))}
                <td className="px-3 py-2 align-top">
                  <button
                    type="button"
                    disabled={adding}
                    onClick={handleAdd}
                    className="inline-flex items-center gap-1 rounded-lg bg-[#14344a] px-3 py-1.5 text-xs font-semibold text-white hover:bg-[#0f2a3c] disabled:opacity-50"
                  >
                    {adding ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Plus className="h-3.5 w-3.5" />
                    )}
                    Add
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {entries.length === 0 && !canEdit && (
        <p className="px-6 py-8 text-center text-sm text-slate-500">
          No entries on this report.
        </p>
      )}
    </div>
  );
};

export default RecruiterReportForm;
