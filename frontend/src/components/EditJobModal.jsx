import { useMemo, useState } from "react";
import { toast } from "sonner";
import { AlertTriangle, Archive, Loader2, Trash2, X } from "lucide-react";
import { deleteJob, updateJob } from "../services/jobs";
import { calculateAts } from "../services/ats";
import { normalizeAtsStatus } from "../lib/atsHelpers";

const EMPLOYMENT_TYPES = [
  "Full-time",
  "Contract",
  "Contract to Hire",
  "Internship",
];

const PRIORITIES = ["Low", "Medium", "High"];

const JD_FIELDS = ["title", "description", "employmentType"];

/**
 * @param {{
 *   job: object,
 *   onClose: () => void,
 *   onSaved: () => void,
 *   onDeleted?: () => void,
 *   atsStatus?: object,
 * }} props
 */
const EditJobModal = ({ job, onClose, onSaved, onDeleted, atsStatus }) => {
  const [status, setStatus] = useState(job?.status ?? "active");
  const [title, setTitle] = useState(job?.title ?? "");
  const [description, setDescription] = useState(job?.description ?? "");
  const [location, setLocation] = useState(job?.location ?? "");
  const [employmentType, setEmploymentType] = useState(
    job?.employmentType ?? "Full-time"
  );
  const [priority, setPriority] = useState(job?.priority ?? "Medium");
  const [experience, setExperience] = useState(job?.experience ?? "");
  const [maxNoticePeriodDays, setMaxNoticePeriodDays] = useState(
    job?.filters?.maxNoticePeriodDays ?? ""
  );
  const [isArchived, setIsArchived] = useState(Boolean(job?.isArchived));

  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [runningAts, setRunningAts] = useState(false);
  const [confirmJdReanalysis, setConfirmJdReanalysis] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const candidateCount = job?.counts?.total ?? 0;
  const canDelete = candidateCount === 0;

  const jdFieldsChanged = useMemo(() => {
    const origDesc = job?.description ?? "";
    return (
      title.trim() !== (job?.title ?? "").trim() ||
      description !== origDesc ||
      employmentType !== (job?.employmentType ?? "Full-time")
    );
  }, [job, title, description, employmentType]);

  const buildPayload = () => {
    const payload = {};

    if (status !== job?.status) payload.status = status;
    if (title.trim() !== (job?.title ?? "").trim()) payload.title = title.trim();
    if (description !== (job?.description ?? "")) payload.description = description;
    if (location !== (job?.location ?? "")) payload.location = location;
    if (employmentType !== (job?.employmentType ?? "Full-time")) {
      payload.employmentType = employmentType;
    }
    if (priority !== (job?.priority ?? "Medium")) payload.priority = priority;
    if (experience !== (job?.experience ?? "")) payload.experience = experience;
    if (
      maxNoticePeriodDays !== "" &&
      Number(maxNoticePeriodDays) !== job?.filters?.maxNoticePeriodDays
    ) {
      payload.maxNoticePeriodDays = Number(maxNoticePeriodDays);
    }
    if (isArchived !== Boolean(job?.isArchived)) payload.isArchived = isArchived;

    return payload;
  };

  const persistUpdate = async (payload) => {
    setSaving(true);
    try {
      await updateJob(job.jobId, payload);
      const hadJdChange = JD_FIELDS.some((k) => k in payload);
      if (payload.isArchived === true) {
        toast.success("Job archived — hidden from the default job list.");
      } else if (payload.isArchived === false) {
        toast.success("Job restored to your job list.");
      } else if (hadJdChange) {
        toast.success(
          "Job updated. JD re-analysis started — re-run ATS when analysis completes."
        );
      } else {
        toast.success("Job updated successfully");
      }
      onSaved();
    } catch {
      // Error toast from api interceptor
    } finally {
      setSaving(false);
      setConfirmJdReanalysis(false);
    }
  };

  const handleSave = async () => {
    if (maxNoticePeriodDays !== "" && Number(maxNoticePeriodDays) < 0) {
      toast.error("Max notice period must be zero or a positive number.");
      return;
    }

    const payload = buildPayload();
    if (Object.keys(payload).length === 0) {
      toast.info("No changes to save.");
      return;
    }

    const jdInPayload = JD_FIELDS.some((k) => k in payload);
    if (jdInPayload && !confirmJdReanalysis) {
      setConfirmJdReanalysis(true);
      return;
    }

    await persistUpdate(payload);
  };

  const handleDelete = async () => {
    if (!canDelete) {
      toast.error(
        "This job has candidates linked. Archive it instead of deleting."
      );
      return;
    }
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }

    setDeleting(true);
    try {
      await deleteJob(job.jobId);
      toast.success("Job permanently deleted.");
      onDeleted?.();
      onClose();
    } catch (err) {
      if (err?.response?.status === 409) {
        toast.error(
          typeof err?.response?.data?.detail === "string"
            ? err.response.data.detail
            : "Cannot delete — candidates exist. Archive the job instead."
        );
        setConfirmDelete(false);
      }
    } finally {
      setDeleting(false);
    }
  };

  const inputCls =
    "h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm text-slate-700 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100";

  const normalizedAtsStatus = normalizeAtsStatus(atsStatus);
  const canRunAtsNow =
    !isArchived &&
    normalizedAtsStatus?.status === "completed" &&
    normalizedAtsStatus?.isStale === true;

  const handleRunAts = async () => {
    setRunningAts(true);
    try {
      await calculateAts(job.jobId);
      toast.success("ATS scoring triggered — processing in background.");
    } catch (err) {
      console.error("Failed to trigger ATS from EditJobModal:", err);
      const detail = err?.response?.data?.detail;
      if (typeof detail === "string") {
        toast.error(detail);
      } else {
        toast.error("Failed to trigger ATS scoring.");
      }
    } finally {
      setRunningAts(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[90vh] w-full max-w-2xl flex-col rounded-3xl border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="shrink-0 border-b border-slate-100 px-8 py-6">
          <button
            type="button"
            onClick={onClose}
            className="absolute right-5 top-5 rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-5 w-5" />
          </button>
          <h2 className="text-xl font-semibold text-slate-900">Edit Job</h2>
          <p className="mt-1 text-sm text-slate-500">
            <span className="font-mono font-medium text-slate-700">
              {job?.jobId}
            </span>
            {" · "}
            {job?.title}
          </p>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-8 py-6">
          {Boolean(job?.isArchived) && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
              <Archive className="mt-0.5 h-4 w-4 shrink-0" />
              <p>
                This job is archived — it is hidden from the default job list and
                ATS scoring is disabled. Uncheck &quot;Archive job&quot; below to restore.
              </p>
            </div>
          )}

          {jdFieldsChanged && !confirmJdReanalysis && (
            <div className="flex items-start gap-3 rounded-xl border border-[#14344a]/25 bg-[#14344a]/5 px-4 py-3 text-sm text-[#14344a]">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <p>
                Changes to title, description, or employment type will trigger JD
                re-analysis. Existing ATS scores become stale until you run Calculate
                ATS again.
              </p>
            </div>
          )}

          {confirmJdReanalysis && (
            <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-4">
              <p className="text-sm font-medium text-amber-900">
                Confirm JD re-analysis
              </p>
              <p className="mt-1 text-sm text-amber-800">
                Saving will reset JD analysis and invalidate current ATS scores.
                You will need to run ATS scoring again after analysis completes.
              </p>
              <div className="mt-3 flex gap-2">
                <button
                  type="button"
                  onClick={() => setConfirmJdReanalysis(false)}
                  className="rounded-lg border border-amber-200 bg-white px-3 py-1.5 text-xs font-medium text-amber-900 hover:bg-amber-100"
                >
                  Go back
                </button>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => persistUpdate(buildPayload())}
                  className="rounded-lg bg-amber-700 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-800 disabled:opacity-60"
                >
                  {saving ? "Saving…" : "Confirm & save"}
                </button>
              </div>
            </div>
          )}

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Status & screening
            </h3>
            <div className="mt-3 grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="mb-1.5 block text-sm font-medium text-slate-700">
                  Status
                </label>
                <select
                  value={status}
                  onChange={(e) => setStatus(e.target.value)}
                  className={inputCls}
                >
                  <option value="active">Active</option>
                  <option value="paused">Paused</option>
                  <option value="closed">Closed</option>
                </select>
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-slate-700">
                  Max notice period (days)
                </label>
                <input
                  type="number"
                  min="0"
                  value={maxNoticePeriodDays}
                  onChange={(e) => setMaxNoticePeriodDays(e.target.value)}
                  className={inputCls}
                />
              </div>
            </div>
          </section>

          <section>
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Job details
              <span className="ml-2 font-normal normal-case text-[#14344a]">
                (JD-affecting)
              </span>
            </h3>
            <div className="mt-3 space-y-4">
              <div>
                <label className="mb-1.5 block text-sm font-medium text-slate-700">
                  Title
                </label>
                <input
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className={inputCls}
                />
              </div>
              <div>
                <label className="mb-1.5 block text-sm font-medium text-slate-700">
                  Description
                </label>
                <textarea
                  rows={4}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Clear to remove description (no JD re-analysis enqueued)"
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-700 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                />
              </div>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700">
                    Employment type
                  </label>
                  <select
                    value={employmentType}
                    onChange={(e) => setEmploymentType(e.target.value)}
                    className={inputCls}
                  >
                    {EMPLOYMENT_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700">
                    Location
                  </label>
                  <input
                    type="text"
                    value={location}
                    onChange={(e) => setLocation(e.target.value)}
                    placeholder="e.g. Bangalore"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-slate-700">
                    Experience
                  </label>
                  <input
                    type="text"
                    value={experience}
                    onChange={(e) => setExperience(e.target.value)}
                    placeholder="e.g. 3+ years"
                    className={inputCls}
                  />
                </div>
                <div>
                  <label className="mb-2 block text-sm font-medium text-slate-700">
                    Priority
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {PRIORITIES.map((p) => (
                      <button
                        key={p}
                        type="button"
                        onClick={() => setPriority(p)}
                        className={`rounded-full border px-3 py-1 text-xs font-medium ${
                          priority === p
                            ? "border-blue-200 bg-blue-100 text-blue-700"
                            : "border-slate-200 bg-white text-slate-500"
                        }`}
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-slate-200 bg-slate-50 p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Archive
            </h3>
            <label className="mt-3 flex cursor-pointer items-start gap-3">
              <input
                type="checkbox"
                checked={isArchived}
                onChange={(e) => setIsArchived(e.target.checked)}
                className="mt-1 h-4 w-4 rounded border-slate-300"
              />
              <span className="text-sm text-slate-700">
                <span className="font-medium">Archive this job</span>
                <span className="mt-0.5 block text-xs text-slate-500">
                  Hide from the default job list. Use when the job code was wrong
                  but candidates already exist. Archived jobs cannot run ATS.
                </span>
              </span>
            </label>
          </section>

          <section className="rounded-xl border border-rose-200 bg-rose-50/50 p-4">
            <h3 className="text-xs font-semibold uppercase tracking-wide text-rose-600">
              Delete permanently
            </h3>
            {canDelete ? (
              <>
                <p className="mt-2 text-sm text-slate-600">
                  Only available when no candidates are linked — for jobs created
                  with the wrong code before any applicants arrive.
                </p>
                {confirmDelete && (
                  <p className="mt-2 text-sm font-medium text-rose-700">
                    This cannot be undone. Delete job {job?.jobId}?
                  </p>
                )}
                <button
                  type="button"
                  disabled={deleting || saving}
                  onClick={handleDelete}
                  className="mt-3 inline-flex items-center gap-2 rounded-lg border border-rose-300 bg-white px-4 py-2 text-sm font-medium text-rose-700 hover:bg-rose-100 disabled:opacity-60"
                >
                  {deleting ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                  {confirmDelete ? "Confirm delete" : "Delete job"}
                </button>
                {confirmDelete && (
                  <button
                    type="button"
                    onClick={() => setConfirmDelete(false)}
                    className="ml-2 mt-3 text-xs text-slate-500 underline"
                  >
                    Cancel
                  </button>
                )}
              </>
            ) : (
              <p className="mt-2 text-sm text-slate-600">
                {candidateCount} candidate{candidateCount === 1 ? "" : "s"}{" "}
                linked — deletion is blocked. Archive the job instead.
              </p>
            )}
          </section>
        </div>

        <div className="shrink-0 flex flex-col gap-3 border-t border-slate-100 px-8 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg border border-slate-300 bg-white px-5 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
            >
              Cancel
            </button>
            {canRunAtsNow && (
              <button
                type="button"
                disabled={runningAts}
                onClick={handleRunAts}
                className="rounded-lg border border-emerald-300 bg-white px-5 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-60"
              >
                {runningAts ? "Triggering…" : "Run ATS"}
              </button>
            )}
          </div>
          <button
            type="button"
            disabled={saving || deleting || confirmJdReanalysis}
            onClick={handleSave}
            className="rounded-lg bg-[#14344a] px-5 py-2 text-sm font-semibold text-white hover:bg-[#0f2a3c] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
};

export default EditJobModal;
