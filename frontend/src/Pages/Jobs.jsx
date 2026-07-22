import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Loader2, Plus, RefreshCw } from "lucide-react";
import { listJobs } from "../services/jobs";

const Jobs = () => {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const navigate = useNavigate();

  const fetchJobs = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await listJobs(1, 20);
      setJobs(data.jobs ?? []);
    } catch (err) {
      const status = err?.response?.status;
      // 401 and 403 are handled globally by api.js interceptor (redirect to /login)
      if (status !== 401 && status !== 403) {
        setError("Unable to load jobs. Please refresh.");
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, []);

  return (
    <div className="flex min-h-[calc(100vh-40px)] flex-col gap-4 p-3 sm:gap-6 sm:p-6">
      {/* Header */}
      <section className="rounded-2xl border border-slate-200 bg-white px-4 py-4 shadow-sm sm:rounded-3xl sm:px-8 sm:py-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-semibold text-slate-900 sm:text-3xl">
              Job Listings
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">
              Review all published job postings and click a role to see
              candidates who applied.
            </p>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={fetchJobs}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 transition hover:bg-slate-50 disabled:opacity-50"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => navigate("/jobs/create")}
              className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-[#0f2a3c]"
            >
              <Plus className="h-4 w-4" />
              Create Job
            </button>
          </div>
        </div>
      </section>

      {/* Listings */}
      <section className="flex-1 overflow-y-auto rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:rounded-3xl sm:p-8">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 sm:mb-6 sm:gap-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-slate-900 sm:text-xl">
              Job listings
            </h2>
            <p className="mt-1 text-sm text-slate-500 sm:mt-2">
              Your active job postings are shown here.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-[#14344a]/10 px-3 py-1 text-sm font-semibold text-[#14344a]">
              {jobs.length} job{jobs.length === 1 ? "" : "s"}
            </span>
            <button
              type="button"
              onClick={() => navigate("/jobs/create")}
              className="inline-flex items-center gap-1.5 rounded-xl bg-[#14344a] px-3.5 py-2 text-sm font-semibold text-white transition hover:bg-[#0f2a3c] sm:hidden"
            >
              <Plus className="h-4 w-4" />
              Add Job
            </button>
          </div>
        </div>

        {loading ? (
          <div className="flex min-h-[280px] items-center justify-center text-slate-500">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading jobs...
          </div>
        ) : error ? (
          <div className="rounded-3xl bg-rose-50 p-6 text-center text-sm text-rose-700">
            <p>{error}</p>
            <button
              type="button"
              onClick={fetchJobs}
              className="mt-4 inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2 text-sm font-medium text-white hover:bg-[#0f2a3c]"
            >
              <RefreshCw className="h-4 w-4" />
              Try again
            </button>
          </div>
        ) : jobs.length === 0 ? (
          <div className="rounded-3xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
            <p className="text-base font-semibold text-slate-800">
              No jobs yet
            </p>
            <p className="mx-auto mt-2 max-w-md text-sm text-slate-500">
              Create your first job posting to start receiving and screening
              candidates.
            </p>
            <button
              type="button"
              onClick={() => navigate("/jobs/create")}
              className="mt-6 inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-[#0f2a3c]"
            >
              <Plus className="h-4 w-4" />
              Create Job
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
            {jobs.map((job) => {
              const jobId = job.jobId ?? "";

              return (
                <button
                  key={jobId}
                  type="button"
                  onClick={() =>
                    navigate(`/jobs/${jobId}`, {
                      state: { job },
                    })
                  }
                  className="group rounded-3xl border border-slate-200 bg-slate-50 p-6 text-left transition duration-300 hover:-translate-y-1 hover:border-[#14344a]/30 hover:bg-white hover:shadow-md"
                >
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <p className="text-sm font-semibold uppercase tracking-[0.22em] text-[#14344a]">
                        {job.status || "Open role"}
                      </p>
                      <h3 className="mt-3 text-xl font-semibold text-slate-900">
                        {job.title || "Untitled role"}
                      </h3>
                    </div>
                    <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-slate-500 shadow-sm">
                      {job.location || "Remote"}
                    </span>
                  </div>

                  <p className="mt-4 line-clamp-2 text-sm leading-6 text-slate-500">
                    {job.jobId
                      ? `Job ID: ${job.jobId}`
                      : "No job ID available."}
                  </p>

                  <div className="mt-4 rounded-2xl border border-[#14344a]/15 bg-[#14344a]/5 p-3">
                    <p className="flex items-center gap-2 text-xs font-medium text-[#14344a]">
                      <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#14344a]/50 opacity-75" />
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-[#14344a]" />
                      </span>
                      {job.counts?.filtered ?? 0} candidates passed filter
                    </p>
                  </div>

                  <div className="mt-6 flex flex-wrap items-center gap-3 text-sm text-slate-500">
                    <span className="font-medium text-slate-700">
                      {job.counts?.total ?? 0} total applicants
                    </span>
                    <span className="inline-flex items-center gap-2 rounded-full bg-[#14344a]/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.2em] text-[#14344a] transition group-hover:bg-[#14344a]/15">
                      View candidates
                    </span>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
};

export default Jobs;
