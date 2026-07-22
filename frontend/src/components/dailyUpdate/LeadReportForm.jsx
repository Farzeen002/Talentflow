import { useState } from "react";
import { Loader2, Plus, Trash2, Check, X, Pencil } from "lucide-react";
import { toast } from "sonner";
import {
  LEAD_METRIC_SECTIONS,
  parseMetricInput,
} from "../../lib/dailyUpdateSchema";
import {
  updateLeadMetrics,
  keyActivities,
  challengesRisks,
  planForTomorrow,
} from "../../services/dailyUpdates";

const COLLECTIONS = [
  {
    key: "keyActivities",
    title: "Key activities",
    required: true,
    api: keyActivities,
  },
  {
    key: "challengesRisks",
    title: "Challenges & risks",
    required: false,
    api: challengesRisks,
  },
  {
    key: "planForTomorrow",
    title: "Plan for tomorrow",
    required: true,
    api: planForTomorrow,
  },
];

/**
 * Lead recruiter report — metrics + text item lists, saved via API.
 * Creates a draft on first edit via ensureDraft().
 */
const LeadReportForm = ({ report, canEdit, ensureDraft, onReportChange }) => {
  const payload = report?.payload ?? {};

  const metricsFromPayload = (p) => ({
    recruitmentSummary: { ...(p?.recruitmentSummary ?? {}) },
    teamProfileReview: { ...(p?.teamProfileReview ?? {}) },
    leadRecruitmentDelivery: { ...(p?.leadRecruitmentDelivery ?? {}) },
  });

  const [metricsBusy, setMetricsBusy] = useState(false);
  const [localMetrics, setLocalMetrics] = useState(() =>
    metricsFromPayload(payload)
  );

  const syncKey = `${report?.reportId ?? "none"}:${report?.updatedAt ?? ""}`;
  const [syncedKey, setSyncedKey] = useState(syncKey);
  if (syncKey !== syncedKey) {
    setSyncedKey(syncKey);
    setLocalMetrics(metricsFromPayload(payload));
  }

  const resolveReportId = async () => {
    if (report?.reportId) return report.reportId;
    if (!ensureDraft) return null;
    const opened = await ensureDraft();
    return opened?.reportId ?? null;
  };

  const setMetric = (section, field, raw) => {
    setLocalMetrics((prev) => ({
      ...prev,
      [section]: {
        ...prev[section],
        [field]: raw,
      },
    }));
  };

  const saveMetrics = async () => {
    if (!canEdit) return;
    setMetricsBusy(true);
    try {
      const body = {};
      const sentFlat = {};
      for (const section of LEAD_METRIC_SECTIONS) {
        const patch = {};
        let any = false;
        for (const f of section.fields) {
          const raw = localMetrics[section.key]?.[f.key];
          if (raw === "" || raw == null) continue;
          const parsed = parseMetricInput(raw);
          if (parsed == null) continue;
          patch[f.key] = parsed;
          sentFlat[`${section.key}.${f.key}`] = parsed;
          any = true;
        }
        if (any) body[section.key] = patch;
      }

      if (Object.keys(body).length === 0) {
        toast.error("Enter at least one metric before saving.");
        return;
      }

      const reportId = await resolveReportId();
      if (!reportId) return;

      const { data } = await updateLeadMetrics(reportId, body);
      const nextPayload = data?.payload ?? {};
      setLocalMetrics(metricsFromPayload(nextPayload));
      onReportChange(data);

      const lost = [];
      for (const [path, sentVal] of Object.entries(sentFlat)) {
        const [section, field] = path.split(".");
        const got = nextPayload?.[section]?.[field];
        if (got !== sentVal) {
          lost.push(`${field} (sent ${sentVal}, got ${got ?? "null"})`);
        }
      }
      if (lost.length) {
        toast.error(
          `Metrics request OK, but server did not keep: ${lost.join("; ")}. Backend PATCH /lead/metrics needs a fix.`
        );
      } else {
        toast.success("Metrics saved");
      }
    } catch (err) {
      const msg =
        err?.response?.data?.detail || err?.message || "Failed to save metrics";
      toast.error(typeof msg === "string" ? msg : "Failed to save metrics");
    } finally {
      setMetricsBusy(false);
    }
  };

  const displayVal = (section, field) => {
    const v = localMetrics[section]?.[field];
    if (v === null || v === undefined) return "";
    return String(v);
  };

  return (
    <div className="space-y-5">
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Metrics</h3>
            <p className="mt-0.5 text-xs text-slate-500">
              Enter 0 if none today. Leave blank to skip a field (won&apos;t
              overwrite a previously saved value). Click Save metrics after
              editing.
            </p>
          </div>
          {canEdit && (
            <button
              type="button"
              disabled={metricsBusy}
              onClick={saveMetrics}
              className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2 text-xs font-semibold text-white hover:bg-[#0f2a3c] disabled:opacity-50"
            >
              {metricsBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              Save metrics
            </button>
          )}
        </div>

        <div className="grid gap-5 md:grid-cols-3">
          {LEAD_METRIC_SECTIONS.map((section) => (
            <div
              key={section.key}
              className="rounded-xl border border-slate-100 bg-slate-50 p-4"
            >
              <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                {section.title}
              </p>
              <div className="space-y-3">
                {section.fields.map((f) => (
                  <label key={f.key} className="block">
                    <span className="text-[11px] font-medium text-slate-600">
                      {f.label}
                    </span>
                    <input
                      type="number"
                      min={0}
                      disabled={!canEdit}
                      value={displayVal(section.key, f.key)}
                      onChange={(e) =>
                        setMetric(section.key, f.key, e.target.value)
                      }
                      className="mt-1 h-9 w-full rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-[#14344a]/40 disabled:bg-slate-100"
                      placeholder="—"
                    />
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {COLLECTIONS.map((col) => (
        <TextCollection
          key={col.key}
          title={col.title}
          required={col.required}
          items={payload[col.key] ?? []}
          api={col.api}
          reportId={report?.reportId}
          ensureDraft={ensureDraft}
          canEdit={canEdit}
          onReportChange={onReportChange}
        />
      ))}
    </div>
  );
};

const TextCollection = ({
  title,
  required,
  items,
  api,
  reportId,
  ensureDraft,
  canEdit,
  onReportChange,
}) => {
  const [text, setText] = useState("");
  const [adding, setAdding] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");
  const [busyId, setBusyId] = useState(null);

  const resolveReportId = async () => {
    if (reportId) return reportId;
    if (!ensureDraft) return null;
    const opened = await ensureDraft();
    return opened?.reportId ?? null;
  };

  const add = async () => {
    const t = text.trim();
    if (!t || !canEdit) return;
    setAdding(true);
    try {
      const id = await resolveReportId();
      if (!id) return;
      const { data } = await api.add(id, t);
      onReportChange(data);
      setText("");
      toast.success("Item added");
    } catch (err) {
      toast.error(
        typeof err?.response?.data?.detail === "string"
          ? err.response.data.detail
          : "Failed to add item"
      );
    } finally {
      setAdding(false);
    }
  };

  const saveEdit = async (itemId) => {
    const t = editText.trim();
    if (!t) return;
    setBusyId(itemId);
    try {
      const id = await resolveReportId();
      if (!id) return;
      const { data } = await api.update(id, itemId, t);
      onReportChange(data);
      setEditingId(null);
      toast.success("Item updated");
    } catch {
      toast.error("Failed to update item");
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (itemId) => {
    if (!window.confirm("Delete this item?")) return;
    setBusyId(itemId);
    try {
      const id = await resolveReportId();
      if (!id) return;
      const { data } = await api.remove(id, itemId);
      onReportChange(data);
      toast.success("Item deleted");
    } catch {
      toast.error("Failed to delete item");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6">
      <h3 className="text-sm font-semibold text-slate-900">
        {title}
        {required ? (
          <span className="ml-1 text-xs font-normal text-slate-400">
            (required on submit)
          </span>
        ) : (
          <span className="ml-1 text-xs font-normal text-slate-400">
            (optional)
          </span>
        )}
      </h3>

      <ul className="mt-4 space-y-2">
        {items.map((item) => (
          <li
            key={item.itemId}
            className="flex items-start gap-2 rounded-xl border border-slate-100 bg-slate-50 px-3 py-2.5"
          >
            {editingId === item.itemId ? (
              <>
                <input
                  value={editText}
                  onChange={(e) => setEditText(e.target.value)}
                  className="h-9 flex-1 rounded-lg border border-slate-200 bg-white px-3 text-sm outline-none focus:border-[#14344a]/40"
                />
                <button
                  type="button"
                  disabled={busyId === item.itemId}
                  onClick={() => saveEdit(item.itemId)}
                  className="rounded-lg bg-[#14344a] p-2 text-white"
                >
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => setEditingId(null)}
                  className="rounded-lg border border-slate-200 p-2"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </>
            ) : (
              <>
                <p className="flex-1 text-sm text-slate-700">{item.text}</p>
                {canEdit && (
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        setEditingId(item.itemId);
                        setEditText(item.text ?? "");
                      }}
                      className="rounded-lg border border-slate-200 p-1.5 text-slate-600 hover:bg-white"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      disabled={busyId === item.itemId}
                      onClick={() => remove(item.itemId)}
                      className="rounded-lg border border-red-100 p-1.5 text-red-600 hover:bg-red-50"
                    >
                      {busyId === item.itemId ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="h-3.5 w-3.5" />
                      )}
                    </button>
                  </div>
                )}
              </>
            )}
          </li>
        ))}
        {items.length === 0 && (
          <li className="text-sm text-slate-400">No items yet.</li>
        )}
      </ul>

      {canEdit && (
        <div className="mt-3 flex gap-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
            placeholder={`Add ${title.toLowerCase()}…`}
            className="h-10 flex-1 rounded-xl border border-slate-200 px-3 text-sm outline-none focus:border-[#14344a]/40"
          />
          <button
            type="button"
            disabled={adding || !text.trim()}
            onClick={add}
            className="inline-flex items-center gap-1 rounded-xl bg-[#14344a] px-4 text-sm font-semibold text-white hover:bg-[#0f2a3c] disabled:opacity-50"
          >
            {adding ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            Add
          </button>
        </div>
      )}
    </div>
  );
};

export default LeadReportForm;
