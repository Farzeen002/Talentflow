import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useNavigate, useParams, useLocation, useNavigationType } from "react-router-dom";
import { toast } from "sonner";
import {
  Loader2, ArrowLeft, CheckCircle2, X, ChevronLeft, ChevronRight,
  AlertCircle, Zap, RefreshCw, Ban, RotateCcw,
} from "lucide-react";
import { getJob } from "../services/jobs";
import {
  listCandidates,
  blacklistCandidate,
  unblacklistCandidate,
} from "../services/candidates";
import { calculateAts, rerunAts } from "../services/ats";
import ResumeModal from "../components/ResumeModal";
import AtsProgressModal from "../components/AtsProgressModal";
import AtsProgressButton from "../components/AtsProgressButton";
import AtsScoreBadge from "../components/AtsScoreBadge";
import AtsStatusBanner from "../components/AtsStatusBanner";
import EditJobModal from "../components/EditJobModal";
import { useAtsStatus } from "../lib/useAtsStatus";
import {
  candidateHasAtsScore,
  countCandidatesWithAtsScore,
  isAtsRunActive,
  jobHasAtsScores,
  normalizeAtsStatus,
} from "../lib/atsHelpers";
import {
  isShortlistedCandidate,
  loadShortlistIds,
  saveShortlistIds,
} from "../lib/shortlistStorage";
import {
  saveJobCandidatesListState,
  loadJobCandidatesListState,
  clearListRestorePending,
  hasPendingListRestore,
  restoreJobCandidatesListView,
} from "../lib/jobCandidatesListState";
import {
  isCandidateBlacklisted,
  getBlacklistReason,
  getBlacklistErrorMessage,
} from "../lib/blacklistHelpers";

const buildListKey = (tabId, view, pageNum, atsRanked, bulkFiltered) =>
  `${tabId}:${view}:${pageNum}:${atsRanked}:${bulkFiltered}`;

/* ──────────────────────────────────────────────
   Constants
────────────────────────────────────────────── */

/** Resume status → display config */
const RESUME_BADGE = {
  completed: { label: "Resume Ready", cls: "bg-green-100 text-green-700 border-green-200" },
  missing: { label: "No Resume", cls: "bg-slate-100 text-slate-500 border-slate-200" },
  pending: { label: "Pending", cls: "bg-yellow-100 text-yellow-700 border-yellow-200" },
  uploaded: { label: "Processing", cls: "bg-yellow-100 text-yellow-700 border-yellow-200" },
  processing: { label: "Extracting…", cls: "bg-blue-100 text-blue-700 border-blue-200" },
  failed: { label: "Failed", cls: "bg-red-100 text-red-700 border-red-200" },
};

/** Job status → badge style */
const STATUS_BADGE = {
  active: "bg-emerald-100 text-emerald-700",
  paused: "bg-yellow-100 text-yellow-700",
  closed: "bg-red-100 text-red-700",
};

const PAGE_LIMIT = 20;

/** Max candidates fetched in one request for ATS ranking (slice after client sort) */
const ATS_FETCH_CAP = 100;

/** Top-N options shown on the ATS Ranked tab */
const ATS_TOP_OPTIONS = [
  { label: "Top 50", value: 50 },
  { label: "Top 100", value: 100 },
  { label: "Top 200", value: 200 },
  { label: "All ranked", value: null },
];

const TABS = [
  { label: "Filtered Candidates", shortLabel: "Filtered", view: "filtered", id: "filtered" },
  { label: "All Candidates", shortLabel: "All", view: "all", id: "all" },
  { label: "ATS Ranked", shortLabel: "ATS", view: "filtered", id: "ats-ranked" },
  { label: "Shortlisted", shortLabel: "Shortlist", view: "filtered", id: "shortlisted" },
  { label: "Blacklisted", shortLabel: "Blacklist", view: "blacklisted", id: "blacklisted" },
];

/** Sort by ATS score descending; unscored candidates sink to the bottom. */
const sortByAtsScore = (list) =>
  [...list].sort((a, b) => {
    const scoreA = a.atsScore ?? -1;
    const scoreB = b.atsScore ?? -1;
    return scoreB - scoreA;
  });

/* ──────────────────────────────────────────────
   Skeleton loaders
────────────────────────────────────────────── */
const SkeletonRow = () => (
  <div className="animate-pulse rounded-3xl border border-slate-200 bg-white p-5">
    <div className="flex items-start justify-between">
      <div className="space-y-2">
        <div className="h-4 w-40 rounded bg-slate-200" />
        <div className="h-3 w-28 rounded bg-slate-100" />
      </div>
      <div className="h-6 w-20 rounded-full bg-slate-100" />
    </div>
    <div className="mt-4 grid grid-cols-4 gap-3">
      {[...Array(4)].map((_, i) => (
        <div key={i} className="h-3 rounded bg-slate-100" />
      ))}
    </div>
  </div>
);

/* ──────────────────────────────────────────────
   Screening column — per-candidate filter status
   (only meaningful for view=filtered; all candidates
    in that view passed all 4 criteria)
────────────────────────────────────────────── */
const ScreeningColumn = ({ c, maxNoticePeriodDays, view }) => {
  if (view === "all") {
    // For "all" view we don't know which criteria they failed — show raw data only
    return (
      <div className="rounded-2xl bg-slate-50 p-4">
        <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Q&A Data
        </h4>
        <div className="space-y-2 text-xs text-slate-600">
          <p>Notice: {c.noticePeriodDays != null ? `${c.noticePeriodDays}d` : "—"}</p>
          <p>Current CTC: {c.currentCtc != null ? `${c.currentCtc} LPA` : "—"}</p>
          <p>Expected CTC: {c.expectedCtc != null ? `${c.expectedCtc} LPA` : "—"}</p>
        </div>
      </div>
    );
  }

  // view=filtered → candidate passed ALL 4 screening criteria
  const maxDays = maxNoticePeriodDays ?? "—";
  const noticePart = c.noticePeriodDays != null ? ` (${c.noticePeriodDays}d)` : "";

  return (
    <div className="rounded-2xl bg-green-50 p-4">
      <h4 className="mb-3 text-xs font-semibold uppercase tracking-wide text-green-700">
        Screening Match ✓
      </h4>
      <div className="space-y-2 text-xs text-slate-700">
        <div className="flex items-center gap-1.5">
          <CheckCircle2 className="h-3.5 w-3.5 text-green-600 shrink-0" />
          <span>Notice ≤ {maxDays}d{noticePart}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <CheckCircle2 className="h-3.5 w-3.5 text-green-600 shrink-0" />
          <span>OK with Client</span>
        </div>
        <div className="flex items-center gap-1.5">
          <CheckCircle2 className="h-3.5 w-3.5 text-green-600 shrink-0" />
          <span>C2H Accepted</span>
        </div>
        <div className="flex items-center gap-1.5">
          <CheckCircle2 className="h-3.5 w-3.5 text-green-600 shrink-0" />
          <span>No PF Account</span>
        </div>
      </div>
    </div>
  );
};

/* ──────────────────────────────────────────────
   Main Page
────────────────────────────────────────────── */
const JobCandidates = () => {
  const { id } = useParams();         // jobId from URL e.g. "DBA002"
  const location = useLocation();   // for state passed via navigate()
  const navigate = useNavigate();
  const navigationType = useNavigationType();
  const listScrollRef = useRef(null);
  const listRestoreDone = useRef(false);
  const candidatesFetchGen = useRef(0);
  const [loadedListKey, setLoadedListKey] = useState("");
  const [highlightedCandidateId, setHighlightedCandidateId] = useState(null);

  /* Job state */
  const [job, setJob] = useState(null);
  const [jobLoading, setJobLoading] = useState(true);
  const [jobError, setJobError] = useState("");

  /* Candidates state */
  const [candidates, setCandidates] = useState([]);
  const [candidatesLoading, setCandidatesLoading] = useState(true);
  const [candidatesError, setCandidatesError] = useState("");

  /* Pagination — live counts from API response */
  const [page, setPage] = useState(
    location.state?.page ?? 1
  );
  const [totalCount, setTotalCount] = useState(0);  // view-specific count
  const [totalAll, setTotalAll] = useState(0);  // all candidates (view=all count)
  const [totalFiltered, setTotalFiltered] = useState(0); // filtered count
  const [totalBlacklisted, setTotalBlacklisted] = useState(0);

  const updateHistoryState = (nextState) => {
    navigate(location.pathname, {
      replace: true,
      state: {
        ...(location.state ?? {}),
        ...nextState,
      },
    });
  };
  const restoreAttempts = useRef(0);
  const locationStateRestored = useRef(false);

  const handleSetPage = (nextPage) => {
    setPage(nextPage);
    updateHistoryState({ page: nextPage, activeTabIdx });
  };

  /* UI */
  const [activeTabIdx, setActiveTabIdx] = useState(
    location.state?.activeTabIdx ?? 0
  );  // index into TABS
  const [editModalOpen, setEditModalOpen] = useState(false);
  const [resumeModalOpen, setResumeModalOpen] = useState(false);
  const [selectedResume, setSelectedResume] = useState({ candidateId: null, candidateName: "", resumeStatus: "" });
  const [sortByAts, setSortByAts] = useState(false);
  const [atsTopLimit, setAtsTopLimit] = useState(50);

  const [atsModalOpen, setAtsModalOpen] = useState(false);

  // Poll while scoring is active, or while the progress modal is open; stop when idle.
  const { atsStatus, loading: atsLoading, error: atsError, isActive, refetch: refetchAts } = useAtsStatus(
    id,
    Boolean(id),
    3000,
    5000,
    { pollWhenIdle: false, forcePoll: atsModalOpen }
  );
  const isProcessing = isActive; // backward-compat alias

  // const activeTab = TABS[activeTabIdx];
  // const isAtsRankedTab = activeTab.id === "ats-ranked";
  // const isShortlistedTab = activeTab.id === "shortlisted";
  // const isFilteredTab = activeTab.id === "filtered";
  const activeTab = TABS?.[activeTabIdx] ?? TABS[0];

  const isAtsRankedTab =
    activeTab?.id === "ats-ranked";

  const isShortlistedTab =
    activeTab?.id === "shortlisted";

  const isBlacklistedTab =
    activeTab?.id === "blacklisted";

  const isFilteredTab =
    activeTab?.id === "filtered";

  const normalizedAtsStatus = normalizeAtsStatus(atsStatus);

  const [shortlistedIds, setShortlistedIds] = useState(() =>
    loadShortlistIds(id)
  );

  useEffect(() => {
    setShortlistedIds(loadShortlistIds(id));
  }, [id]);

  useEffect(() => {
    if (
      activeTabIdx < 0 ||
      activeTabIdx >= TABS.length
    ) {
      console.warn(
        "Invalid activeTabIdx:",
        activeTabIdx
      );

      setActiveTabIdx(0);
    }
  }, [activeTabIdx]);

  // Recruiter edits JD
  // Backend marks ATS as stale
  // isStale=true
  // Show warning banner
  // Enable "Re-run ATS" button
  const isStaleAts =
    normalizedAtsStatus?.status === "completed" &&
    normalizedAtsStatus?.isStale === true;

  /* ── Fetch job detail ── */
  const fetchJob = useCallback(async () => {
    setJobLoading(true);
    setJobError("");
    try {
      const { data } = await getJob(id);
      setJob(data);
    } catch (err) {
      // 401/403 handled globally; show local error for others
      const status = err?.response?.status;
      if (status !== 401 && status !== 403) {
        setJobError(
          status === 404
            ? `Job "${id}" not found.`
            : "Failed to load job details."
        );
      }
    } finally {
      setJobLoading(false);
    }
  }, [id]);

  /* ── Fetch candidates (no caching — always fresh) ── */
  const fetchBlacklistedCount = useCallback(async () => {
    try {
      const { data } = await listCandidates(id, {
        view: "blacklisted",
        page: 1,
        limit: 1,
      });
      setTotalBlacklisted(data?.total ?? 0);
    } catch {
      // badge stays at previous value on failure
    }
  }, [id]);

  const fetchCandidates = useCallback(
    async (
      view,
      pageNum,
      { atsRanked = false, bulkFiltered = false, tabId = "filtered" } = {}
    ) => {
      const isBlacklistFetch = tabId === "blacklisted" || view === "blacklisted";
      const listKey = buildListKey(tabId, view, pageNum, atsRanked, bulkFiltered);
      const fetchGen = ++candidatesFetchGen.current;

      setCandidatesLoading(true);
      setCandidatesError("");
      try {
        const filteredCount = job?.counts?.filtered ?? ATS_FETCH_CAP;
        const useBulk = (atsRanked || bulkFiltered) && !isBlacklistFetch;
        const fetchLimit = useBulk
          ? Math.min(Math.max(filteredCount, 1), ATS_FETCH_CAP)
          : PAGE_LIMIT;

        const { data } = await listCandidates(id, {
          view: useBulk ? "filtered" : view,
          page: useBulk ? 1 : pageNum,
          limit: fetchLimit,
          sort: "created_at_desc",
        });

        if (fetchGen !== candidatesFetchGen.current) return;

        let rawCandidates = data.candidates ?? [];

        if (isBlacklistFetch) {
          // Backend filters via view=blacklisted; trust API total + rows.
          setTotalBlacklisted(data.total ?? rawCandidates.length);
          setTotalCount(data.total ?? rawCandidates.length);
        } else {
          // Defense-in-depth: drop any blacklisted rows if they leak into active views
          rawCandidates = rawCandidates.filter((c) => !isCandidateBlacklisted(c));

          setTotalAll(data.total ?? 0);
          setTotalFiltered(data.filtered ?? 0);

          setTotalCount(
            atsRanked
              ? (data.filtered ?? data.candidates?.length ?? 0)
              : view === "filtered"
                ? (data.filtered ?? 0)
                : (data.total ?? 0)
          );
        }

        setCandidates(rawCandidates);
        setLoadedListKey(listKey);
      } catch (err) {
        if (fetchGen !== candidatesFetchGen.current) return;

        const status = err?.response?.status;
        const message = err?.response?.data?.message || err?.response?.data?.error;
        if (status !== 401 && status !== 403) {
          setCandidatesError(
            status === 422
              ? `Invalid request: ${message || "Check your filters or try a smaller limit."}`
              : "Failed to load candidates."
          );
        }
      } finally {
        if (fetchGen === candidatesFetchGen.current) {
          setCandidatesLoading(false);
        }
      }
    },
    [id, job?.counts?.filtered]
  );

  /* Initial loads */
  useEffect(() => { fetchJob(); }, [fetchJob]);
  useEffect(() => { fetchBlacklistedCount(); }, [fetchBlacklistedCount]);

  const isFirstRender = useRef(true);
  const restoredNavigation = useRef(false);

  useEffect(() => {
    listRestoreDone.current = false;
  }, [id]);

  useEffect(() => {
    if (navigationType === "PUSH" && !location.state?.restoreList) {
      clearListRestorePending(id);
    }
  }, [id, navigationType, location.state?.restoreList]);

  useEffect(() => {
    if (!location.state || locationStateRestored.current) return;

    const nextPage = location.state.page;
    const nextActiveTabIdx = location.state.activeTabIdx;

    if (nextPage != null && nextPage !== page) {
      setPage(nextPage);
    }
    if (nextActiveTabIdx != null && nextActiveTabIdx !== activeTabIdx) {
      restoredNavigation.current = true;
      setActiveTabIdx(nextActiveTabIdx);
    }
    const tabId = location.state.activeTabId;
    if (tabId) {
      const idx = TABS.findIndex((t) => t.id === tabId);
      if (idx >= 0 && idx !== activeTabIdx) {
        restoredNavigation.current = true;
        setActiveTabIdx(idx);
      }
    }

    locationStateRestored.current = true;
  }, [location.state]);

  useEffect(() => {
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }

    if (restoredNavigation.current) {
      restoredNavigation.current = false;
      return;
    }

    setPage(1);
  }, [activeTabIdx]);

  const currentListKey = buildListKey(
    activeTab.id,
    activeTab.view,
    page,
    isAtsRankedTab,
    isShortlistedTab,
    isBlacklistedTab
  );
  const listDataReady = loadedListKey === currentListKey && !candidatesLoading;
  const showListSkeleton = candidatesLoading && loadedListKey !== currentListKey;
  const isInitialListLoad = !loadedListKey;

  useEffect(() => {
    fetchCandidates(activeTab.view, page, {
      atsRanked: isAtsRankedTab,
      bulkFiltered: isShortlistedTab,
      tabId: activeTab.id,
    });
  }, [
    activeTab.id,
    activeTab.view,
    page,
    fetchCandidates,
    isAtsRankedTab,
    isShortlistedTab,
    isBlacklistedTab,
  ]);

  const handleEditSaved = async () => {
    setEditModalOpen(false);

    await fetchJob();

    refetchAts(); // refresh ATS status immediately

    fetchCandidates(activeTab.view, page, {
      atsRanked: isAtsRankedTab,
      bulkFiltered: isShortlistedTab,
      tabId: activeTab.id,
    });
  };

  const handleViewResume = (candidateId, candidateName, resumeStatus) => {
    setSelectedResume({ candidateId, candidateName, resumeStatus });
    setResumeModalOpen(true);
  };

  const handleCalculateAts = async () => {
    try {
      // Let the backend enforce all pre-conditions (JD analysis status,
      // filtered candidates, etc.) — it returns 422 with a clear message.
      await calculateAts(id);
      toast.success("ATS scoring triggered — processing in background.");
      setAtsModalOpen(true);
      // Immediately fetch fresh status so the modal doesn't show stale data
      // while waiting for the next scheduled poll cycle.
      setTimeout(() => refetchAts(), 800);
    } catch (err) {
      console.error("Failed to calculate ATS:", err);
      const httpStatus = err?.response?.status;

      if (httpStatus === 409) {
        // A run is already in progress — open the modal to show live status
        toast.info("ATS scoring is already in progress. Opening status view…");
        setAtsModalOpen(true);
        setTimeout(() => refetchAts(), 300);
        return;
      }

      if (httpStatus === 422) {
        // Backend pre-condition failed (JD not analysed / no filtered candidates)
        const detail = err?.response?.data?.detail;
        toast.error(
          typeof detail === "string"
            ? detail
            : "Pre-conditions not met: ensure JD analysis is complete and candidates have passed screening."
        );
        return;
      }

      // Generic fallback — global interceptor already showed a toast,
      // but keep this for cases where it was suppressed.
      const errorMsg =
        err?.response?.data?.detail || "Failed to trigger ATS scoring.";
      if (httpStatus !== 401 && httpStatus !== 403) {
        toast.error(errorMsg);
      }
    }
  };

  const handleForceRerunAts = async () => {
    try {
      await rerunAts(id);

      toast.success(
        "Full ATS re-run triggered. Processing in background."
      );

      setAtsModalOpen(true);

      setTimeout(() => {
        refetchAts();
      }, 800);
    } catch (err) {
      console.error("Failed to trigger ATS re-run:", err);

      const msg =
        err?.response?.data?.detail ||
        "Failed to trigger ATS re-run";

      toast.error(msg);
    }
  };

  const handleAtsRerun = () => {
    fetchCandidates(activeTab.view, page, {
      atsRanked: isAtsRankedTab,
      bulkFiltered: isShortlistedTab,
      tabId: activeTab.id,
    });
  };

  const openAtsProgressModal = () => {
    setAtsModalOpen(true);
  };

  const handleToggleShortlist = (e, candidateId) => {
    e.stopPropagation();
    setShortlistedIds((prev) => {
      const next = new Set(prev);
      if (next.has(candidateId)) {
        next.delete(candidateId);
        toast.success("Removed from shortlist");
      } else {
        next.add(candidateId);
        toast.success("Added to shortlist");
      }
      saveShortlistIds(id, next);
      return next;
    });
  };

  // Auto-refresh candidate list whenever ATS run finishes (modal open or not)
  useEffect(() => {
    const normalizedAtsStatus = normalizeAtsStatus(atsStatus);
    console.log("ATS Status:", normalizedAtsStatus);
    if (
      (normalizedAtsStatus?.status === "completed" || normalizedAtsStatus?.status === "partially_failed") &&
      !isActive
    ) {
      const timer = setTimeout(() => {
        fetchCandidates(activeTab.view, page, {
          atsRanked: isAtsRankedTab,
          bulkFiltered: isShortlistedTab,
          tabId: activeTab.id,
        });
      }, 1200);
      return () => clearTimeout(timer);
    }
  }, [
    atsStatus?.status,
    isActive,
    activeTab.view,
    page,
    fetchCandidates,
    isAtsRankedTab,
    isShortlistedTab,
    isBlacklistedTab,
  ]);

  /* Pagination — ATS Ranked & Shortlisted use a single full list */
  const usesSinglePageList = isAtsRankedTab || isShortlistedTab;
  const totalPages = usesSinglePageList ? 1 : Math.ceil(totalCount / PAGE_LIMIT);
  const hasMore = !usesSinglePageList && page * PAGE_LIMIT < totalCount;
  const hasPrev = !usesSinglePageList && page > 1;

  const safeCandidates =
    Array.isArray(candidates)
      ? candidates
      : [];

  const displayedCandidates = useMemo(() => {
    if (isAtsRankedTab) {
      const scoredOnly =
        safeCandidates.filter(candidateHasAtsScore);

      const ranked =
        sortByAtsScore(scoredOnly);

      return atsTopLimit
        ? ranked.slice(0, atsTopLimit)
        : ranked;
    }

    if (isShortlistedTab) {
      return safeCandidates.filter((c) =>
        isShortlistedCandidate(c, shortlistedIds)
      );
    }

    if (sortByAts) {
      return sortByAtsScore(safeCandidates);
    }

    return safeCandidates;
  }, [
    safeCandidates,
    isAtsRankedTab,
    isShortlistedTab,
    atsTopLimit,
    shortlistedIds,
    sortByAts,
  ]);

  const shortlistedCount = shortlistedIds.size;

  /* Convenience: current maxNoticePeriodDays from job.filters */
  const maxNoticeDays = job?.filters?.maxNoticePeriodDays ?? null;
  const isJobArchived = Boolean(job?.isArchived);
  const atsScoringActive = isAtsRunActive(normalizedAtsStatus?.status);
  const hasAtsScores = jobHasAtsScores(normalizedAtsStatus, candidates);
  const scoredCandidateCount = countCandidatesWithAtsScore(candidates);
  const unscoredCandidateCount = safeCandidates.filter(
    (candidate) => !candidateHasAtsScore(candidate)
  ).length;
  const showNewCandidateAtsPrompt =
    unscoredCandidateCount > 0 && !atsScoringActive && !jobLoading;

  const newCandidateAtsHelperText =
    unscoredCandidateCount === 1
      ? "1 new candidate detected. Click to calculate the ATS score for the latest candidate."
      : `${unscoredCandidateCount} new candidates detected. Click to calculate the ATS score for the latest candidate.`;

  const handleOpenCandidate = (candidate) => {
    saveJobCandidatesListState(id, {
      scrollTop: listScrollRef.current?.scrollTop ?? 0,
      candidateId: candidate.candidateId,
      page,
      activeTabIdx,
    });

    navigate(`/jobs/${id}/candidate/${candidate.candidateId}`, {
      state: {
        candidate,
        job,
        activeTabId: activeTab.id,
        activeTabIdx,
        page,
      },
    });
  };

  const clearRestoreNavigationState = useCallback(() => {
    if (
      !location.state?.restoreList &&
      !location.state?.lastViewedCandidateId
    ) {
      return;
    }
    const {
      restoreList: _r,
      lastViewedCandidateId: _c,
      ...rest
    } = location.state ?? {};
    navigate(location.pathname, { replace: true, state: rest });
  }, [location.pathname, location.state, navigate]);

  useEffect(() => {
    if (!listDataReady || listRestoreDone.current) return;

    const pendingRestore =
      location.state?.restoreList ||
      location.state?.lastViewedCandidateId ||
      hasPendingListRestore(id);

    if (!pendingRestore) return;

    const saved = loadJobCandidatesListState(id);
    if (saved?.activeTabIdx != null && saved.activeTabIdx !== activeTabIdx) {
      clearListRestorePending(id);
      clearRestoreNavigationState();
      listRestoreDone.current = true;
      return;
    }

    const candidateId =
      location.state?.lastViewedCandidateId ?? saved?.candidateId ?? null;
    const scrollTop = saved?.scrollTop ?? 0;

    if (!candidateId && scrollTop <= 0) {
      clearListRestorePending(id);
      clearRestoreNavigationState();
      listRestoreDone.current = true;
      return;
    }

    // listRestoreDone.current = true;
    // clearListRestorePending(id);
    // clearRestoreNavigationState();

    // const timer = window.setTimeout(() => {
    //   const container = listScrollRef.current;

    //   if (candidateId) {
    //     setHighlightedCandidateId(candidateId);
    //   }

    //   restoreJobCandidatesListView(container, {
    //     scrollTop,
    //     candidateId,
    //   });

    //   if (candidateId) {
    //     window.setTimeout(
    //       () => setHighlightedCandidateId(null),
    //       4000
    //     );
    //   }
    // }, 80);

    const restoreView = () => {
      const container = listScrollRef.current;

      const row = candidateId
        ? document.getElementById(
          `candidate-row-${candidateId}`
        )
        : null;

      if (candidateId && !row) {
        let attempts = restoreAttempts.current || 0;

        if (attempts > 60) {
          listRestoreDone.current = true;
          return;
        }

        restoreAttempts.current = attempts + 1;

        requestAnimationFrame(restoreView);
        return;
      }

      if (candidateId) {
        setHighlightedCandidateId(candidateId);
      }

      restoreJobCandidatesListView(container, {
        scrollTop,
        candidateId,
      });

      listRestoreDone.current = true;
      clearListRestorePending(id);
      clearRestoreNavigationState();

      if (candidateId) {
        setTimeout(() => {
          setHighlightedCandidateId(null);
        }, 4000);
      }
    };

    restoreAttempts.current = 0;

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        restoreView();
      });
    });

    // return () => window.clearTimeout(timer);
  }, [
    listDataReady,
    id,
    activeTabIdx,
    location.state?.restoreList,
    location.state?.lastViewedCandidateId,
    clearRestoreNavigationState,
  ]);

  /** ATS Ranked: prompt only when no scores yet and not currently scoring */
  const showAtsRequiredGate =
    isAtsRankedTab && !atsScoringActive && !hasAtsScores && listDataReady;

  const showAtsProgressGate =
    isAtsRankedTab && atsScoringActive && !hasAtsScores && listDataReady;

  /** Banner reflects scores on candidates even if ats-status is still idle */
  const bannerAtsStatus =
    hasAtsScores && normalizedAtsStatus?.status === "idle"
      ? {
        ...normalizedAtsStatus,
        status: "completed",
        processedCandidates:
          scoredCandidateCount || normalizedAtsStatus?.processedCandidates || 0,
        totalCandidates:
          normalizedAtsStatus?.totalCandidates ?? job?.counts?.filtered ?? scoredCandidateCount,
      }
      : normalizedAtsStatus;

  const handleBlacklistCandidate = async (e, candidateId, candidateName) => {
    e.stopPropagation();
    const label = candidateName || "this candidate";
    const reason = window.prompt(
      `Blacklist ${label}? They will be hidden from all lists and ATS scoring.\n\nReason (optional but recommended):`,
      ""
    );
    if (reason === null) return;

    try {
      await blacklistCandidate(candidateId, { reason: reason.trim() || undefined });
      toast.success("Candidate blacklisted");
      setShortlistedIds((prev) => {
        const next = new Set(prev);
        next.delete(candidateId);
        saveShortlistIds(id, next);
        return next;
      });
      await fetchJob();
      const blacklistedTabIdx = TABS.findIndex((t) => t.id === "blacklisted");
      if (blacklistedTabIdx >= 0) {
        setActiveTabIdx(blacklistedTabIdx);
        setPage(1);
        updateHistoryState({ activeTabIdx: blacklistedTabIdx, page: 1 });
      }
      await fetchCandidates("blacklisted", 1, {
        atsRanked: false,
        bulkFiltered: false,
        tabId: "blacklisted",
      });
      await fetchBlacklistedCount();
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) {
        toast.info("Candidate is already blacklisted");
        fetchBlacklistedCount();
        return;
      }
      toast.error(getBlacklistErrorMessage(err));
    }
  };

  const handleUnblacklistCandidate = async (e, candidateId, candidateName) => {
    e.stopPropagation();
    const label = candidateName || "this candidate";
    if (!window.confirm(`Restore ${label} to active status?`)) return;

    try {
      await unblacklistCandidate(candidateId);
      toast.success("Candidate restored");
      fetchCandidates(activeTab.view, page, {
        atsRanked: isAtsRankedTab,
        bulkFiltered: isShortlistedTab,
        tabId: activeTab.id,
      });
      fetchJob();
      fetchBlacklistedCount();
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) {
        toast.info("Candidate is already active");
        fetchCandidates(activeTab.view, page, {
          atsRanked: isAtsRankedTab,
          bulkFiltered: isShortlistedTab,
          tabId: activeTab.id,
        });
        fetchBlacklistedCount();
        return;
      }
      toast.error(getBlacklistErrorMessage(err));
    }
  };

  /* ── Job loading / error screens ── */
  if (jobLoading) {
    return (
      <div className="flex h-screen items-center justify-center gap-2 text-slate-600">
        <Loader2 className="h-5 w-5 animate-spin" /> Loading job details…
      </div>
    );
  }

  if (jobError) {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-4 bg-[#f5f7fb]">
        <AlertCircle className="h-10 w-10 text-red-400" />
        <p className="text-red-600">{jobError}</p>
        <button
          onClick={() => navigate("/jobs")}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          Back to Jobs
        </button>
      </div>
    );
  }

  return (
    <>
      {editModalOpen && (
        <EditJobModal
          job={job}
          atsStatus={atsStatus}
          onClose={() => setEditModalOpen(false)}
          onSaved={handleEditSaved}
          onDeleted={() => navigate("/jobs")}
        />
      )}

      <ResumeModal
        isOpen={resumeModalOpen}
        onClose={() => setResumeModalOpen(false)}
        candidateId={selectedResume.candidateId}
        candidateName={selectedResume.candidateName}
        resumeStatus={selectedResume.resumeStatus}
      />

      <AtsProgressModal
        isOpen={atsModalOpen}
        onClose={() => setAtsModalOpen(false)}
        atsStatus={atsStatus}
        loading={atsLoading}
        error={atsError}
        jobId={id}
        onRerun={handleAtsRerun}
        onRefresh={refetchAts}
      />

      <div className="flex h-[100dvh] flex-col overflow-hidden bg-[#f5f7fb]">

        {/* ── HEADER ── */}
        <div className="shrink-0 border-b border-slate-200 bg-[#f5f7fb] px-3 py-3 sm:px-6 sm:py-4">
          <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-6">
            <div className="flex flex-col gap-4">
              {/* Title row — Edit sits with job info */}
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
                <div className="min-w-0 flex-1">
                  <button
                    onClick={() => navigate("/jobs")}
                    className="mb-3 inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 sm:mb-4 sm:px-4"
                  >
                    <ArrowLeft className="h-4 w-4" />
                    <span className="sm:hidden">Back</span>
                    <span className="hidden sm:inline">Back to Jobs</span>
                  </button>
                  <h1 className="truncate text-xl font-bold text-slate-900 sm:text-3xl">
                    {job?.title || "Job Details"}
                  </h1>
                  <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-slate-500">
                    <span className="font-mono font-medium">
                      {job?.jobId?.toUpperCase() ?? "—"}
                    </span>
                    <span>•</span>
                    <span>
                      {job?.createdAt
                        ? new Date(job.createdAt).toLocaleDateString("en-IN", {
                            day: "numeric",
                            month: "short",
                            year: "numeric",
                          })
                        : "—"}
                    </span>
                    <span>•</span>
                    <span
                      className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${
                        STATUS_BADGE[job?.status] ?? "bg-slate-100 text-slate-500"
                      }`}
                    >
                      {job?.status ?? "—"}
                    </span>
                    {isJobArchived && (
                      <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800">
                        Archived
                      </span>
                    )}
                  </div>
                </div>

                <button
                  type="button"
                  onClick={() => setEditModalOpen(true)}
                  className="shrink-0 self-start rounded-xl border border-slate-300 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 sm:mt-10"
                >
                  Edit Job
                </button>
              </div>

              {isJobArchived && (
                <p className="max-w-2xl rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800">
                  This job is archived — hidden from the default job list and ATS
                  scoring is disabled. Open Edit Job to restore it.
                </p>
              )}

              {/* ATS status + actions — one tight group */}
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:gap-4">
                <div className="min-w-0 flex-1">
                  <AtsStatusBanner
                    atsStatus={bannerAtsStatus}
                    loading={atsLoading && !atsStatus}
                    onViewDetails={openAtsProgressModal}
                  />
                </div>

                {!isJobArchived && (
                  <div className="flex w-full shrink-0 flex-col gap-2 lg:w-auto lg:max-w-xl">
                    <div className="flex flex-wrap items-center gap-2">
                      <AtsProgressButton
                        atsStatus={atsStatus}
                        loading={atsLoading && !atsStatus}
                        onClick={openAtsProgressModal}
                        className="px-4 py-2.5"
                      />

                      <button
                        onClick={handleCalculateAts}
                        disabled={isActive || jobLoading}
                        id="calculate-ats-btn"
                        className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2.5 text-sm font-medium text-white transition hover:bg-[#0f2a3c] disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {isActive ? (
                          <>
                            <Loader2 className="h-4 w-4 animate-spin" />
                            Calculating…
                          </>
                        ) : (
                          <>
                            <Zap className="h-4 w-4" />
                            {normalizedAtsStatus?.isStale
                              ? "Recalculate ATS for Updated JD"
                              : hasAtsScores
                                ? "Score Latest Candidates"
                                : "Calculate ATS Score"}
                          </>
                        )}
                      </button>

                      {hasAtsScores && (
                        <button
                          type="button"
                          onClick={handleForceRerunAts}
                          disabled={isActive || jobLoading}
                          className="inline-flex items-center gap-2 rounded-xl border border-[#14344a]/25 bg-[#14344a]/5 px-4 py-2.5 text-sm font-medium text-[#14344a] hover:bg-[#14344a]/10 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <RefreshCw className="h-4 w-4" />
                          Force Re-run ATS
                        </button>
                      )}

                      {isStaleAts && !hasAtsScores && (
                        <button
                          type="button"
                          onClick={handleForceRerunAts}
                          disabled={isActive || jobLoading}
                          className="inline-flex items-center gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                        >
                          <RefreshCw className="h-4 w-4" />
                          Re-run ATS
                        </button>
                      )}
                    </div>

                    {normalizedAtsStatus?.isStale && !isActive && (
                      <p className="text-xs text-amber-700">
                        Job Description has changed since the last ATS run.
                        Generate a new ATS analysis using the latest requirements.
                      </p>
                    )}

                    {activeTab?.id === "filtered" &&
                      showNewCandidateAtsPrompt &&
                      unscoredCandidateCount > 0 &&
                      !isActive && (
                        <p className="text-xs text-slate-600">
                          {newCandidateAtsHelperText}
                        </p>
                      )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* ── BODY ── */}
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden px-3 pb-3 sm:gap-4 sm:px-6 sm:pb-4 xl:grid xl:grid-cols-[280px_1fr] xl:gap-6">

          {/* Mobile pipeline summary (sidebar is xl+) */}
          <div className="grid shrink-0 grid-cols-3 gap-2 xl:hidden">
            <div className="rounded-2xl border border-slate-200 bg-white px-3 py-3 text-center shadow-sm">
              <p className="text-lg font-bold text-slate-900">
                {job?.counts?.total ?? totalAll}
              </p>
              <p className="mt-0.5 text-[10px] text-slate-500">Total</p>
            </div>
            <div className="rounded-2xl border border-green-200 bg-green-50 px-3 py-3 text-center shadow-sm">
              <p className="text-lg font-bold text-green-700">
                {job?.counts?.filtered ?? totalFiltered}
              </p>
              <p className="mt-0.5 text-[10px] text-green-700">Filtered</p>
            </div>
            <div className="rounded-2xl border border-red-200 bg-red-50 px-3 py-3 text-center shadow-sm">
              <p className="text-lg font-bold text-red-700">{totalBlacklisted}</p>
              <p className="mt-0.5 text-[10px] text-red-700">Blacklist</p>
            </div>
          </div>

          {/* ── LEFT SIDEBAR (desktop) ── */}
          <div className="hidden min-h-0 overflow-y-auto pr-2 xl:block">
            <div className="space-y-5 pb-6">

              {/* Job Info */}
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-500">
                  Job Information
                </h2>
                <div className="space-y-2.5 text-sm text-slate-600">
                  {[
                    ["Title", job?.title],
                    ["Job ID", job?.jobId?.toUpperCase()],
                    ["Location", job?.location],
                    ["Type", job?.employmentType],
                    ["Experience", job?.experience],
                    ["Priority", job?.priority],
                    ["Status", job?.status],
                    ["Created", job?.createdAt
                      ? new Date(job.createdAt).toLocaleDateString("en-IN")
                      : null],
                  ].map(([label, val]) => (
                    <p key={label}>
                      <span className="font-medium text-slate-800">{label}:</span>{" "}
                      {val ?? <span className="text-slate-400">—</span>}
                    </p>
                  ))}
                </div>
              </div>

              {/* Live Candidate Counts — always fresh from API */}
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-500">
                  Pipeline Counts
                </h2>
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-2xl border bg-slate-50 p-4">
                    <p className="text-2xl font-bold text-slate-900">
                      {/* Prefer job.counts.total (from GET /jobs/{id}), fall back to state */}
                      {job?.counts?.total ?? totalAll}
                    </p>
                    <p className="mt-1 text-xs text-slate-500">Total</p>
                  </div>
                  <div className="rounded-2xl border border-green-200 bg-green-50 p-4">
                    <p className="text-2xl font-bold text-green-700">
                      {job?.counts?.filtered ?? totalFiltered}
                    </p>
                    <p className="mt-1 text-xs text-green-700">Filtered</p>
                  </div>
                  <div className="col-span-2 rounded-2xl border border-orange-200 bg-orange-50 p-4">
                    <p className="text-2xl font-bold text-orange-700">
                      {(job?.counts?.total ?? totalAll) -
                        (job?.counts?.filtered ?? totalFiltered)}
                    </p>
                    <p className="mt-1 text-xs text-orange-700">
                      Failed one or more criteria
                    </p>
                  </div>
                  {totalBlacklisted > 0 && (
                    <div className="col-span-2 rounded-2xl border border-red-200 bg-red-50 p-4">
                      <p className="text-2xl font-bold text-red-700">
                        {totalBlacklisted}
                      </p>
                      <p className="mt-1 text-xs text-red-700">Blacklisted</p>
                    </div>
                  )}
                  <div className="col-span-2 rounded-2xl border border-red-100 bg-red-50 p-4">
                    <p className="text-2xl font-bold text-red-600">
                      {safeCandidates.filter((c) => c.resumeStatus === "failed").length}
                    </p>
                    <p className="mt-1 text-xs text-red-500">Resume Processing Failed</p>
                  </div>
                </div>
              </div>

              {/* Screening Filter Rules — fixed 3 + 1 dynamic */}
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <div className="mb-5 flex items-start justify-between">
                  <div>
                    <h2 className="text-sm font-semibold text-slate-900">
                      Screening Filter Rules
                    </h2>

                    <p className="mt-1 text-xs text-slate-500">
                      Candidates must satisfy all active screening conditions.
                    </p>
                  </div>

                  <div className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
                    4 Active Rules
                  </div>
                </div>

                <div className="space-y-3">
                  {[
                    {
                      title: "End Client Approval",
                      value: "Candidate is comfortable working with end clients",
                      color: "emerald",
                    },
                    {
                      title: "C2H Availability",
                      value: "Candidate accepts contract-to-hire opportunities",
                      color: "emerald",
                    },
                    {
                      title: "PF Account Status",
                      value: "Candidate does not have an active PF account",
                      color: "emerald",
                    },
                    {
                      title: "Notice Period",
                      value: `Maximum allowed notice period is ${maxNoticeDays ?? "—"
                        } days`,
                      color: "blue",
                    },
                  ].map((rule) => (
                    <div
                      key={rule.title}
                      className={`flex items-start gap-4 rounded-xl border p-4 ${rule.color === "blue"
                        ? "border-blue-100 bg-blue-50"
                        : "border-emerald-100 bg-emerald-50"
                        }`}
                    >
                      <div
                        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-white ${rule.color === "blue"
                          ? "bg-blue-500"
                          : "bg-emerald-500"
                          }`}
                      >
                        <CheckCircle2 className="h-4 w-4" />
                      </div>

                      <div>
                        <p className="text-sm font-semibold text-slate-800">
                          {rule.title}
                        </p>

                        <p className="mt-1 text-xs leading-relaxed text-slate-600">
                          {rule.value}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="mt-4 mb-5 rounded-xl border border-amber-100 bg-amber-50 px-4 py-3">
                  <p className="text-xs text-amber-700">
                    Incomplete screening responses are automatically excluded.
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* ── RIGHT — Candidate List ── */}
          <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">

            {/* Tabs + info bar */}
            <div className="shrink-0 bg-white">
              <div className="border-b border-slate-200 px-3 pt-3 sm:px-6 sm:pt-4">
                <div className="-mx-1 flex gap-1 overflow-x-auto overscroll-x-contain px-1 pb-0 text-sm font-medium text-slate-500 scrollbar-none sm:gap-6 sm:overflow-visible">
                  {TABS.map((tab, i) => (
                    <button
                      key={tab.label}
                      onClick={() => {
                        clearListRestorePending(id);
                        listRestoreDone.current = true;
                        setHighlightedCandidateId(null);
                        setActiveTabIdx(i);
                        setPage(1);
                        updateHistoryState({ activeTabIdx: i, page: 1 });
                      }}
                      className={`shrink-0 whitespace-nowrap rounded-t-lg px-2.5 pb-2.5 pt-1 transition-colors sm:rounded-none sm:px-0 sm:pt-0 sm:pb-3 ${activeTabIdx === i
                        ? "border-b-2 border-slate-900 text-slate-900"
                        : "hover:text-slate-700"
                        }`}
                    >
                      <span className="sm:hidden">{tab.shortLabel}</span>
                      <span className="hidden sm:inline">{tab.label}</span>
                      {tab.id === "filtered" && totalFiltered > 0 && (
                        <span className="ml-2 rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-700">
                          {totalFiltered}
                        </span>
                      )}
                      {tab.id === "shortlisted" && shortlistedCount > 0 && (
                        <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
                          {shortlistedCount}
                        </span>
                      )}
                      {tab.id === "blacklisted" && totalBlacklisted > 0 && (
                        <span className="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-700">
                          {totalBlacklisted}
                        </span>
                      )}
                      {tab.id === "all" && totalAll > 0 && (
                        <span className="ml-2 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
                          {totalAll}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 bg-slate-50 px-3 py-2.5 text-xs text-slate-500 sm:gap-3 sm:px-6 sm:py-3">
                <span>
                  {isAtsRankedTab ? (
                    <>
                      <span className="font-medium text-blue-700">
                        {displayedCandidates.length}
                      </span>{" "}
                      candidates ranked by ATS score (highest first)
                      {atsTopLimit ? ` · showing top ${atsTopLimit}` : ""}
                      {" · "}
                      <span className="font-medium text-slate-700">{totalFiltered}</span>{" "}
                      passed screening
                    </>
                  ) : isShortlistedTab ? (
                    <>
                      <span className="font-medium text-amber-800">
                        {displayedCandidates.length}
                      </span>{" "}
                      shortlisted candidate
                      {displayedCandidates.length === 1 ? "" : "s"}
                      {" · "}
                      <span className="font-medium text-slate-700">{totalFiltered}</span>{" "}
                      passed screening
                    </>
                  ) : isBlacklistedTab ? (
                    <>
                      <span className="font-medium text-red-700">{totalBlacklisted}</span>{" "}
                      blacklisted candidate{totalBlacklisted === 1 ? "" : "s"}
                      {" · "}
                      profile and audit history preserved
                    </>
                  ) : isFilteredTab ? (
                    <>
                      <span className="font-medium text-green-700">{totalFiltered}</span> candidates
                      passed all 4 screening criteria ·{" "}
                      <span className="font-medium text-slate-700">{totalAll}</span> total
                    </>
                  ) : (
                    <>
                      Showing all <span className="font-medium text-slate-700">{totalAll}</span>{" "}
                      active candidates ·{" "}
                      <span className="font-medium text-green-700">{totalFiltered}</span> filtered
                      {totalBlacklisted > 0 && (
                        <>
                          {" · "}
                          <span className="font-medium text-red-600">{totalBlacklisted}</span>{" "}
                          blacklisted (see Blacklisted tab)
                        </>
                      )}
                    </>
                  )}
                </span>
                <div className="flex flex-wrap items-center gap-3">
                  {isAtsRankedTab ? (
                    <label className="flex items-center gap-2">
                      <span className="font-medium text-slate-600">Show</span>
                      <select
                        value={atsTopLimit ?? "all"}
                        onChange={(e) => {
                          const val = e.target.value;
                          setAtsTopLimit(val === "all" ? null : Number(val));
                          setPage(1);
                        }}
                        className="h-8 rounded-lg border border-slate-300 bg-white px-2.5 text-xs font-medium text-slate-700 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100"
                      >
                        {ATS_TOP_OPTIONS.map((opt) => (
                          <option
                            key={opt.label}
                            value={opt.value ?? "all"}
                          >
                            {opt.label}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : (
                    <>
                      <span className="font-medium text-slate-600">
                        Notice threshold: ≤ {maxNoticeDays ?? "—"} days
                      </span>
                      <button
                        onClick={() => setSortByAts(!sortByAts)}
                        className={`px-3 py-1 rounded-lg text-xs font-medium transition ${sortByAts
                          ? "bg-blue-100 text-blue-700 border border-blue-200"
                          : "bg-white text-slate-600 border border-slate-200 hover:bg-slate-50"
                          }`}
                      >
                        {sortByAts ? "Sort: ATS ↓" : "Sort: Default"}
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* CANDIDATES — scrollable (only this region scrolls, not the page or tabs) */}
            {isStaleAts && (
              <div className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-3 sm:px-4">
                <div className="flex flex-col items-stretch gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
                  <div>
                    <p className="font-medium text-amber-800">
                      ATS scores are based on an older Job Description version.
                    </p>

                    <p className="mt-1 text-sm text-amber-700">
                      This Job Description has been updated since ATS scoring was
                      last calculated. Existing scores are still available, but
                      they may not reflect the latest requirements.
                    </p>
                  </div>

                  <button
                    type="button"
                    onClick={handleForceRerunAts}
                    disabled={isActive || jobLoading}
                    className="inline-flex items-center gap-2 rounded-lg border border-amber-300 bg-white px-4 py-2 text-sm font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-50"
                  >
                    <RefreshCw className="h-4 w-4" />
                    Re-run ATS
                  </button>
                </div>
              </div>
            )}
            <div
              ref={listScrollRef}
              className="min-h-0 flex-1 overflow-y-auto overscroll-contain"
            >
              <div className="space-y-3 p-3 pb-8 sm:space-y-4 sm:p-4 sm:pb-10">
                {showAtsProgressGate ? (
                  <div className="rounded-3xl border border-dashed border-[#14344a]/25 bg-[#14344a]/5 p-10 text-center">
                    <Loader2 className="mx-auto h-10 w-10 animate-spin text-[#14344a]" />
                    <p className="mt-4 text-lg font-semibold text-slate-900">
                      ATS scoring in progress
                    </p>
                    <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
                      Rankings will appear here as soon as scores are ready.
                    </p>
                    <button
                      type="button"
                      onClick={openAtsProgressModal}
                      className="mt-4 text-sm font-medium text-[#14344a] underline"
                    >
                      Check ATS progress
                    </button>
                  </div>
                ) : showAtsRequiredGate ? (
                  <div className="rounded-3xl border border-dashed border-[#14344a]/25 bg-[#14344a]/5 p-10 text-center">
                    <Zap className="mx-auto h-10 w-10 text-[#14344a]/70" />
                    <p className="mt-4 text-lg font-semibold text-slate-900">
                      No ATS scores yet
                    </p>
                    <p className="mx-auto mt-2 max-w-md text-sm text-slate-600">
                      Run ATS scoring on filtered candidates to see ranked results
                      here.
                    </p>
                    {!isJobArchived && (
                      <button
                        type="button"
                        onClick={handleCalculateAts}
                        disabled={isActive || jobLoading}
                        className="mt-6 inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-6 py-3 text-sm font-medium text-white hover:bg-[#0f2a3c] disabled:opacity-50"
                      >
                        <Zap className="h-4 w-4" />
                        Calculate ATS Score for the Latest candidates
                      </button>
                    )}

                    {normalizedAtsStatus?.status === "failed" && (
                      <button
                        type="button"
                        onClick={() => setAtsModalOpen(true)}
                        className="mt-3 block mx-auto text-sm font-medium text-[#14344a] underline"
                      >
                        View error details
                      </button>
                    )}
                  </div>
                ) : showListSkeleton ? (
                  isInitialListLoad ? (
                    [...Array(5)].map((_, i) => <SkeletonRow key={i} />)
                  ) : (
                    <div className="flex items-center justify-center gap-2 py-20 text-sm text-slate-500">
                      <Loader2 className="h-5 w-5 animate-spin text-slate-400" />
                      Loading candidates…
                    </div>
                  )
                ) : candidatesError ? (
                  <div className="rounded-3xl bg-rose-50 p-6 text-center text-sm text-rose-700">
                    <AlertCircle className="mx-auto mb-2 h-6 w-6 text-rose-400" />
                    {candidatesError}
                    <button
                      onClick={() =>
                        fetchCandidates(activeTab.view, page, {
                          atsRanked: isAtsRankedTab,
                          bulkFiltered: isShortlistedTab,
                          tabId: activeTab.id,
                        })
                      }
                      className="mt-3 block mx-auto rounded-lg border border-rose-200 px-4 py-1.5 text-xs hover:bg-rose-100"
                    >
                      Retry
                    </button>
                  </div>
                ) : displayedCandidates.length === 0 ? (
                  <div className="rounded-3xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center text-slate-500">
                    <p className="font-medium text-slate-800">No candidates found</p>
                    <p className="mx-auto mt-1 max-w-md text-xs text-slate-400">
                      {isAtsRankedTab
                        ? hasAtsScores
                          ? "No scored candidates in the current top-N filter. Try “All ranked” or run ATS again."
                          : "Run ATS scoring on filtered candidates to see ranked results here."
                        : isShortlistedTab
                          ? "No shortlisted candidates yet. Use Shortlist on a candidate card to add them here."
                          : isBlacklistedTab
                            ? "No blacklisted candidates. Use Blacklist on a candidate card to flag fake or invalid profiles."
                            : isFilteredTab
                              ? "No candidates passed all 4 screening criteria for this job."
                              : "No candidates have been received for this job yet."}
                    </p>
                    <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
                      {!isJobArchived && !hasAtsScores && !isBlacklistedTab && !isShortlistedTab && (
                        <button
                          type="button"
                          onClick={handleCalculateAts}
                          disabled={isActive || jobLoading}
                          className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2 text-sm font-medium text-white hover:bg-[#0f2a3c] disabled:opacity-50"
                        >
                          <Zap className="h-4 w-4" />
                          Calculate ATS Score
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => setEditModalOpen(true)}
                        className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                      >
                        Edit Job
                      </button>
                      <button
                        type="button"
                        onClick={() => navigate("/jobs")}
                        className="inline-flex items-center gap-2 rounded-xl border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                      >
                        Back to Jobs
                      </button>
                    </div>
                  </div>
                ) : (
                  displayedCandidates.map((c, rankIdx) => {
                    const badge =
                      RESUME_BADGE[c.resumeStatus] ?? {
                        label: c.resumeStatus ?? "Unknown",
                        cls: "bg-slate-100 text-slate-500 border-slate-200",
                      };
                    const shortlisted = isShortlistedCandidate(c, shortlistedIds);
                    const blacklisted = isBlacklistedTab || isCandidateBlacklisted(c);
                    const blacklistReason = getBlacklistReason(c);

                    return (
                      <div
                        key={c.candidateId}
                        id={`candidate-row-${c.candidateId}`}
                        onClick={() => handleOpenCandidate(c)}
                        className={`grid cursor-pointer grid-cols-1 gap-3 rounded-2xl border bg-white p-3.5 transition-all duration-200 hover:border-slate-300 hover:shadow-lg sm:gap-4 sm:rounded-3xl sm:p-5 xl:grid-cols-[2fr_1.2fr_1fr_1fr] ${highlightedCandidateId === c.candidateId
                          ? "border-[#14344a] ring-2 ring-[#14344a]/30 ring-offset-2 shadow-lg"
                          : "border-slate-200"
                          }`}
                      >
                        {/* COL 1 — Candidate info */}
                        <div>
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex items-start gap-3">
                              {isAtsRankedTab && (
                                <span
                                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-sm font-bold text-blue-700"
                                  title={`Rank #${rankIdx + 1}`}
                                >
                                  {rankIdx + 1}
                                </span>
                              )}
                              <div>
                                <h3 className="text-base font-semibold text-slate-900 sm:text-lg">
                                  {c.name ?? "—"}
                                </h3>
                                <p className="mt-0.5 text-sm text-slate-600">
                                  {c.currentRole ?? "—"}
                                </p>
                                {c.currentCompany && (
                                  <p className="text-xs text-slate-400">
                                    {c.currentCompany}
                                  </p>
                                )}
                              </div>
                            </div>
                            <div className="flex shrink-0 flex-col items-end gap-1">
                              {shortlisted && !blacklisted && (
                                <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-[10px] font-semibold text-amber-800">
                                  Shortlisted
                                </span>
                              )}
                              {blacklisted && (
                                <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-[10px] font-semibold text-red-800">
                                  Blacklisted
                                </span>
                              )}
                              <span
                                className={`rounded-full border px-3 py-1 text-xs font-medium ${badge.cls}`}
                              >
                                {badge.label}
                              </span>
                            </div>
                          </div>
                          <div className="mt-4 grid grid-cols-2 gap-2 text-xs text-slate-500">
                            <p>
                              <span className="font-medium text-slate-700">Exp:</span>{" "}
                              {c.experienceYears != null ? `${c.experienceYears} yrs` : "—"}
                            </p>
                            <p>
                              <span className="font-medium text-slate-700">Notice:</span>{" "}
                              {c.noticePeriodDays != null
                                ? `${c.noticePeriodDays}d`
                                : "—"}
                            </p>
                            <p>
                              <span className="font-medium text-slate-700">Current:</span>{" "}
                              {c.currentCtc != null ? `${c.currentCtc} LPA` : "—"}
                            </p>
                            <p>
                              <span className="font-medium text-slate-700">Expected:</span>{" "}
                              {c.expectedCtc != null ? `${c.expectedCtc} LPA` : "—"}
                            </p>
                          </div>
                          {c.needsReview && (
                            <span className="mt-3 inline-block rounded-full bg-amber-100 px-3 py-1 text-xs font-medium text-amber-700">
                              ⚠ Needs Review
                            </span>
                          )}
                          {blacklisted && blacklistReason && (
                            <p className="mt-2 text-xs text-red-600">
                              Reason: {blacklistReason}
                            </p>
                          )}
                        </div>

                        {/* COL 2 — Screening (tablet+) */}
                        <div className="hidden sm:block">
                          <ScreeningColumn
                            c={c}
                            maxNoticePeriodDays={maxNoticeDays}
                            view={activeTab.view}
                          />
                        </div>

                        {/* COL 3 — Actions */}
                        <div
                          className="grid grid-cols-2 gap-2 sm:flex sm:flex-col sm:gap-2.5"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {/* <button
                            onClick={() =>
                              navigate(
                                `/jobs/${id}/candidate/${c.candidateId}`,
                                {
                                  state: {
                                    candidate: c,
                                    job,
                                    activeTabId: activeTab.id,
                                    activeTabIdx,
                                    page,
                                  },
                                }
                              )
                            }
                            className="rounded-xl bg-[#14344a] px-4 py-2 text-xs font-medium text-white hover:bg-[#0f2a3c]"
                          > */}
                          <button
                            onClick={() => handleOpenCandidate(c)}
                            className="col-span-2 rounded-xl bg-[#14344a] px-3 py-2.5 text-xs font-medium text-white hover:bg-[#0f2a3c] sm:col-span-1 sm:px-4"
                          >
                            View Profile
                          </button>
                          <button
                            onClick={() =>
                              handleViewResume(c.candidateId, c.name, c.resumeStatus)
                            }
                            disabled={c.resumeStatus !== "completed"}
                            className="rounded-xl border border-slate-300 bg-white px-4 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            View Resume
                          </button>
                          {isBlacklistedTab ? (
                            <button
                              type="button"
                              onClick={(e) =>
                                handleUnblacklistCandidate(e, c.candidateId, c.name)
                              }
                              className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-emerald-300 bg-emerald-50 px-4 py-2 text-xs font-medium text-emerald-800 hover:bg-emerald-100"
                            >
                              <RotateCcw className="h-3.5 w-3.5" />
                              Restore
                            </button>
                          ) : (
                            <>
                              <button
                                type="button"
                                onClick={(e) =>
                                  handleToggleShortlist(e, c.candidateId)
                                }
                                className={`rounded-xl px-4 py-2 text-xs font-medium ${shortlisted
                                  ? "border border-amber-300 bg-amber-50 text-amber-900 hover:bg-amber-100"
                                  : "bg-green-100 text-green-700 hover:bg-green-200"
                                  }`}
                              >
                                {shortlisted ? "★ Shortlisted" : "☆ Shortlist"}
                              </button>
                              <button
                                type="button"
                                onClick={(e) =>
                                  handleBlacklistCandidate(e, c.candidateId, c.name)
                                }
                                title="Soft-blacklist — candidate stays on file but is hidden from all lists and ATS"
                                className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-700 hover:bg-red-100"
                              >
                                <Ban className="h-3.5 w-3.5" />
                                Blacklist
                              </button>
                            </>
                          )}
                        </div>

                        {/* COL 4 — Resume / ATS */}
                        <div className="rounded-2xl bg-slate-50 p-3 sm:p-4">
                          <div className="flex items-start justify-between">
                            <div>
                              <p className="text-xs text-slate-500">Resume</p>
                              <span
                                className={`mt-1 inline-block rounded-full border px-2 py-0.5 text-xs font-semibold ${badge.cls}`}
                              >
                                {badge.label}
                              </span>
                            </div>
                            <AtsScoreBadge score={c.atsScore} size="md" />
                          </div>
                          <div className="mt-4 space-y-1.5 text-xs text-slate-600">
                            <div className="flex justify-between">
                              <span>Added</span>
                              <span className="font-medium text-slate-800">
                                {c.createdAt
                                  ? new Date(c.createdAt).toLocaleDateString("en-IN")
                                  : "—"}
                              </span>
                            </div>
                            <div className="flex justify-between">
                              <span>ATS Score</span>
                              <span className="font-medium text-slate-600">
                                {c.atsScore !== null && c.atsScore !== undefined
                                  ? `${c.atsScore.toFixed(1)}%`
                                  : "Not scored"}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            {/* ── PAGINATION (hidden on ATS Ranked — full ranked list on one page) ── */}
            {!showAtsRequiredGate &&
              !showAtsProgressGate &&
              !usesSinglePageList &&
              listDataReady &&
              !candidatesError &&
              totalPages > 1 && (
                <div className="shrink-0 flex items-center justify-between border-t border-slate-200 bg-white px-3 py-3 sm:px-6">
                  <p className="text-xs text-slate-500">
                    Page {page} of {totalPages} ·{" "}
                    {totalCount} candidates
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      disabled={!hasPrev}
                      onClick={() => handleSetPage(page - 1)}
                      className="rounded-lg border border-slate-300 p-1.5 text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                    <span className="min-w-[2rem] text-center text-sm font-medium text-slate-700">
                      {page}
                    </span>
                    <button
                      disabled={!hasMore}
                      onClick={() => handleSetPage(page + 1)}
                      className="rounded-lg border border-slate-300 p-1.5 text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              )}
          </div>
        </div>
      </div>
    </>
  );
};

export default JobCandidates;