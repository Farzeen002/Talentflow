import { useEffect, useRef, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import {
  Loader2,
  MoreHorizontal,
  Search,
  BriefcaseBusiness,
  AlertCircle,
  LogOut,
  User,
} from "lucide-react";

import api from "../services/api";
import { logout as logoutUser } from "../services/auth";
import { listJobs, updateJob } from "../services/jobs";
import { getAtsStatus, calculateAts } from "../services/ats";
import EditJobModal from "../components/EditJobModal";
import AtsStatusBanner from "../components/AtsStatusBanner";
import {
  isAtsRunComplete,
  formatAtsProgress,
  normalizeAtsStatus,
} from "../lib/atsHelpers";
import { toast } from "sonner";
import { MOCK_CANDIDATE_COUNT } from "../lib/centralizedCandidatesMock";

/* ── Status badge styles (matches API values: active | paused | closed) ── */
const STATUS_STYLE = {
  active: "bg-emerald-100 text-emerald-700",
  paused: "bg-yellow-100 text-yellow-700",
  closed: "bg-red-100 text-red-700",
};

const getStatusStyle = (status) =>
  STATUS_STYLE[status?.toLowerCase()] ?? "bg-slate-100 text-slate-600";

/* ── Skeleton row for jobs table ── */
const SkeletonRow = () => (
  <tr className="border-b border-slate-100">
    {[...Array(8)].map((_, i) => (
      <td key={i} className="px-5 py-4">
        <div
          className="h-3 animate-pulse rounded bg-slate-200"
          style={{ width: `${50 + (i % 3) * 20}%` }}
        />
      </td>
    ))}
  </tr>
);

/* ──────────────────────────────────────────────
   Dashboard
────────────────────────────────────────────── */
const Dashboard = () => {
  const navigate = useNavigate();

  /* ── Recruiter State ── */
  const [recruiter, setRecruiter] = useState(null);
  const [loadingRecruiter, setLoadingRecruiter] = useState(true);
  const [recruiterError, setRecruiterError] = useState("");

  /* ── Jobs State ── */
  const [jobs, setJobs] = useState([]);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [jobsError, setJobsError] = useState(null);

  const [openMenu, setOpenMenu] = useState(null);
  const [openMenuUpward, setOpenMenuUpward] = useState(false);
  const [editingJob, setEditingJob] = useState(null);
  const [showArchived, setShowArchived] = useState(false);
  const [jobAtsStatuses, setJobAtsStatuses] = useState({});
  const [atsStatusLoading, setAtsStatusLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const menuRef = useRef(null);

  /* ── Fetch Recruiter Profile ── */
  const fetchRecruiterProfile = async () => {
    try {
      setLoadingRecruiter(true);

      const response = await api.get("/auth/me");

      console.log("Recruiter:", response.data);

      setRecruiter(response.data);
      setRecruiterError("");
    } catch (err) {
      console.error("Recruiter profile fetch error:", err);

      // interceptor already handles 401/403 redirects
      if (
        err?.response?.status !== 401 &&
        err?.response?.status !== 403
      ) {
        setRecruiterError(
          err?.response?.data?.detail ||
          "Failed to load recruiter profile."
        );
      }
    } finally {
      setLoadingRecruiter(false);
    }
  };

  /* ── Fetch jobs from real API ── */
  const fetchJobs = useCallback(async () => {
    setLoadingJobs(true);
    setJobsError(null);

    try {
      const { data } = await listJobs(1, 50, {
        includeArchived: showArchived,
      });

      console.log("RAW RESPONSE:");
      console.log("json:", JSON.stringify(data, null, 2));

      console.log("SHOW ARCHIVED:", showArchived);
      console.log("JOBS FROM API:", data.jobs);

      setJobs(data.jobs ?? []);
    } catch (err) {
      const status = err?.response?.status;

      if (status !== 401 && status !== 403) {
        setJobsError("Failed to load jobs. Please try again.");
      }
    } finally {
      setLoadingJobs(false);
    }
  }, [showArchived]);

  /* Fetch ATS status per job for dashboard columns */
  useEffect(() => {
    const visible = jobs.filter((j) =>
      showArchived ? j.isArchived : !j.isArchived
    );
    if (!visible.length) {
      setJobAtsStatuses({});
      return;
    }

    let cancelled = false;
    (async () => {
      setAtsStatusLoading(true);
      const pairs = await Promise.all(
        visible.map(async (job) => {
          try {
            const { data } = await getAtsStatus(job.jobId);
            return [job.jobId, normalizeAtsStatus(data)];
          } catch {
            return [job.jobId, { status: "idle" }];
          }
        })
      );
      if (!cancelled) {
        setJobAtsStatuses(Object.fromEntries(pairs));
        setAtsStatusLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [jobs, showArchived]);

  useEffect(() => {
    fetchRecruiterProfile();
    fetchJobs();
  }, [fetchJobs]);

  const displayedJobs = jobs.filter((j) => {
    const inArchiveView = showArchived
      ? Boolean(j.isArchived)
      : !j.isArchived;
    if (!inArchiveView) return false;

    const q = searchQuery.trim().toLowerCase();
    if (!q) return true;

    const haystack = [
      j.title,
      j.jobId,
      j.location,
      j.status,
      j.priority,
      j.employmentType,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    return haystack.includes(q);
  });

  const handleRunAts = async (jobId, e) => {
    e?.stopPropagation?.();
    try {
      await calculateAts(jobId);
      toast.success("ATS scoring started — open the job to track progress.");
      const { data } = await getAtsStatus(jobId);
      setJobAtsStatuses((prev) => ({
        ...prev,
        [jobId]: normalizeAtsStatus(data),
      }));
    } catch (err) {
      if (err?.response?.status === 409) {
        toast.info("ATS scoring is already in progress for this job.");
      }
    }
  };

  /* ── Click-outside to close dropdown menu ── */
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setOpenMenu(null);
      }
    };

    document.addEventListener("mousedown", handleClickOutside);

    return () =>
      document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleMenuToggle = (event, jobId) => {
    if (openMenu === jobId) {
      setOpenMenu(null);
      return;
    }

    const rect = event.currentTarget.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;

    setOpenMenuUpward(spaceBelow < 380 && rect.top > 380);
    setOpenMenu(jobId);
  };

  /* ── Logout ── */
  const handleLogout = () => {
    localStorage.removeItem("auth_token");
    logoutUser();
    window.location.href = "/login";
  };

  const handleArchiveToggle = async (job) => {
    setOpenMenu(null);
    const next = !job.isArchived;
    try {
      await updateJob(job.jobId, { isArchived: next });
      toast.success(
        next ? "Job archived — hidden from default list" : "Job restored"
      );
      fetchJobs();
    } catch {
      fetchJobs();
    }
  };

  /* ── PATCH job status ── */
  const handleStatusChange = async (jobId, newStatus) => {
    setOpenMenu(null);

    setJobs((prev) =>
      prev.map((j) =>
        j.jobId === jobId ? { ...j, status: newStatus } : j
      )
    );

    try {
      await updateJob(jobId, { status: newStatus });

      toast.success(`Job marked as ${newStatus}`);
    } catch {
      fetchJobs();
      toast.error("Failed to update job status");
    }
  };

  /* ── Aggregate stats ── */
  const stats = (() => {
    const activeJobs = jobs.filter(
      (j) => j.status === "active" && !j.isArchived
    ).length;

    const totalCandidates = jobs.reduce(
      (s, j) => s + (j.counts?.total ?? 0),
      0
    );

    const totalFiltered = jobs.reduce(
      (s, j) => s + (j.counts?.filtered ?? 0),
      0
    );

    const atsCompleteJobs = Object.values(jobAtsStatuses).filter((s) =>
      isAtsRunComplete(s?.status)
    ).length;

    const totalAtsScored = Object.values(jobAtsStatuses).reduce(
      (sum, s) => sum + (s?.processedCandidates ?? 0),
      0
    );

    return [
      {
        label: "Active Jobs",
        value: String(activeJobs),
        sub: `${jobs.length} in pipeline`,
      },
      {
        label: "Total Candidates",
        value: totalCandidates.toLocaleString(),
        sub: "Across your jobs",
      },
      {
        label: "Filtered",
        value: totalFiltered.toLocaleString(),
        sub: "Passed screening",
      },
      {
        label: "ATS Processed",
        value: atsStatusLoading ? "…" : String(totalAtsScored),
        sub: `${atsCompleteJobs} job${atsCompleteJobs === 1 ? "" : "s"} scored`,
      },
      {
        label: "Centralized Pool",
        value: (totalCandidates + MOCK_CANDIDATE_COUNT).toLocaleString(),
        sub: "Team + yours",
        to: "/centralized-candidates",
      },
    ];
  })();

  /* ── Full Page Loader ── */
  if (loadingRecruiter) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="text-center">
          <Loader2 className="mx-auto h-8 w-8 animate-spin text-[#14344a]" />
          <p className="mt-3 text-sm text-slate-500">
            Loading dashboard...
          </p>
        </div>
      </div>
    );
  }

  /* ── Recruiter Error ── */
  if (recruiterError) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <div className="text-center">
          <p className="mb-4 text-red-600">{recruiterError}</p>

          <button
            onClick={() => (window.location.href = "/login")}
            className="rounded-lg bg-slate-900 px-4 py-2 text-white hover:bg-black"
          >
            Back to Login
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#f6f7fb] lg:h-screen lg:overflow-hidden">
      {editingJob && (
        <EditJobModal
          job={editingJob}
          onClose={() => setEditingJob(null)}
          onSaved={() => {
            setEditingJob(null);
            fetchJobs();
          }}
          onDeleted={() => {
            setEditingJob(null);
            fetchJobs();
          }}
        />
      )}
      <div className="flex min-h-screen flex-col lg:h-full lg:overflow-hidden">

        {/* HEADER — desktop only; AppLayout provides mobile chrome */}
        <div className="hidden shrink-0 items-center justify-between gap-4 border-b border-slate-200 bg-white px-3 py-3 sm:px-6 lg:flex">
          <div className="relative min-w-0 flex-1 max-w-xl">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search jobs by title, ID, location…"
              className="h-10 w-full rounded-xl border border-slate-200 bg-white pl-10 pr-4 text-sm outline-none focus:border-[#14344a]/40 focus:ring-2 focus:ring-[#14344a]/10"
              aria-label="Search jobs"
            />
          </div>

          <div className="flex shrink-0 items-center gap-4">
            <div className="flex items-center gap-3 border-l border-slate-200 pl-4">
              <div
                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[#14344a] text-white shadow-sm"
                aria-hidden
              >
                <User className="h-4 w-4" strokeWidth={2} />
              </div>

              <div className="hidden sm:block">
                <p className="text-sm font-semibold text-slate-900">
                  {recruiter?.name || "Recruiter"}
                </p>
                <p className="text-xs text-slate-500">{recruiter?.email}</p>
              </div>
            </div>

            <button
              onClick={handleLogout}
              className="flex items-center gap-2 rounded-xl bg-red-50 px-4 py-2 text-sm font-medium text-red-600 transition hover:bg-red-100"
            >
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">Logout</span>
            </button>
          </div>
        </div>

        {/* BODY */}
        <div className="flex flex-1 flex-col px-3 pb-4 pt-3 sm:px-6 sm:pt-4 lg:overflow-hidden">

          {/* TOP ROW */}
          <div className="shrink-0">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
              <div>
                <h2 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
                  Dashboard
                </h2>
                <p className="mt-1 text-sm text-slate-500">
                  Manage hiring pipelines and ATS workflows.
                </p>
              </div>

              <div className="flex w-full flex-col gap-2 sm:flex-row sm:items-center lg:w-auto">
                <div className="relative w-full lg:hidden">
                  <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
                  <input
                    type="search"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search jobs by title, ID, location…"
                    className="h-10 w-full rounded-xl border border-slate-200 bg-white pl-10 pr-4 text-sm outline-none focus:border-[#14344a]/40 focus:ring-2 focus:ring-[#14344a]/10"
                    aria-label="Search jobs"
                  />
                </div>
                <button
                  onClick={() => navigate("/jobs/create")}
                  className="flex h-10 shrink-0 items-center justify-center gap-2 rounded-xl bg-[#14344a] px-4 text-sm font-medium text-white transition hover:bg-[#0f2a3c]"
                >
                  + Create Job
                </button>
              </div>
            </div>

            {/* KPI strip — single panel, hairline dividers */}
            <div className="mt-5 overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="grid grid-cols-2 divide-x divide-y divide-slate-100 sm:grid-cols-3 xl:grid-cols-5 xl:divide-y-0">
                {stats.map((item) => (
                  <div
                    key={item.label}
                    role={item.to ? "button" : undefined}
                    tabIndex={item.to ? 0 : undefined}
                    onClick={item.to ? () => navigate(item.to) : undefined}
                    onKeyDown={
                      item.to
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              navigate(item.to);
                            }
                          }
                        : undefined
                    }
                    className={`group px-4 py-3.5 sm:px-5 sm:py-4 ${
                      item.to
                        ? "cursor-pointer transition-colors hover:bg-[#f0f4f7]"
                        : ""
                    }`}
                  >
                    <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-400">
                      {item.label}
                    </p>
                    <p className="mt-2 text-[1.65rem] font-semibold leading-none tracking-tight text-[#14344a] tabular-nums sm:text-[1.85rem]">
                      {item.value}
                    </p>
                    <p className="mt-2 flex items-center gap-1 text-xs text-slate-500">
                      <span>{item.sub}</span>
                      {item.to && (
                        <span className="text-[#14344a]/80 opacity-0 transition-opacity group-hover:opacity-100">
                          →
                        </span>
                      )}
                    </p>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* JOBS TABLE */}
          <div
            className={`mt-4 flex-1 rounded-3xl border border-slate-200 bg-white shadow-sm ${openMenu ? "overflow-visible" : "overflow-hidden"
              }`}
          >
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 px-3 py-3 sm:px-5 sm:py-4">
              <div>
                <h3 className="text-lg font-semibold text-slate-900">
                  {showArchived ? "Archived jobs" : "Active hiring pipelines"}
                </h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  {showArchived
                    ? "Restore a job to show it in your main list again"
                    : "ATS status reflects the latest scoring run per job"}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowArchived((v) => !v)}
                className={`rounded-xl border px-4 py-2 text-sm font-medium transition ${showArchived
                  ? "border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100"
                  : "border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                  }`}
              >
                {showArchived ? "← Back to active jobs" : "Show archived jobs"}
              </button>
            </div>

            {loadingJobs ? (
              <div className="h-[calc(100%-65px)] overflow-auto">
                <table className="min-w-full">
                  <tbody>
                    {[...Array(5)].map((_, i) => (
                      <SkeletonRow key={i} />
                    ))}
                  </tbody>
                </table>
              </div>
            ) : jobsError ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
                <AlertCircle className="h-8 w-8 text-rose-400" />

                <p className="text-sm text-rose-600">{jobsError}</p>

                <button
                  onClick={fetchJobs}
                  className="rounded-lg border border-slate-300 px-4 py-2 text-xs text-slate-600 hover:bg-slate-50"
                >
                  Retry
                </button>
              </div>
            ) : displayedJobs.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
                <BriefcaseBusiness className="h-10 w-10 text-slate-300" />

                <p className="text-sm text-slate-500">
                  {searchQuery.trim()
                    ? `No jobs match “${searchQuery.trim()}”.`
                    : showArchived
                      ? "No archived jobs."
                      : "No active jobs yet."}
                </p>

                {searchQuery.trim() ? (
                  <button
                    type="button"
                    onClick={() => setSearchQuery("")}
                    className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                  >
                    Clear search
                  </button>
                ) : (
                  !showArchived && (
                    <button
                      onClick={() => navigate("/jobs/create")}
                      className="rounded-xl bg-[#14344a] px-4 py-2 text-sm font-medium text-white hover:bg-[#0f2a3c]"
                    >
                      Create your first job
                    </button>
                  )
                )}
              </div>
            ) : (
              <>
              {/* Mobile job cards */}
              <div className="space-y-3 p-3 md:hidden">
                {displayedJobs.map((job) => {
                  const jobAts = jobAtsStatuses[job.jobId];
                  return (
                    <div
                      key={job.jobId}
                      className={`rounded-2xl border border-slate-200 bg-white p-4 shadow-sm ${
                        job.isArchived ? "bg-amber-50/40" : ""
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-semibold text-slate-900">
                            {job.title}
                          </p>
                          <p className="mt-0.5 font-mono text-xs text-slate-500">
                            {job.jobId?.toUpperCase() ?? "—"}
                          </p>
                        </div>
                        <span
                          className={`shrink-0 rounded-full px-2.5 py-0.5 text-[10px] font-medium ${getStatusStyle(
                            job.status
                          )}`}
                        >
                          {job.status}
                        </span>
                      </div>

                      <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-slate-600">
                        <div className="rounded-xl bg-slate-50 px-2.5 py-2">
                          <p className="text-slate-400">Total</p>
                          <p className="mt-0.5 font-semibold text-slate-800">
                            {job.counts?.total ?? "—"}
                          </p>
                        </div>
                        <div className="rounded-xl bg-green-50 px-2.5 py-2">
                          <p className="text-green-600/80">Filtered</p>
                          <p className="mt-0.5 font-semibold text-green-700">
                            {job.counts?.filtered ?? "—"}
                          </p>
                        </div>
                      </div>

                      <div className="mt-3">
                        <AtsStatusBanner
                          atsStatus={jobAts}
                          loading={atsStatusLoading && !jobAts}
                          compact
                        />
                      </div>

                      <div className="mt-3 grid grid-cols-2 gap-2">
                        <button
                          type="button"
                          onClick={() => navigate(`/jobs/${job.jobId}`)}
                          className="rounded-xl bg-[#14344a] px-3 py-2.5 text-xs font-medium text-white hover:bg-[#0f2a3c]"
                        >
                          Candidates
                        </button>
                        {showArchived ? (
                          <button
                            type="button"
                            onClick={() => handleArchiveToggle(job)}
                            className="rounded-xl border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs font-medium text-amber-900"
                          >
                            Unarchive
                          </button>
                        ) : (
                          <button
                            type="button"
                            disabled={job.isArchived}
                            onClick={(e) => handleRunAts(job.jobId, e)}
                            className="rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-xs font-medium text-slate-700 disabled:opacity-50"
                          >
                            Run ATS
                          </button>
                        )}
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setEditingJob(job);
                          setOpenMenu(null);
                        }}
                        className="mt-2 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs font-medium text-slate-600 hover:bg-slate-50"
                      >
                        Edit job
                      </button>
                    </div>
                  );
                })}
              </div>

              {/* Desktop table */}
              <div
                className={`hidden h-[calc(100%-65px)] md:block ${openMenu ? "overflow-visible" : "overflow-auto"
                  }`}
              >
                <table className="min-w-full">
                  <thead className="sticky top-0 z-10 bg-slate-50">
                    <tr className="border-b border-slate-200 text-left">
                      {[
                        "Job Title",
                        "Job ID",
                        "Total Candidates",
                        "Filtered Candidates",
                        "ATS status",
                        "Status",
                        "Created",
                        "Actions",
                      ].map((h) => (
                        <th
                          key={h}
                          className="px-5 py-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500"
                        >
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>

                  <tbody>
                    {displayedJobs.map((job) => {
                      const jobAts = jobAtsStatuses[job.jobId];
                      return (
                        <tr
                          key={job.jobId}
                          className={`border-b border-slate-100 transition hover:bg-slate-50 ${job.isArchived ? "bg-amber-50/40" : ""
                            }`}
                        >
                          <td className="px-5 py-4 text-sm font-medium text-slate-800">
                            {job.title}
                            {job.isArchived && (
                              <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-medium text-amber-800">
                                Archived
                              </span>
                            )}
                          </td>

                          <td className="px-5 py-4 font-mono text-sm text-slate-500">
                            {job.jobId?.toUpperCase() ?? "—"}
                          </td>

                          <td className="px-5 py-4 text-sm text-slate-700">
                            {job.counts?.total ?? (
                              <span className="text-slate-400">—</span>
                            )}
                          </td>

                          <td className="px-5 py-4 text-sm font-medium text-green-700">
                            {job.counts?.filtered ?? (
                              <span className="font-normal text-slate-400">
                                —
                              </span>
                            )}
                          </td>

                          <td className="px-5 py-4">
                            <AtsStatusBanner
                              atsStatus={jobAts}
                              loading={atsStatusLoading && !jobAts}
                              compact
                            />
                            {jobAts && formatAtsProgress(jobAts) && (
                              <p className="mt-1 text-[10px] text-slate-500">
                                {formatAtsProgress(jobAts)}
                              </p>
                            )}
                          </td>

                          <td className="px-5 py-4">
                            <span
                              className={`rounded-full px-3 py-1 text-[11px] font-medium ${getStatusStyle(
                                job.status
                              )}`}
                            >
                              {job.status}
                            </span>
                          </td>

                          <td className="px-5 py-4 text-sm text-slate-500">
                            {job.createdAt
                              ? new Date(job.createdAt).toLocaleDateString(
                                "en-IN",
                                {
                                  day: "numeric",
                                  month: "short",
                                  year: "numeric",
                                }
                              )
                              : "—"}
                          </td>

                          <td className="relative px-5 py-4">
                            <div className="flex items-center gap-2">
                              <button
                                onClick={() =>
                                  navigate(`/jobs/${job.jobId}`)
                                }
                                className="rounded-lg bg-[#14344a] px-3 py-2 text-[11px] font-medium text-white transition hover:bg-[#0f2a3c]"
                              >
                                View Candidates
                              </button>

                              {showArchived ? (
                                <button
                                  type="button"
                                  onClick={() => handleArchiveToggle(job)}
                                  className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] font-medium text-amber-900 hover:bg-amber-100"
                                >
                                  Unarchive
                                </button>
                              ) : (
                                <button
                                  type="button"
                                  disabled={job.isArchived}
                                  onClick={(e) => handleRunAts(job.jobId, e)}
                                  className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-[11px] font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  Run ATS
                                </button>
                              )}

                              <div
                                className="relative"
                                ref={openMenu === job.jobId ? menuRef : null}
                              >
                                <button
                                  onClick={(e) =>
                                    handleMenuToggle(e, job.jobId)
                                  }
                                  className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"
                                >
                                  <MoreHorizontal className="h-4 w-4" />
                                </button>

                                {openMenu === job.jobId && (
                                  <div
                                    className={`absolute right-0 z-50 max-h-[360px] w-56 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-2 shadow-2xl ${openMenuUpward
                                      ? "bottom-full mb-2"
                                      : "top-11"
                                      }`}
                                    onWheel={(e) => e.stopPropagation()}
                                  >
                                    <button
                                      onClick={() => {
                                        navigate(`/jobs/${job.jobId}`);
                                        setOpenMenu(null);
                                      }}
                                      className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100"
                                    >
                                      View Candidates
                                    </button>

                                    <button
                                      onClick={() => {
                                        setEditingJob(job);
                                        setOpenMenu(null);
                                      }}
                                      className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100"
                                    >
                                      Edit job
                                    </button>

                                    <button
                                      onClick={() => handleArchiveToggle(job)}
                                      className="w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100"
                                    >
                                      {job.isArchived ? "Restore job" : "Archive job"}
                                    </button>

                                    <div className="my-2 border-t border-slate-100" />

                                    <p className="px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                                      Change Status
                                    </p>

                                    {["active", "paused", "closed"].map((s) => (
                                      <button
                                        key={s}
                                        disabled={job.status === s}
                                        onClick={() =>
                                          handleStatusChange(job.jobId, s)
                                        }
                                        className="w-full rounded-lg px-3 py-2 text-left text-sm capitalize hover:bg-slate-100 disabled:cursor-default disabled:text-slate-400"
                                      >
                                        Mark as {s}
                                      </button>
                                    ))}
                                  </div>
                                )}
                              </div>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;