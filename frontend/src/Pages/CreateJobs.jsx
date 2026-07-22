import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  Briefcase,
  CalendarDays,
  ChevronDown,
  Inbox,
  Loader2,
  MapPin,
  Pencil,
  Search,
  Users,
  X,
} from "lucide-react";
import { createJob, listImportableJobs } from "../services/jobs";

const DESC_MAX = 10000;

function normalizeImportable(raw) {
  const list = raw?.jobs ?? raw?.items ?? (Array.isArray(raw) ? raw : []);
  return list
    .map((item) => {
      const jobId = String(
        item.jobId ?? item.job_id ?? item.jobCode ?? item.job_code ?? item.code ?? ""
      ).trim();
      const title = String(
        item.title ?? item.jobTitle ?? item.job_title ?? item.name ?? ""
      ).trim();
      const candidateCount = Number(
        item.candidateCount ?? item.candidate_count ?? item.count ?? 0
      );
      if (!jobId) return null;
      return {
        jobId,
        title: title || jobId,
        candidateCount: Number.isFinite(candidateCount) ? candidateCount : 0,
      };
    })
    .filter(Boolean);
}

const CITY_OPTIONS = [
  "Bengaluru",
  "Hyderabad",
  "Chennai",
  "Mumbai",
  "Pune",
  "Delhi",
  "Noida",
  "Gurgaon",
  "Kolkata",
  "Ahmedabad",
  "Remote",
];

const SectionLabel = ({ n, children }) => (
  <div className="mb-5 flex items-center gap-2.5">
    <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[#14344a] text-xs font-bold text-white">
      {n}
    </span>
    <h2 className="text-[15px] font-semibold text-slate-900">{children}</h2>
  </div>
);

const FieldLabel = ({ children, required, badge }) => (
  <label className="mb-1.5 flex flex-wrap items-center gap-2 text-[13px] font-medium text-slate-700">
    {children}
    {required && <span className="text-rose-500">*</span>}
    {badge}
  </label>
);

const ImportedBadge = () => (
  <span className="rounded-md bg-sky-50 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-sky-700 ring-1 ring-sky-200/80">
    Imported
  </span>
);

const JobCreate = () => {
  const navigate = useNavigate();

  const [title, setTitle] = useState("");
  const [jobId, setJobId] = useState("");
  const [jobIdError, setJobIdError] = useState("");
  const [description, setDescription] = useState("");
  const [location, setLocation] = useState("");
  const [employmentType, setEmploymentType] = useState("Full-time");
  const [priority, setPriority] = useState("Medium");
  const [experience, setExperience] = useState("");
  const [maxNoticePeriodDays, setMaxNoticePeriodDays] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [imported, setImported] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importables, setImportables] = useState([]);
  const [importLoading, setImportLoading] = useState(false);
  const [importError, setImportError] = useState("");
  const [importQuery, setImportQuery] = useState("");

  const filteredImportables = useMemo(() => {
    const q = importQuery.trim().toLowerCase();
    if (!q) return importables;
    return importables.filter(
      (j) =>
        j.jobId.toLowerCase().includes(q) ||
        j.title.toLowerCase().includes(q)
    );
  }, [importables, importQuery]);

  const loadImportables = async () => {
    setImportLoading(true);
    setImportError("");
    try {
      const { data } = await listImportableJobs();
      const rows = normalizeImportable(data);
      setImportables(rows);
      if (rows.length === 0) {
        setImportError(
          "No importable Naukri codes found. All codes may already have jobs, or no candidates are ingested yet."
        );
      }
    } catch {
      setImportables([]);
      setImportError("Could not load importable jobs. You can still create manually.");
    } finally {
      setImportLoading(false);
    }
  };

  useEffect(() => {
    loadImportables();
  }, []);

  const openImportPanel = () => {
    setImportOpen((v) => !v);
    setImportQuery("");
    if (importables.length === 0 && !importLoading) loadImportables();
  };

  const selectImportable = (row) => {
    setJobId(row.jobId);
    setTitle(row.title);
    setImported(true);
    setJobIdError("");
    setImportOpen(false);
    toast.success(`Imported ${row.jobId}`);
  };

  const createManually = () => {
    setImported(false);
    setImportOpen(false);
    setJobId("");
    setTitle("");
    setJobIdError("");
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setJobIdError("");

    if (!jobId.trim()) {
      setJobIdError("Job ID is required.");
      return;
    }
    if (!title.trim()) {
      toast.error("Job title is required.");
      return;
    }
    if (!description.trim()) {
      toast.error("Job description is required.");
      return;
    }
    if (maxNoticePeriodDays === "") {
      toast.error("Max notice period is required.");
      return;
    }

    setSubmitting(true);
    try {
      await createJob({
        title: title.trim(),
        jobId: jobId.trim(),
        description: description.trim(),
        location: location || undefined,
        employmentType,
        priority,
        experience: experience || undefined,
        maxNoticePeriodDays: Number(maxNoticePeriodDays),
      });
      toast.success("Job created successfully");
      navigate("/jobs");
    } catch (error) {
      const status = error?.response?.status;
      if (status === 409) {
        setJobIdError(`Job "${jobId}" already exists for your account.`);
      } else if (status === 422) {
        const detail = error?.response?.data?.detail;
        if (Array.isArray(detail)) {
          const fieldErr = detail.find(
            (d) =>
              d.loc?.[1] === "jobId" || d.loc?.[1] === "maxNoticePeriodDays"
          );
          setJobIdError(fieldErr?.msg ?? detail[0]?.msg ?? "Validation error");
        } else if (typeof detail === "string") {
          setJobIdError(detail);
        }
      }
    } finally {
      setSubmitting(false);
    }
  };

  const inputCls =
    "h-11 w-full rounded-xl border border-slate-200 bg-white px-3.5 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-[#14344a]/40 focus:ring-2 focus:ring-[#14344a]/10 disabled:cursor-not-allowed disabled:bg-slate-50 disabled:text-slate-600";

  return (
    <div className="flex min-h-screen flex-col bg-[#f3f5f8]">
      <div className="flex min-h-0 w-full flex-1 flex-col px-4 py-5 sm:px-6 lg:px-8 lg:py-6">
        <header className="mb-5 shrink-0 sm:mb-6">
          <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-[28px]">
            Create New Job
          </h1>
          <p className="mt-1.5 max-w-3xl text-sm text-slate-500">
            Import a Naukri job code from your candidates, or create one manually —
            then add the JD and screening settings.
          </p>
        </header>

        <form onSubmit={handleSubmit} className="flex min-h-0 flex-1 flex-col pb-24">
          <div className="grid min-h-0 flex-1 grid-cols-1 items-stretch gap-5 lg:grid-cols-12">
            {/* LEFT */}
            <div className="flex flex-col lg:col-span-4">
              <section className="flex h-full flex-col rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_3px_rgba(15,23,42,0.04)] sm:p-6">
                <SectionLabel n={1}>Job Information</SectionLabel>

                {/* Import CTA */}
                <div className="rounded-2xl border border-dashed border-slate-300 bg-gradient-to-b from-slate-50 to-white p-4">
                  <button
                    type="button"
                    onClick={openImportPanel}
                    className="flex w-full items-center justify-between gap-3 rounded-xl bg-[#14344a] px-4 py-3 text-left text-sm font-semibold text-white shadow-sm transition hover:bg-[#0f2a3c]"
                  >
                    <span className="inline-flex items-center gap-2.5">
                      <Inbox className="h-4 w-4 opacity-90" />
                      Import Existing Job
                    </span>
                    <ChevronDown
                      className={`h-4 w-4 opacity-80 transition ${importOpen ? "rotate-180" : ""}`}
                    />
                  </button>
                  <p className="mt-2.5 text-center text-[12px] text-slate-500">
                    Pull Job ID &amp; title from ingested Naukri codes
                  </p>
                  <button
                    type="button"
                    onClick={createManually}
                    className="mt-1 w-full text-center text-[12px] font-medium text-[#14344a] underline-offset-2 hover:underline"
                  >
                    or Create Manually
                  </button>
                </div>

                {/* Import picker */}
                {importOpen && (
                  <div className="mt-3 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                    <div className="flex items-center justify-between border-b border-slate-100 px-3.5 py-2.5">
                      <p className="text-xs font-semibold text-slate-700">
                        Select a Naukri job code
                      </p>
                      <button
                        type="button"
                        onClick={() => setImportOpen(false)}
                        className="rounded-lg p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                        aria-label="Close"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </div>

                    <div className="relative border-b border-slate-100 px-3 py-2.5">
                      <Search className="pointer-events-none absolute left-5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                      <input
                        type="search"
                        value={importQuery}
                        onChange={(e) => setImportQuery(e.target.value)}
                        placeholder="Search by Job ID or title…"
                        className="h-9 w-full rounded-lg border border-slate-200 bg-slate-50 pl-9 pr-3 text-sm outline-none focus:border-[#14344a]/30 focus:bg-white focus:ring-2 focus:ring-[#14344a]/10"
                      />
                    </div>

                    <div className="max-h-60 overflow-y-auto">
                      {importLoading ? (
                        <div className="flex items-center justify-center gap-2 py-10 text-sm text-slate-500">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          Loading codes…
                        </div>
                      ) : filteredImportables.length === 0 ? (
                        <p className="px-4 py-8 text-center text-xs leading-relaxed text-slate-500">
                          {importError || "No matching job codes."}
                        </p>
                      ) : (
                        <ul>
                          {filteredImportables.map((row) => {
                            const selected = imported && jobId === row.jobId;
                            return (
                              <li key={row.jobId}>
                                <button
                                  type="button"
                                  onClick={() => selectImportable(row)}
                                  className={`flex w-full items-center justify-between gap-3 border-b border-slate-50 px-3.5 py-3 text-left transition last:border-0 ${
                                    selected
                                      ? "bg-emerald-50"
                                      : "hover:bg-slate-50"
                                  }`}
                                >
                                  <div className="min-w-0">
                                    <p className="font-mono text-[13px] font-semibold text-slate-900">
                                      {row.jobId}
                                    </p>
                                    <p className="mt-0.5 truncate text-xs text-slate-500">
                                      {row.title}
                                    </p>
                                  </div>
                                  {row.candidateCount > 0 && (
                                    <span className="inline-flex shrink-0 items-center gap-1 rounded-full bg-slate-100 px-2 py-1 text-[10px] font-medium text-slate-600">
                                      <Users className="h-3 w-3" />
                                      {row.candidateCount}
                                    </span>
                                  )}
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  </div>
                )}

                {/* Fields */}
                <div className="mt-5 space-y-4">
                  <div>
                    <FieldLabel
                      required
                      badge={imported ? <ImportedBadge /> : null}
                    >
                      Job ID
                    </FieldLabel>
                    <input
                      type="text"
                      required
                      placeholder="e.g. DBA003"
                      value={jobId}
                      disabled={imported}
                      onChange={(e) => {
                        setJobId(e.target.value);
                        if (jobIdError) setJobIdError("");
                      }}
                      className={`${inputCls} font-mono tracking-wide ${
                        jobIdError
                          ? "border-rose-400 focus:border-rose-400 focus:ring-rose-100"
                          : ""
                      }`}
                    />
                    {jobIdError && (
                      <p className="mt-1.5 text-xs text-rose-500">{jobIdError}</p>
                    )}
                  </div>

                  <div>
                    <FieldLabel
                      required
                      badge={imported ? <ImportedBadge /> : null}
                    >
                      Job Title
                    </FieldLabel>
                    <div className="relative">
                      <input
                        type="text"
                        required
                        placeholder="e.g. QA Test Engineer"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        className={`${inputCls} pr-10`}
                      />
                      <Pencil className="pointer-events-none absolute right-3.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
                    </div>
                  </div>
                </div>

                {/* Priority inside same card for balance */}
                <div className="mt-auto border-t border-slate-100 pt-5">
                  <p className="mb-3 text-[13px] font-medium text-slate-700">
                    Hiring Priority
                  </p>
                  <div className="grid grid-cols-3 gap-2">
                    {["Low", "Medium", "High"].map((item) => {
                      const active = priority === item;
                      const styles = {
                        Low: active
                          ? "border-slate-400 bg-slate-50 text-slate-800 ring-1 ring-slate-300"
                          : "",
                        Medium: active
                          ? "border-amber-400 bg-amber-50 text-amber-900 ring-1 ring-amber-300"
                          : "",
                        High: active
                          ? "border-emerald-500 bg-emerald-50 text-emerald-900 ring-1 ring-emerald-400"
                          : "",
                      };
                      return (
                        <button
                          key={item}
                          type="button"
                          onClick={() => setPriority(item)}
                          className={`rounded-xl border px-3 py-2.5 text-sm font-semibold transition ${
                            active
                              ? styles[item]
                              : "border-slate-200 bg-white text-slate-500 hover:border-slate-300 hover:bg-slate-50"
                          }`}
                        >
                          {item}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </section>
            </div>

            {/* RIGHT */}
            <div className="flex min-h-0 flex-col gap-5 lg:col-span-8">
              <section className="flex min-h-0 flex-1 flex-col rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_3px_rgba(15,23,42,0.04)] sm:p-6">
                <SectionLabel n={2}>Job Description</SectionLabel>
                <textarea
                  rows={14}
                  required
                  maxLength={DESC_MAX}
                  placeholder="Paste the full job description here — responsibilities, skills, must-haves…"
                  value={description}
                  onChange={(e) =>
                    setDescription(e.target.value.slice(0, DESC_MAX))
                  }
                  className="min-h-[220px] w-full flex-1 resize-y rounded-xl border border-slate-200 bg-slate-50/50 px-4 py-3.5 text-sm leading-relaxed text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-[#14344a]/40 focus:bg-white focus:ring-2 focus:ring-[#14344a]/10 lg:min-h-[280px]"
                />
                <div className="mt-2.5 flex items-center justify-between text-[12px] text-slate-400">
                  <span>
                    {description.length.toLocaleString()} / {DESC_MAX.toLocaleString()}{" "}
                    characters
                  </span>
                  <button
                    type="button"
                    className="font-medium text-[#14344a] hover:underline"
                    onClick={async () => {
                      try {
                        const text = await navigator.clipboard.readText();
                        if (text) {
                          setDescription(text.slice(0, DESC_MAX));
                          toast.success("Pasted from clipboard");
                        }
                      } catch {
                        toast.info("Use Ctrl+V / Cmd+V to paste into the box");
                      }
                    }}
                  >
                    Paste from clipboard
                  </button>
                </div>
              </section>

              <section className="shrink-0 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-[0_1px_3px_rgba(15,23,42,0.04)] sm:p-6">
                <SectionLabel n={3}>Job Configuration</SectionLabel>
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
                  <div>
                    <FieldLabel>
                      <ListIcon />
                      Experience
                    </FieldLabel>
                    <input
                      type="text"
                      placeholder="e.g. 3+ Years"
                      value={experience}
                      onChange={(e) => setExperience(e.target.value)}
                      className={inputCls}
                    />
                  </div>

                  <div>
                    <FieldLabel>
                      <MapPin className="h-3.5 w-3.5 text-slate-400" />
                      Location
                    </FieldLabel>
                    <select
                      value={location}
                      onChange={(e) => setLocation(e.target.value)}
                      className={inputCls}
                    >
                      <option value="">Select city…</option>
                      {CITY_OPTIONS.map((city) => (
                        <option key={city} value={city}>
                          {city}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <FieldLabel>
                      <Briefcase className="h-3.5 w-3.5 text-slate-400" />
                      Employment Type
                    </FieldLabel>
                    <select
                      value={employmentType}
                      onChange={(e) => setEmploymentType(e.target.value)}
                      className={inputCls}
                    >
                      <option>Full-time</option>
                      <option>Contract</option>
                      <option>Contract to Hire</option>
                      <option>Internship</option>
                    </select>
                  </div>

                  <div>
                    <FieldLabel required>
                      <CalendarDays className="h-3.5 w-3.5 text-slate-400" />
                      Max Notice Period
                    </FieldLabel>
                    <select
                      required
                      value={maxNoticePeriodDays}
                      onChange={(e) => setMaxNoticePeriodDays(e.target.value)}
                      className={inputCls}
                    >
                      <option value="">Select notice period</option>
                      {[0, 15, 30, 45, 60, 75, 90].map((days) => (
                        <option key={days} value={days}>
                          {days} Days
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </section>
            </div>
          </div>

          {/* Footer — full width of content area */}
          <div className="fixed bottom-0 left-0 right-0 z-20 border-t border-slate-200 bg-white/95 backdrop-blur lg:left-64">
            <div className="flex w-full items-center justify-between gap-3 px-4 py-3.5 sm:px-6 lg:px-8">
              <button
                type="button"
                onClick={() => navigate("/jobs")}
                className="rounded-xl border border-slate-200 bg-white px-5 py-2.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="inline-flex min-w-[140px] items-center justify-center gap-2 rounded-xl bg-[#14344a] px-6 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-[#0f2a3c] disabled:cursor-not-allowed disabled:opacity-60"
              >
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Creating…
                  </>
                ) : (
                  "Create Job"
                )}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
};

function ListIcon() {
  return (
    <svg
      className="h-3.5 w-3.5 text-slate-400"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <line x1="8" x2="21" y1="6" y2="6" />
      <line x1="8" x2="21" y1="12" y2="12" />
      <line x1="8" x2="21" y1="18" y2="18" />
      <line x1="3" x2="3.01" y1="6" y2="6" />
      <line x1="3" x2="3.01" y1="12" y2="12" />
      <line x1="3" x2="3.01" y1="18" y2="18" />
    </svg>
  );
}

export default JobCreate;
