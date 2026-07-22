import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  Loader2,
  Users,
  User,
  Eye,
  FileText,
  Briefcase,
  Search,
} from "lucide-react";
import { getMe } from "../services/auth";
import { listJobs } from "../services/jobs";
import { listCandidates } from "../services/candidates";
import ResumeModal from "../components/ResumeModal";
import {
  MOCK_TEAM_CANDIDATES,
  MOCK_RECRUITERS,
  mapMyCandidate,
  recruiterChipClass,
  buildMockResumeHtml,
  getMockCandidateById,
} from "../lib/centralizedCandidatesMock";
import {
  saveCentralizedListState,
  loadCentralizedListState,
  clearCentralizedListRestorePending,
} from "../lib/centralizedListState";

const TABS = [
  { id: "yours", label: "Your candidates", shortLabel: "Yours" },
  { id: "all", label: "All candidates", shortLabel: "All" },
];

const CentralizedCandidates = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const listRestoreDone = useRef(false);

  const saved = loadCentralizedListState();
  const initialFromNav = location.state ?? {};

  const [tab, setTab] = useState(
    initialFromNav.tab ?? saved?.tab ?? "yours"
  );
  const [user, setUser] = useState(null);
  const [myCandidates, setMyCandidates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState(
    initialFromNav.search ?? saved?.search ?? ""
  );
  const [recruiterFilter, setRecruiterFilter] = useState(
    initialFromNav.recruiterFilter ?? saved?.recruiterFilter ?? ""
  );
  const [resumeModal, setResumeModal] = useState(null);
  const [highlightedId, setHighlightedId] = useState(null);

  const myDisplayName =
    user?.name?.trim() ||
    localStorage.getItem("user_name")?.trim() ||
    "You";

  const loadMine = useCallback(async () => {
    setLoading(true);
    try {
      const meRes = await getMe().catch(() => null);
      const me = meRes?.data ?? meRes ?? null;
      setUser(me);

      const displayName =
        me?.name?.trim() ||
        localStorage.getItem("user_name")?.trim() ||
        "You";

      const jobsRes = await listJobs(1, 50);
      const jobs = jobsRes.data?.jobs ?? jobsRes.data?.items ?? [];
      const collected = [];

      await Promise.all(
        jobs.map(async (job) => {
          const jobId = job.jobId || job.id;
          if (!jobId) return;
          try {
            const { data } = await listCandidates(jobId, {
              view: "all",
              page: 1,
              limit: 50,
            });
            const rows = data.candidates ?? [];
            for (const c of rows) {
              collected.push(
                mapMyCandidate(c, {
                  recruiterName: displayName,
                  jobId,
                  jobTitle: job.title || job.jobTitle,
                })
              );
            }
          } catch {
            /* skip job if candidates fail */
          }
        })
      );

      collected.sort((a, b) => {
        const ta = a.createdAt ? new Date(a.createdAt).getTime() : 0;
        const tb = b.createdAt ? new Date(b.createdAt).getTime() : 0;
        return tb - ta;
      });
      setMyCandidates(collected);
    } catch {
      setMyCandidates([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadMine();
  }, [loadMine]);

  // Restore scroll / highlight after returning from a candidate profile
  useEffect(() => {
    if (loading || listRestoreDone.current) return;

    const shouldRestore =
      location.state?.restoreList ||
      location.state?.lastViewedCandidateId ||
      saved?.pendingRestore;

    if (!shouldRestore) {
      listRestoreDone.current = true;
      return;
    }

    const candidateId =
      location.state?.lastViewedCandidateId ?? saved?.candidateId ?? null;
    const scrollY =
      location.state?.scrollY ?? saved?.scrollY ?? window.scrollY ?? 0;

    listRestoreDone.current = true;

    const timer = window.setTimeout(() => {
      if (candidateId) {
        setHighlightedId(candidateId);
        const row = document.getElementById(`central-row-${candidateId}`);
        if (row) {
          row.scrollIntoView({ block: "center", behavior: "instant" });
        } else if (scrollY > 0) {
          window.scrollTo({ top: scrollY, behavior: "instant" });
        }
      } else if (scrollY > 0) {
        window.scrollTo({ top: scrollY, behavior: "instant" });
      }

      clearCentralizedListRestorePending();
      if (location.state?.restoreList || location.state?.lastViewedCandidateId) {
        navigate(location.pathname, { replace: true, state: {} });
      }

      window.setTimeout(() => setHighlightedId(null), 2500);
    }, 80);

    return () => window.clearTimeout(timer);
  }, [loading, location.state, location.pathname, navigate, saved]);

  const allCandidates = useMemo(() => {
    const mineTagged = myCandidates.map((c) => ({
      ...c,
      recruiterName: c.recruiterName || myDisplayName,
      isMine: true,
    }));
    return [...mineTagged, ...MOCK_TEAM_CANDIDATES].sort((a, b) => {
      const ta = a.createdAt ? new Date(a.createdAt).getTime() : 0;
      const tb = b.createdAt ? new Date(b.createdAt).getTime() : 0;
      return tb - ta;
    });
  }, [myCandidates, myDisplayName]);

  const baseList = tab === "yours" ? myCandidates : allCandidates;

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return baseList.filter((c) => {
      if (recruiterFilter && c.recruiterName !== recruiterFilter) return false;
      if (!q) return true;
      const hay = [
        c.name,
        c.currentRole,
        c.currentCompany,
        c.jobTitle,
        c.jobId,
        c.recruiterName,
        ...(c.skills || []),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [baseList, search, recruiterFilter]);

  const persistListState = (candidateId) => {
    const payload = {
      scrollY: window.scrollY,
      tab,
      search,
      recruiterFilter,
      candidateId: candidateId ?? null,
    };
    saveCentralizedListState(payload);
    return payload;
  };

  const openResume = (c, e) => {
    e?.stopPropagation?.();
    let mockResumeUrl = null;
    if (c.isMock) {
      const full = getMockCandidateById(c.candidateId) || c;
      const html = buildMockResumeHtml(full);
      mockResumeUrl = URL.createObjectURL(
        new Blob([html], { type: "text/html" })
      );
    }
    setResumeModal({
      candidateId: c.candidateId,
      candidateName: c.name,
      resumeStatus: c.resumeStatus,
      mockResumeUrl,
    });
  };

  const openProfile = (c) => {
    const restore = persistListState(c.candidateId);

    if (c.isMock) {
      navigate(`/centralized-candidates/${c.candidateId}`, {
        state: { centralizedRestore: restore },
      });
      return;
    }

    if (c.jobId && c.candidateId) {
      navigate(`/jobs/${c.jobId}/candidate/${c.candidateId}`, {
        state: {
          returnTo: "/centralized-candidates",
          centralizedRestore: restore,
        },
      });
      return;
    }
  };

  return (
    <div className="flex flex-col gap-4 p-3 pb-10 sm:gap-6 sm:p-6 lg:p-8">
      <section className="rounded-2xl border border-slate-200 bg-white px-4 py-5 shadow-sm sm:rounded-3xl sm:px-6 sm:py-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900 sm:text-2xl">
              Centralized Candidates
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              Your candidates come from your jobs. All candidates shows your
              profiles plus team submissions across recruiters.
            </p>
            {user?.name && (
              <p className="mt-2 text-xs text-slate-500">
                Signed in as{" "}
                <span className="font-medium text-slate-700">{user.name}</span>
              </p>
            )}
          </div>
          {tab === "all" && (
            <p className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
              Team recruiters: {MOCK_RECRUITERS.join(", ")}
            </p>
          )}
        </div>

        <div className="mt-5 flex flex-wrap gap-2 border-b border-slate-100 pb-0">
          {TABS.map((t) => {
            const active = tab === t.id;
            const count =
              t.id === "yours" ? myCandidates.length : allCandidates.length;
            return (
              <button
                key={t.id}
                type="button"
                onClick={() => {
                  setTab(t.id);
                  setRecruiterFilter("");
                }}
                className={`inline-flex items-center gap-2 border-b-2 px-3 py-2.5 text-sm font-medium transition ${
                  active
                    ? "border-[#14344a] text-[#14344a]"
                    : "border-transparent text-slate-500 hover:text-slate-800"
                }`}
              >
                {t.id === "yours" ? (
                  <User className="h-4 w-4" />
                ) : (
                  <Users className="h-4 w-4" />
                )}
                <span className="hidden sm:inline">{t.label}</span>
                <span className="sm:hidden">{t.shortLabel}</span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                    active
                      ? "bg-[#14344a]/10 text-[#14344a]"
                      : "bg-slate-100 text-slate-600"
                  }`}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>

        <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="relative max-w-md flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, role, job, recruiter…"
              className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-9 pr-3 text-sm outline-none focus:border-[#14344a]/40 focus:ring-2 focus:ring-[#14344a]/10"
            />
          </div>
          {tab === "all" && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="mr-1 text-[11px] font-medium uppercase tracking-wide text-slate-400">
                Added by
              </span>
              <button
                type="button"
                onClick={() => setRecruiterFilter("")}
                className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                  !recruiterFilter
                    ? "border-[#14344a] bg-[#14344a] text-white"
                    : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                Everyone
              </button>
              <button
                type="button"
                onClick={() => setRecruiterFilter(myDisplayName)}
                className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                  recruiterFilter === myDisplayName
                    ? "border-[#14344a] bg-[#14344a] text-white"
                    : "border-emerald-200 bg-emerald-50 text-emerald-800 hover:bg-emerald-100"
                }`}
              >
                {myDisplayName}
              </button>
              {MOCK_RECRUITERS.map((name) => (
                <button
                  key={name}
                  type="button"
                  onClick={() =>
                    setRecruiterFilter((prev) => (prev === name ? "" : name))
                  }
                  className={`rounded-full border px-2.5 py-1 text-xs font-medium ${
                    recruiterFilter === name
                      ? "border-[#14344a] bg-[#14344a] text-white"
                      : `${recruiterChipClass(name)} hover:opacity-90`
                  }`}
                >
                  {name}
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      {loading ? (
        <div className="flex h-48 items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white text-slate-600">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading candidates…
        </div>
      ) : filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-14 text-center">
          <Users className="mx-auto h-8 w-8 text-slate-300" />
          <p className="mt-3 text-sm font-semibold text-slate-800">
            No candidates here
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {tab === "yours"
              ? "Candidates from your jobs will appear here."
              : "Try clearing search or recruiter filters."}
          </p>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {filtered.map((c) => (
            <article
              key={`${c.candidateId}-${c.recruiterName}`}
              id={`central-row-${c.candidateId}`}
              onClick={() => openProfile(c)}
              className={`cursor-pointer rounded-2xl border bg-white p-4 shadow-sm transition hover:border-slate-300 sm:p-5 ${
                highlightedId === c.candidateId
                  ? "border-[#14344a] ring-2 ring-[#14344a]/30"
                  : "border-slate-200"
              }`}
            >
              <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="text-base font-semibold text-slate-900">
                      {c.name}
                    </h2>
                    <span
                      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${recruiterChipClass(
                        c.isMine ? "You" : c.recruiterName
                      )}`}
                      title="Added by"
                    >
                      {c.isMine ? `You · ${c.recruiterName}` : c.recruiterName}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-slate-600">
                    {c.currentRole}
                    {c.currentCompany ? ` · ${c.currentCompany}` : ""}
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-500">
                    {c.experienceYears != null && (
                      <span className="rounded-full border border-slate-100 bg-slate-50 px-2.5 py-1">
                        {c.experienceYears} yrs
                      </span>
                    )}
                    {c.noticePeriodDays != null && (
                      <span className="rounded-full border border-slate-100 bg-slate-50 px-2.5 py-1">
                        Notice {c.noticePeriodDays}d
                      </span>
                    )}
                    {c.currentCtc != null && (
                      <span className="rounded-full border border-slate-100 bg-slate-50 px-2.5 py-1">
                        CTC {c.currentCtc} LPA
                      </span>
                    )}
                    {c.expectedCtc != null && (
                      <span className="rounded-full border border-slate-100 bg-slate-50 px-2.5 py-1">
                        Exp {c.expectedCtc} LPA
                      </span>
                    )}
                    {c.atsScore != null && (
                      <span className="rounded-full border border-emerald-100 bg-emerald-50 px-2.5 py-1 font-medium text-emerald-800">
                        ATS {Math.round(c.atsScore)}
                      </span>
                    )}
                  </div>
                  {(c.jobId || c.jobTitle) && (
                    <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500">
                      <Briefcase className="h-3.5 w-3.5" />
                      {c.jobId}
                      {c.jobTitle ? ` · ${c.jobTitle}` : ""}
                    </p>
                  )}
                </div>

                <div
                  className="flex shrink-0 flex-wrap gap-2"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    onClick={() => openProfile(c)}
                    className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50"
                  >
                    <Eye className="h-3.5 w-3.5" />
                    Preview
                  </button>
                  <button
                    type="button"
                    disabled={c.resumeStatus !== "completed" && !c.resumeUrl}
                    onClick={(e) => openResume(c, e)}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-[#14344a] px-3 py-2 text-xs font-semibold text-white hover:bg-[#0f2a3c] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <FileText className="h-3.5 w-3.5" />
                    View Resume
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}

      {resumeModal && (
        <ResumeModal
          isOpen
          onClose={() => setResumeModal(null)}
          candidateId={resumeModal.candidateId}
          candidateName={resumeModal.candidateName}
          resumeStatus={resumeModal.resumeStatus}
          mockResumeUrl={resumeModal.mockResumeUrl}
        />
      )}
    </div>
  );
};

export default CentralizedCandidates;
