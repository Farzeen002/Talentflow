import { useEffect, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import {
  ArrowLeft, Mail, MapPin, Briefcase,
  Download, FileText, Loader2, AlertCircle,
  CheckCircle2, XCircle, MinusCircle,
  Maximize2, Minimize2, ExternalLink, X, Ban, RotateCcw,
} from "lucide-react";
import {
  getCandidate,
  getResumePreview,
  getResumeDownload,
  blacklistCandidate,
  unblacklistCandidate,
} from "../services/candidates";
import AtsScoreBadge from "../components/AtsScoreBadge";
import AtsInsightsPanel from "../components/AtsInsightsPanel";
import { getCandidateAtsScore } from "../lib/atsHelpers";
import {
  loadShortlistIds,
  saveShortlistIds,
  isShortlistedCandidate,
} from "../lib/shortlistStorage";
import { toast } from "sonner";
import { fetchCandidateAtsScore } from "../services/ats";
import {
  isCandidateBlacklisted,
  getBlacklistAudit,
  getBlacklistErrorMessage,
} from "../lib/blacklistHelpers";

/* ──────────────────────────────────────────────
   Formatting helpers
────────────────────────────────────────────── */

/**
 * metadata.profileCtcRupees is RAW RUPEES (e.g. 450000 = ₹4.5L)
 * qa.currentCtc / qa.expectedCtc are already in LPA (e.g. 9 = 9 LPA)
 * NEVER mix these two units.
 */
const formatProfileCtc = (rupees) =>
  rupees != null ? `₹${(rupees / 100000).toFixed(1)}L` : "—";

const formatLpa = (lpa) => (lpa != null ? `${lpa} LPA` : "—");

const formatBool = (val) => {
  if (val === null || val === undefined) return "Not answered";
  return val ? "Yes" : "No";
};

const isWordResume = (resume) => {
  const fileName = (resume?.original?.filename ?? "").toString().toLowerCase();
  const contentType = (resume?.original?.contentType ?? resume?.contentType ?? "").toString().toLowerCase();
  return (
    fileName.endsWith(".doc") ||
    fileName.endsWith(".docx") ||
    contentType.includes("msword") ||
    contentType.includes("wordprocessingml")
  );
};

/** Resume status → badge config */
const RESUME_BADGE = {
  completed: { label: "Available", cls: "bg-green-100 text-green-700 border-green-200" },
  missing: { label: "No Resume", cls: "bg-slate-100 text-slate-500 border-slate-200" },
  pending: { label: "Pending", cls: "bg-yellow-100 text-yellow-700 border-yellow-200" },
  uploaded: { label: "Processing", cls: "bg-yellow-100 text-yellow-700 border-yellow-200" },
  processing: { label: "Extracting…", cls: "bg-blue-100 text-blue-700 border-blue-200" },
  failed: { label: "Failed", cls: "bg-red-100 text-red-700 border-red-200" },
};

/* ──────────────────────────────────────────────
   Sub-components
────────────────────────────────────────────── */

/** Screening Q&A row with pass/fail/unknown icon */
const QARow = ({ label, value, passFn }) => {
  const isUnanswered = value === "Not answered" || value === "—";
  const passes = !isUnanswered && (passFn ? passFn(value) : true);

  const Icon = isUnanswered
    ? MinusCircle
    : passes
      ? CheckCircle2
      : XCircle;

  const iconCls = isUnanswered
    ? "text-slate-300"
    : passes
      ? "text-green-500"
      : "text-red-400";

  const chipCls = isUnanswered
    ? "border-slate-200 bg-slate-50 text-slate-400"
    : passes
      ? "border-green-200 bg-green-50 text-green-700"
      : "border-red-200 bg-red-50 text-red-600";

  return (
    <div className="flex items-center gap-3">
      <Icon className={`h-4 w-4 shrink-0 ${iconCls}`} />
      <div className="flex flex-wrap items-center gap-2 text-sm text-slate-700">
        <span className="font-medium">{label}:</span>
        <span className={`rounded-full border px-3 py-0.5 text-xs font-medium ${chipCls}`}>
          {value}
        </span>
      </div>
    </div>
  );
};

/** Skeleton for detail page loading */
const DetailSkeleton = () => (
  <div className="h-screen overflow-hidden bg-[#f6f7fb] p-4">
    <div className="mb-3 flex items-center gap-2">
      <div className="h-8 w-8 animate-pulse rounded-lg bg-slate-200" />
      <div className="h-4 w-40 animate-pulse rounded bg-slate-200" />
    </div>
    <div className="flex h-[calc(100vh-70px)] w-full flex-col gap-4">
      <div className="h-28 animate-pulse rounded-2xl bg-slate-200" />
      <div className="grid flex-1 grid-cols-2 gap-4">
        <div className="animate-pulse rounded-2xl bg-slate-200" />
        <div className="animate-pulse rounded-2xl bg-slate-200" />
      </div>
    </div>
  </div>
);

/* ──────────────────────────────────────────────
   Main Component
────────────────────────────────────────────── */
const CandidateDetails = () => {
  const { id, candidateId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const [candidate, setCandidate] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [resumePreviewUrl, setResumePreviewUrl] = useState(null);
  const [resumePreviewLoading, setResumePreviewLoading] = useState(false);
  const [resumeDownloadLoading, setResumeDownloadLoading] = useState(false);
  const [shortlistedIds, setShortlistedIds] = useState(new Set());
  const [isShortlisted, setIsShortlisted] = useState(false);
  const [atsData, setAtsData] = useState(null);
  const [atsLoading, setAtsLoading] = useState(true);
  const [atsError, setAtsError] = useState(null);
  const [isPreviewFullscreen, setIsPreviewFullscreen] = useState(false);
  const jobId = id;

  useEffect(() => {
    const fetchDetail = async () => {
      setLoading(true);
      setError("");
      try {
        // GET /api/v1/candidates/{candidateId}
        // Backend isolates by recruiter_id — wrong UUID returns 404
        const { data } = await getCandidate(candidateId);
        setCandidate(data);
      } catch (err) {
        const status = err?.response?.status;
        if (status === 404) {
          setError("Candidate not found or you do not have access to this profile.");
        } else if (status !== 401 && status !== 403) {
          setError("Failed to load candidate details.");
        }
      } finally {
        setLoading(false);
      }
    };
    fetchDetail();
  }, [candidateId]);

  const candidateJobId = candidate?.jobId ?? candidate?.job_id ?? null;
  const candidateInternalId = candidate?.id ?? candidate?.candidateId ?? null;

  /** Prefer the signed GCS URL in the iframe. `/gcs-proxy` only works if Vite/Nginx
   *  proxies to storage.googleapis.com — otherwise the SPA index.html loads and the
   *  dashboard appears inside the resume viewer. */
  const getPreviewSrc = (urlStr) => {
    if (!urlStr || typeof urlStr !== "string") return null;
    try {
      const parsed = new URL(urlStr);
      if (parsed.hostname === "storage.googleapis.com") {
        return urlStr;
      }
    } catch (err) {
      console.error("Invalid preview URL", err);
    }
    return urlStr;
  };

  useEffect(() => {
    if (!candidateJobId) return;
    const ids = loadShortlistIds(candidateJobId);
    setShortlistedIds(ids);
    setIsShortlisted(isShortlistedCandidate(candidate, ids));
  }, [candidate, candidateJobId]);

  useEffect(() => {
    const fetchPreview = async () => {
      if (!candidate || !candidate.resume || candidate.resume.status !== "completed") {
        setResumePreviewUrl(null);
        return;
      }

      if (isWordResume(candidate.resume)) {
        setResumePreviewUrl(null);
        return;
      }

      try {
        setResumePreviewLoading(true);
        const { data } = await getResumePreview(candidateId);
        setResumePreviewUrl(getPreviewSrc(data.url));
      } catch (err) {
        console.error("Failed to fetch resume preview:", err);
        setResumePreviewUrl(null);
      } finally {
        setResumePreviewLoading(false);
      }
    };

    fetchPreview();
  }, [candidate, candidateId]);

  useEffect(() => {
    if (!candidateId || !jobId) return;

    const fetchAts = async () => {
      try {
        setAtsLoading(true);

        const data = await fetchCandidateAtsScore(
          candidateId,
          jobId
        );

        console.log("ATS API RESPONSE:", data);

        setAtsData(data);
      } catch (err) {
        console.error("ATS fetch failed", err);
        setAtsError(err);
      } finally {
        setAtsLoading(false);
      }
    };

    fetchAts();
  }, [candidateId, jobId]);

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.key === "Escape") {
        setIsPreviewFullscreen(false);
      }
    };
    if (isPreviewFullscreen) {
      window.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [isPreviewFullscreen]);

  // Handle resume download
  const handleNavigateBack = () => {
    if (location.state?.returnTo === "/centralized-candidates") {
      const restore = {
        ...(location.state.centralizedRestore ?? {}),
        lastViewedCandidateId: candidateId,
        restoreList: true,
      };
      navigate("/centralized-candidates", { state: restore });
      return;
    }

    const returnState = {
      ...(location.state ?? {}),
      activeTabIdx: location.state?.activeTabIdx ?? 0,
      page: location.state?.page ?? 1,
      lastViewedCandidateId: candidateId,
      restoreList: true,
    };

    navigate(`/jobs/${id}`, { state: returnState });
  };

  const handleBlacklistCandidate = async () => {
    const name = candidate?.metadata?.name ?? candidate?.name ?? "this candidate";
    const reason = window.prompt(
      `Blacklist ${name}? They will be hidden from all lists and ATS scoring.\n\nReason (optional but recommended):`,
      ""
    );
    if (reason === null) return;

    try {
      await blacklistCandidate(candidateId, { reason: reason.trim() || undefined });
      toast.success("Candidate blacklisted");
      const { data } = await getCandidate(candidateId);
      setCandidate(data);
      if (candidateJobId && candidateInternalId) {
        setShortlistedIds((prev) => {
          const next = new Set(prev);
          next.delete(candidateInternalId);
          saveShortlistIds(candidateJobId, next);
          return next;
        });
        setIsShortlisted(false);
      }
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) {
        toast.info("Candidate is already blacklisted");
        const { data } = await getCandidate(candidateId);
        setCandidate(data);
        return;
      }
      toast.error(getBlacklistErrorMessage(err));
    }
  };

  const handleUnblacklistCandidate = async () => {
    const name = candidate?.metadata?.name ?? candidate?.name ?? "this candidate";
    if (!window.confirm(`Restore ${name} to active status?`)) return;

    try {
      await unblacklistCandidate(candidateId);
      toast.success("Candidate restored");
      const { data } = await getCandidate(candidateId);
      setCandidate(data);
    } catch (err) {
      const status = err?.response?.status;
      if (status === 409) {
        toast.info("Candidate is already active");
        const { data } = await getCandidate(candidateId);
        setCandidate(data);
        return;
      }
      toast.error(getBlacklistErrorMessage(err));
    }
  };

  const handleToggleShortlist = () => {
    if (!candidateJobId || !candidateInternalId) return;

    setShortlistedIds((prev) => {
      const next = new Set(prev);
      if (next.has(candidateInternalId)) {
        next.delete(candidateInternalId);
        setIsShortlisted(false);
        toast.success("Removed from shortlist");
      } else {
        next.add(candidateInternalId);
        setIsShortlisted(true);
        toast.success("Added to shortlist");
      }
      saveShortlistIds(candidateJobId, next);
      return next;
    });
  };

  const handleDownloadResume = async () => {
    try {
      setResumeDownloadLoading(true);
      const { data } = await getResumeDownload(candidateId);
      // Trigger browser download
      const link = document.createElement("a");
      link.href = data.url;
      link.download = data.filename || "resume.pdf";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      toast.success("Resume downloaded successfully!");
    } catch (err) {
      console.error("Failed to download resume:", err);
      toast.error("Failed to download resume. Please try again.");
    } finally {
      setResumeDownloadLoading(false);
    }
  };

  if (loading) return <DetailSkeleton />;
  console.log("ATS STATE:", atsData);

  if (error) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#f6f7fb] px-4 py-10 sm:px-6 lg:px-8">
        <AlertCircle className="h-10 w-10 text-red-400" />
        <p className="text-center text-red-600">{error}</p>
        <button
          onClick={handleNavigateBack}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          Go Back
        </button>
      </div>
    );
  }

  /* ── Destructure API response sections ── */
  const meta = candidate?.metadata ?? {};
  const qa = candidate?.qa ?? {};
  const skills = candidate?.skills?.raw ?? [];
  const resume = candidate?.resume ?? {};
  const proc = candidate?.processing ?? {};
  const candidateAtsScore = getCandidateAtsScore(candidate);
  const blacklistAudit = getBlacklistAudit(candidate);
  const isBlacklisted = isCandidateBlacklisted(candidate);

  // email.from — safe access (field name is reserved word but valid as string key)
  const emailSender = candidate?.email?.["from"] ?? "—";
  const emailSubject = candidate?.email?.subject ?? "—";
  const emailTimestamp = candidate?.email?.timestamp ?? null;

  // Resume badge
  const rBadge = RESUME_BADGE[resume?.status] ?? {
    label: resume?.status ?? "—",
    cls: "bg-slate-100 text-slate-500 border-slate-200",
  };

  // Screening Q&A rows with pass/fail logic matching the 4 filter criteria
  const qaRows = [
    {
      label: "OK with End Client",
      value: formatBool(qa.isOkClient),
      passFn: (v) => v === "Yes",
    },
    {
      label: "C2H Accepted",
      value: formatBool(qa.isC2hOk),
      passFn: (v) => v === "Yes",
    },
    {
      label: "PF Account",
      value: formatBool(qa.hasPfAccount),
      passFn: (v) => v === "Yes",
    },
    {
      label: "Willing to Relocate",
      value: formatBool(qa.willingToRelocate),
      passFn: null,
    },
    {
      label: "Notice Period",
      value: qa.noticePeriodDays != null ? `${qa.noticePeriodDays} days` : "Not answered",
      passFn: null,
    },
    {
      label: "Current CTC (Q&A)",
      value: formatLpa(qa.currentCtc),
      passFn: null,
    },
    {
      label: "Expected CTC (Q&A)",
      value: formatLpa(qa.expectedCtc),
      passFn: null,
    },
    {
      label: "Run Mgmt Exp.",
      value: qa.experienceRunManagementYears != null
        ? `${qa.experienceRunManagementYears} yrs`
        : "Not answered",
      passFn: null,
    },
    {
      label: "Service Delivery Exp.",
      value: qa.experienceServiceDeliveryYears != null
        ? `${qa.experienceServiceDeliveryYears} yrs`
        : "Not answered",
      passFn: null,
    },
  ];

  const initials = meta.name
    ? meta.name.split(" ").map((n) => n[0]).join("").toUpperCase().slice(0, 2)
    : "?";

  return (
    <div className="flex h-[calc(100dvh-4rem-4.5rem)] flex-col overflow-hidden bg-[#f6f7fb] px-3 py-3 sm:px-6 sm:py-4 lg:h-[100dvh] lg:px-8">

      {/* PAGE HEADER */}
      <div className="mb-3 flex shrink-0 flex-wrap items-center gap-2">
        <button
          onClick={handleNavigateBack}
          className="rounded-lg border border-slate-200 bg-white p-2.5 hover:bg-slate-50"
          aria-label="Back"
        >
          <ArrowLeft className="h-4 w-4 text-slate-700" />
        </button>
        <h1 className="text-sm font-semibold text-slate-800">
          Candidate Profile
        </h1>
        {proc.needsReview && (
          <span className="rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-700 sm:ml-2 sm:px-3">
            ⚠ Needs Review
          </span>
        )}
        {isBlacklisted && (
          <span className="rounded-full bg-red-100 px-2.5 py-0.5 text-xs font-medium text-red-700 sm:ml-2 sm:px-3">
            Blacklisted
          </span>
        )}
      </div>

      {/* MAIN LAYOUT */}
      <div className="mx-auto flex min-h-0 w-full max-w-screen-2xl flex-1 flex-col gap-3 overflow-hidden sm:gap-4">

        {/* TOP CARD — profile header */}
        <div className="w-full shrink-0 rounded-2xl border border-slate-200 bg-white p-3 shadow-sm sm:p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">

            {/* Avatar + identity */}
            <div className="flex min-w-0 items-center gap-3 sm:gap-4">
              <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-slate-700 to-slate-900 text-lg font-bold text-white sm:h-16 sm:w-16 sm:text-xl">
                {initials}
              </div>
              <div className="min-w-0">
                <h2 className="truncate text-xl font-bold tracking-tight text-slate-900 sm:text-2xl">
                  {meta.name ?? "Name not available"}
                </h2>
                <div className="mt-1 flex flex-wrap items-center gap-3 text-sm text-slate-500">
                  {meta.currentRole && (
                    <div className="flex items-center gap-1">
                      <Briefcase className="h-3.5 w-3.5" />
                      {meta.currentRole}
                    </div>
                  )}
                  {meta.currentRole && meta.currentCompany && <span>·</span>}
                  {meta.currentCompany && (
                    <span className="text-slate-600">{meta.currentCompany}</span>
                  )}
                  {meta.experienceYears != null && (
                    <>
                      <span>·</span>
                      <span>{meta.experienceYears} yrs exp</span>
                    </>
                  )}
                  {meta.currentLocation && (
                    <>
                      <span>·</span>
                      <div className="flex items-center gap-1">
                        <MapPin className="h-3.5 w-3.5" />
                        {meta.currentLocation}
                      </div>
                    </>
                  )}
                </div>
                {/* Source & Job metadata */}
                <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                  {/* <span>ID: {candidate?.candidateId ?? "—"}</span> */}
                  {candidateJobId && (
                    <>
                      {/* <span>·</span> */}
                      <span>Job: {candidateJobId}</span>
                    </>
                  )}
                  {/* <span>·</span> */}
                  {/* <span>Source: {candidate?.source ?? "—"}</span> */}
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center">
              <button
                onClick={handleDownloadResume}
                disabled={resumeDownloadLoading || candidate?.resume?.status !== "completed"}
                className="col-span-2 flex items-center justify-center gap-1.5 rounded-lg bg-[#111827] px-3 py-2.5 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50 sm:col-span-1 sm:px-4 sm:py-2"
              >
                {resumeDownloadLoading ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Downloading...
                  </>
                ) : (
                  <>
                    <Download className="h-3.5 w-3.5" /> Download Resume
                  </>
                )}
              </button>
              <button
                onClick={handleToggleShortlist}
                disabled={isBlacklisted}
                className={`rounded-lg border px-4 py-2 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${isShortlisted
                  ? "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100"
                  : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                  }`}
              >
                {isShortlisted ? "★ Shortlisted" : "☆ Shortlist"}
              </button>
              {isBlacklisted ? (
                <button
                  type="button"
                  onClick={handleUnblacklistCandidate}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-2 text-xs font-medium text-emerald-800 hover:bg-emerald-100"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  Restore
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleBlacklistCandidate}
                  title="Soft-blacklist — candidate stays on file but is hidden from all lists and ATS"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-700 hover:bg-red-100"
                >
                  <Ban className="h-3.5 w-3.5" />
                  Blacklist
                </button>
              )}
              <AtsScoreBadge score={candidateAtsScore} size="md" />
            </div>
          </div>
        </div>

        {/* CONTENT GRID — independent scroll panes on desktop */}
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto sm:gap-4 lg:grid-cols-[48%_52%] lg:overflow-hidden xl:grid-cols-[45%_55%]">

          {/* ── LEFT SIDE ── */}
          <div className="min-h-0 space-y-3 overflow-visible pb-2 lg:overflow-y-auto lg:overscroll-contain lg:pb-4 lg:pr-1">

            {isBlacklisted && (blacklistAudit?.reason || blacklistAudit?.blacklistedAt) && (
              <div className="rounded-2xl border border-red-200 bg-red-50 p-5 shadow-sm">
                <h3 className="text-sm font-semibold text-red-900">
                  Blacklist Status
                </h3>
                <div className="mt-3 space-y-2 text-sm text-red-800">
                  {blacklistAudit.reason && (
                    <p>
                      <span className="font-medium">Reason:</span> {blacklistAudit.reason}
                    </p>
                  )}
                  {blacklistAudit.blacklistedAt && (
                    <p>
                      <span className="font-medium">Blacklisted:</span>{" "}
                      {new Date(blacklistAudit.blacklistedAt).toLocaleString("en-IN")}
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Contact Information */}
            <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-100 px-4 py-3">
                <h3 className="text-sm font-semibold text-slate-900">
                  Contact Information
                </h3>
              </div>

              {/* Experience grid */}
              <div className="border-t border-slate-100 px-4 py-4">
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Experience Details
                </h3>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {[
                    ["Total Exp.", meta.experienceYears != null ? `${meta.experienceYears} yrs` : "—"],
                    ["Current Role", meta.currentRole ?? "—"],
                    ["Company", meta.currentCompany ?? "—"],
                    ["Job Title", meta.jobTitle ?? "—"],
                    ["Notice (Profile)", meta.profileNoticeDays != null ? `${meta.profileNoticeDays}d` : "—"],
                    // profileCtcRupees is RAW RUPEES — format as LPA
                    ["CTC (Profile)", formatProfileCtc(meta.profileCtcRupees)],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-xl border border-slate-200 bg-slate-50 p-3">
                      <p className="text-[10px] text-slate-500">{label}</p>
                      <p className="mt-1 text-sm font-medium text-slate-800">{value}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Compensation — Q&A values are in LPA */}
              <div className="border-t border-slate-100 px-4 py-4">
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Compensation (Q&A — in LPA)
                </h3>
                <div className="flex gap-3">
                  <span className="rounded-full bg-blue-50 px-4 py-2 text-sm font-medium text-slate-700">
                    Current: {formatLpa(qa.currentCtc)}
                  </span>
                  <span className="rounded-full bg-yellow-50 px-4 py-2 text-sm font-medium text-slate-700">
                    Expected: {formatLpa(qa.expectedCtc)}
                  </span>
                </div>
                <p className="mt-2 text-xs text-slate-400">
                  Note: Profile CTC (₹ rupees) and Q&A CTC (LPA) come from different sources.
                </p>
              </div>
            </div>


            {/* Screening Q&A Answers */}
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <div className="mb-5 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">
                    Recruiter Screening Summary
                  </h3>

                  <p className="mt-1 text-xs text-slate-500">
                    Candidate responses collected during recruiter screening.
                  </p>
                </div>

                <div className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700">
                  Verified Responses
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                {qaRows.map(({ label, value, passFn }) => {
                  const passed =
                    typeof passFn === "function" ? passFn(value) : null;

                  return (
                    <div
                      key={label}
                      className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 transition-all hover:border-slate-300"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
                            {label}
                          </p>

                          <p className="mt-1 text-sm font-semibold text-slate-800">
                            {value || "Not Provided"}
                          </p>
                        </div>

                        {passed !== null && (
                          <div
                            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${passed
                              ? "bg-emerald-100 text-emerald-700"
                              : "bg-rose-100 text-rose-700"
                              }`}
                          >
                            {passed ? "Eligible" : "Mismatch"}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Skills */}
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-slate-900">Skills</h3>
              <p className="mt-0.5 text-xs text-slate-500">
                {skills.length > 0 ? "Extracted from resume/profile" : "No skills extracted yet"}
              </p>
              {skills.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {skills.map((skill) => (
                    <span
                      key={skill}
                      className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-700"
                    >
                      {skill}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* ── RIGHT SIDE ── */}
          <div className="min-h-0 space-y-3 overflow-visible pb-2 lg:overflow-y-auto lg:overscroll-contain lg:pb-4 lg:pr-1">

            {/* Resume Review */}
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">Resume Review</h3>
                  <div className="mt-1 flex items-center gap-2">
                    <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${rBadge.cls}`}>
                      {rBadge.label}
                    </span>
                    {resume.original?.filename && (
                      <span className="text-xs text-slate-400">
                        {resume.original.filename}
                      </span>
                    )}
                    {resume.original?.sizeBytes != null && (
                      <span className="text-xs text-slate-400">
                        ({(resume.original.sizeBytes / 1024).toFixed(0)} KB)
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  {resume.status === "completed" && resumePreviewUrl && (
                    <>
                      <a
                        href={resumePreviewUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
                        title="Open resume in a new browser tab"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Open in New Tab</span>
                      </a>
                      <button
                        onClick={() => setIsPreviewFullscreen(true)}
                        className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
                        title="View fullscreen"
                      >
                        <Maximize2 className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Fullscreen</span>
                      </button>
                    </>
                  )}
                  <button
                    onClick={handleDownloadResume}
                    disabled={resumeDownloadLoading || resume.status !== "completed"}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    {resumeDownloadLoading ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Downloading...
                      </>
                    ) : (
                      <>
                        <Download className="h-3.5 w-3.5" /> Download
                      </>
                    )}
                  </button>
                </div>
              </div>

              {/* PDF placeholder */}
              <div className="overflow-hidden rounded-xl border border-slate-200 shadow-sm transition-all duration-200 hover:shadow-md">
                {resume.status === "completed" && !isWordResume(resume) ? (
                  resumePreviewLoading ? (
                    <div className="flex min-h-[420px] items-center justify-center bg-[#2b2b2b] sm:min-h-[450px] lg:min-h-[520px]">
                      <div className="text-center">
                        <Loader2 className="mx-auto h-8 w-8 animate-spin text-white/50" />
                        <p className="mt-3 text-sm text-white/70">Loading resume preview…</p>
                      </div>
                    </div>
                  ) : resumePreviewUrl ? (
                    <iframe
                      src={resumePreviewUrl}
                      className="min-h-[420px] w-full border-0 transition-opacity duration-300 sm:min-h-[450px] lg:min-h-[520px]"
                      title="Resume Preview"
                    />
                  ) : (
                    <div className="flex min-h-[420px] flex-col items-center justify-center bg-[#2b2b2b] px-6 text-center sm:min-h-[450px] lg:min-h-[520px]">
                      <FileText className="mx-auto h-12 w-12 text-white/50" />
                      <p className="mt-3 text-sm text-white/70 font-semibold">
                        Unable to preview resume inline.
                      </p>
                      <p className="mt-2 text-xs text-white/70">
                        Please download the resume to view it.
                      </p>
                    </div>
                  )
                ) : isWordResume(resume) ? (
                  <div className="flex min-h-[420px] flex-col items-center justify-center bg-[#2b2b2b] px-6 text-center sm:min-h-[450px] lg:min-h-[520px]">
                    <FileText className="mx-auto h-12 w-12 text-white/50" />
                    <p className="mt-3 text-sm text-white/70 font-semibold">
                      Resume is in Word format and cannot be previewed here.
                    </p>
                    <p className="mt-2 text-xs text-white/70">
                      Please download the file to view it locally.
                    </p>
                  </div>
                ) : (
                  <div className="flex min-h-[420px] items-center justify-center bg-[#2b2b2b] sm:min-h-[450px] lg:min-h-[520px]">
                    <div className="text-center">
                      <FileText className="mx-auto h-12 w-12 text-white/50" />
                      <p className="mt-3 text-sm text-white/70">
                        {resume.status === "completed"
                          ? "Resume preview is available above."
                          : rBadge.label}
                      </p>
                      {resume.status === "failed" && resume.processing?.lastError && (
                        <p className="mt-1 text-xs text-red-400">
                          Error: {resume.processing.lastError}
                        </p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>

            <AtsInsightsPanel
              candidate={candidate}
              candidateId={candidateId}
              atsData={atsData}
              loading={atsLoading}
              error={atsError}
            />

            {/* Needs Review banner */}
            {proc.needsReview && (
              <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
                <p className="text-sm font-semibold text-amber-700">
                  ⚠ Needs Manual Review
                </p>
                <p className="mt-1 text-xs text-amber-600">
                  The parser had low confidence on this candidate. Please verify
                  the screening data manually before shortlisting.
                </p>
              </div>
            )}

            {/* Timestamps */}
            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
              <div className="flex gap-6 text-xs text-slate-500">
                <p>
                  <span className="font-medium text-slate-700">Created: </span>
                  {candidate?.createdAt
                    ? new Date(candidate.createdAt).toLocaleString("en-IN")
                    : "—"}
                </p>
                <p>
                  <span className="font-medium text-slate-700">Updated: </span>
                  {candidate?.updatedAt
                    ? new Date(candidate.updatedAt).toLocaleString("en-IN")
                    : "—"}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* FULLSCREEN PREVIEW OVERLAY */}
      {isPreviewFullscreen && resumePreviewUrl && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/75 backdrop-blur-sm p-4 md:p-6 transition-all duration-300">
          <div className="relative flex h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl transition-all duration-300 animate-in fade-in zoom-in-95 duration-200">
            {/* Modal Header */}
            <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-5 py-3">
              <div className="flex items-center gap-3">
                <FileText className="h-5 w-5 text-slate-500" />
                <div>
                  <h3 className="text-sm font-semibold text-slate-800">
                    {resume.original?.filename || "Resume Preview"}
                  </h3>
                  {resume.original?.sizeBytes != null && (
                    <p className="text-[10px] text-slate-500">
                      ({(resume.original.sizeBytes / 1024).toFixed(0)} KB)
                    </p>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2">
                <a
                  href={resumePreviewUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
                  title="Open resume in a new browser tab"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Open in New Tab</span>
                </a>
                <button
                  onClick={() => setIsPreviewFullscreen(false)}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 transition-colors"
                  title="Close fullscreen"
                >
                  <Minimize2 className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Exit Fullscreen</span>
                </button>
                <button
                  onClick={() => setIsPreviewFullscreen(false)}
                  className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600 transition-colors"
                  title="Close"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>

            {/* Modal Body */}
            <div className="flex-1 bg-[#2b2b2b]">
              <iframe
                src={resumePreviewUrl}
                className="h-full w-full border-0"
                title="Fullscreen Resume Preview"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default CandidateDetails;