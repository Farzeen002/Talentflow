import { useEffect, useMemo, useState } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import {
  ArrowLeft,
  MapPin,
  Briefcase,
  Download,
  FileText,
  AlertCircle,
  Maximize2,
  Minimize2,
  ExternalLink,
  X,
  Ban,
  Loader2,
} from "lucide-react";
import { toast } from "sonner";
import AtsScoreBadge from "../components/AtsScoreBadge";
import AtsInsightsPanel from "../components/AtsInsightsPanel";
import {
  getMockCandidateById,
  mockToDetailShape,
  buildMockResumeHtml,
  recruiterChipClass,
} from "../lib/centralizedCandidatesMock";
import {
  saveCentralizedListState,
  loadCentralizedListState,
} from "../lib/centralizedListState";

const formatProfileCtc = (rupees) =>
  rupees != null ? `₹${(rupees / 100000).toFixed(1)}L` : "—";

const formatLpa = (lpa) => (lpa != null ? `${lpa} LPA` : "—");

const formatBool = (val) => {
  if (val === null || val === undefined) return "Not answered";
  return val ? "Yes" : "No";
};

const RESUME_BADGE = {
  completed: {
    label: "Available",
    cls: "bg-green-100 text-green-700 border-green-200",
  },
};

/**
 * Mock candidate profile — same structure/layout as real CandidateDetails.
 */
const MockCandidateDetails = () => {
  const { candidateId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();

  const raw = getMockCandidateById(candidateId);
  const candidate = mockToDetailShape(raw);

  const [isPreviewFullscreen, setIsPreviewFullscreen] = useState(false);
  const [isShortlisted, setIsShortlisted] = useState(false);
  const [resumeDownloadLoading, setResumeDownloadLoading] = useState(false);

  const resumePreviewUrl = useMemo(() => {
    if (!raw) return null;
    const html = buildMockResumeHtml(raw);
    return URL.createObjectURL(new Blob([html], { type: "text/html" }));
  }, [raw]);

  useEffect(() => {
    return () => {
      if (resumePreviewUrl) URL.revokeObjectURL(resumePreviewUrl);
    };
  }, [resumePreviewUrl]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") setIsPreviewFullscreen(false);
    };
    if (isPreviewFullscreen) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isPreviewFullscreen]);

  const handleNavigateBack = () => {
    const saved = loadCentralizedListState();
    const restore = {
      ...(location.state?.centralizedRestore ?? {}),
      ...(saved
        ? {
            tab: saved.tab,
            search: saved.search,
            recruiterFilter: saved.recruiterFilter,
            scrollY: saved.scrollY,
          }
        : {}),
      lastViewedCandidateId: candidateId,
      restoreList: true,
    };
    saveCentralizedListState({
      ...restore,
      candidateId,
      pendingRestore: true,
    });
    navigate("/centralized-candidates", { state: restore });
  };

  const handleDownloadResume = () => {
    if (!resumePreviewUrl || !raw) return;
    setResumeDownloadLoading(true);
    try {
      const link = document.createElement("a");
      link.href = resumePreviewUrl;
      link.download = `${raw.name.replace(/\s+/g, "_")}_Resume.html`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      toast.success("Resume downloaded successfully!");
    } finally {
      setResumeDownloadLoading(false);
    }
  };

  if (!candidate) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[#f6f7fb] px-4">
        <AlertCircle className="h-10 w-10 text-red-400" />
        <p className="text-center text-red-600">Candidate not found.</p>
        <button
          type="button"
          onClick={() => navigate("/centralized-candidates")}
          className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50"
        >
          Back to Centralized
        </button>
      </div>
    );
  }

  const meta = candidate.metadata ?? {};
  const qa = candidate.qa ?? {};
  const skills = candidate.skills?.raw ?? [];
  const resume = candidate.resume ?? {};
  const candidateJobId = candidate.jobId;
  const rBadge = RESUME_BADGE.completed;

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
      value:
        qa.noticePeriodDays != null
          ? `${qa.noticePeriodDays} days`
          : "Not answered",
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
      value:
        qa.experienceRunManagementYears != null
          ? `${qa.experienceRunManagementYears} yrs`
          : "Not answered",
      passFn: null,
    },
    {
      label: "Service Delivery Exp.",
      value:
        qa.experienceServiceDeliveryYears != null
          ? `${qa.experienceServiceDeliveryYears} yrs`
          : "Not answered",
      passFn: null,
    },
  ];

  const initials = meta.name
    ? meta.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2)
    : "?";

  const mockAtsData = {
    status: "completed",
    scoredAt: candidate.createdAt,
    jdAnalysisVersion: 1,
    isStale: false,
    scoreBreakdown: {
      finalScore: candidate.atsScore ?? 75,
      criticalRatio: 0.8,
      experienceYears: meta.experienceYears,
      jdMinExp: 3,
      matchedCritical: 4,
      totalCritical: 5,
      matchedSkills: skills.slice(0, Math.max(1, skills.length - 1)),
      missingSkills: skills.length ? [skills[skills.length - 1]] : [],
    },
  };

  return (
    <div className="flex h-[calc(100dvh-4rem-4.5rem)] flex-col overflow-hidden bg-[#f6f7fb] px-3 py-3 sm:px-6 sm:py-4 lg:h-[100dvh] lg:px-8">
      {/* PAGE HEADER */}
      <div className="mb-3 flex shrink-0 flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={handleNavigateBack}
          className="rounded-lg border border-slate-200 bg-white p-2.5 hover:bg-slate-50"
          aria-label="Back"
        >
          <ArrowLeft className="h-4 w-4 text-slate-700" />
        </button>
        <h1 className="text-sm font-semibold text-slate-800">
          Candidate Profile
        </h1>
        <span
          className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${recruiterChipClass(candidate.recruiterName)}`}
        >
          Added by {candidate.recruiterName}
        </span>
      </div>

      <div className="mx-auto flex min-h-0 w-full max-w-screen-2xl flex-1 flex-col gap-3 overflow-hidden sm:gap-4">
        {/* TOP CARD — profile header */}
        <div className="w-full shrink-0 rounded-2xl border border-slate-200 bg-white p-3 shadow-sm sm:p-4">
          <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
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
                <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                  {candidateJobId && <span>Job: {candidateJobId}</span>}
                </div>
              </div>
            </div>

            <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:flex-wrap sm:items-center">
              <button
                type="button"
                onClick={handleDownloadResume}
                disabled={resumeDownloadLoading}
                className="col-span-2 flex items-center justify-center gap-1.5 rounded-lg bg-[#111827] px-3 py-2.5 text-xs font-medium text-white hover:bg-black disabled:cursor-not-allowed disabled:opacity-50 sm:col-span-1 sm:px-4 sm:py-2"
              >
                {resumeDownloadLoading ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />{" "}
                    Downloading...
                  </>
                ) : (
                  <>
                    <Download className="h-3.5 w-3.5" /> Download Resume
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsShortlisted((v) => !v);
                  toast.success(
                    isShortlisted
                      ? "Removed from shortlist"
                      : "Added to shortlist"
                  );
                }}
                className={`rounded-lg border px-4 py-2 text-xs font-medium transition ${
                  isShortlisted
                    ? "border-amber-300 bg-amber-50 text-amber-800 hover:bg-amber-100"
                    : "border-slate-300 bg-white text-slate-700 hover:bg-slate-50"
                }`}
              >
                {isShortlisted ? "★ Shortlisted" : "☆ Shortlist"}
              </button>
              <button
                type="button"
                onClick={() =>
                  toast.info("Blacklist is not available for this candidate.")
                }
                className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-xs font-medium text-red-700 hover:bg-red-100"
              >
                <Ban className="h-3.5 w-3.5" />
                Blacklist
              </button>
              <AtsScoreBadge score={candidate.atsScore} size="md" />
            </div>
          </div>
        </div>

        {/* CONTENT GRID */}
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto sm:gap-4 lg:grid-cols-[48%_52%] lg:overflow-hidden xl:grid-cols-[45%_55%]">
          {/* LEFT */}
          <div className="min-h-0 space-y-3 overflow-visible pb-2 lg:overflow-y-auto lg:overscroll-contain lg:pb-4 lg:pr-1">
            <div className="rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="border-b border-slate-100 px-4 py-3">
                <h3 className="text-sm font-semibold text-slate-900">
                  Contact Information
                </h3>
              </div>

              <div className="border-t border-slate-100 px-4 py-4">
                <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
                  Experience Details
                </h3>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {[
                    [
                      "Total Exp.",
                      meta.experienceYears != null
                        ? `${meta.experienceYears} yrs`
                        : "—",
                    ],
                    ["Current Role", meta.currentRole ?? "—"],
                    ["Company", meta.currentCompany ?? "—"],
                    ["Job Title", meta.jobTitle ?? "—"],
                    [
                      "Notice (Profile)",
                      meta.profileNoticeDays != null
                        ? `${meta.profileNoticeDays}d`
                        : "—",
                    ],
                    ["CTC (Profile)", formatProfileCtc(meta.profileCtcRupees)],
                  ].map(([label, value]) => (
                    <div
                      key={label}
                      className="rounded-xl border border-slate-200 bg-slate-50 p-3"
                    >
                      <p className="text-[10px] text-slate-500">{label}</p>
                      <p className="mt-1 text-sm font-medium text-slate-800">
                        {value}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

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
                  Note: Profile CTC (₹ rupees) and Q&A CTC (LPA) come from
                  different sources.
                </p>
              </div>
            </div>

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
                            className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${
                              passed
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

            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <h3 className="text-sm font-semibold text-slate-900">Skills</h3>
              <p className="mt-0.5 text-xs text-slate-500">
                {skills.length > 0
                  ? "Extracted from resume/profile"
                  : "No skills extracted yet"}
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

          {/* RIGHT */}
          <div className="min-h-0 space-y-3 overflow-visible pb-2 lg:overflow-y-auto lg:overscroll-contain lg:pb-4 lg:pr-1">
            <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
              <div className="mb-3 flex items-center justify-between">
                <div>
                  <h3 className="text-sm font-semibold text-slate-900">
                    Resume Review
                  </h3>
                  <div className="mt-1 flex items-center gap-2">
                    <span
                      className={`rounded-full border px-2 py-0.5 text-xs font-semibold ${rBadge.cls}`}
                    >
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
                  {resumePreviewUrl && (
                    <>
                      <a
                        href={resumePreviewUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50"
                        title="Open resume in a new browser tab"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Open in New Tab</span>
                      </a>
                      <button
                        type="button"
                        onClick={() => setIsPreviewFullscreen(true)}
                        className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50"
                        title="View fullscreen"
                      >
                        <Maximize2 className="h-3.5 w-3.5" />
                        <span className="hidden sm:inline">Fullscreen</span>
                      </button>
                    </>
                  )}
                  <button
                    type="button"
                    onClick={handleDownloadResume}
                    disabled={resumeDownloadLoading}
                    className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {resumeDownloadLoading ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />{" "}
                        Downloading...
                      </>
                    ) : (
                      <>
                        <Download className="h-3.5 w-3.5" /> Download
                      </>
                    )}
                  </button>
                </div>
              </div>

              <div className="overflow-hidden rounded-xl border border-slate-200 shadow-sm transition-all duration-200 hover:shadow-md">
                {resumePreviewUrl ? (
                  <iframe
                    src={resumePreviewUrl}
                    className="min-h-[420px] w-full border-0 transition-opacity duration-300 sm:min-h-[450px] lg:min-h-[520px]"
                    title="Resume Preview"
                  />
                ) : (
                  <div className="flex min-h-[420px] flex-col items-center justify-center bg-[#2b2b2b] px-6 text-center sm:min-h-[450px] lg:min-h-[520px]">
                    <FileText className="mx-auto h-12 w-12 text-white/50" />
                    <p className="mt-3 text-sm font-semibold text-white/70">
                      Unable to preview resume inline.
                    </p>
                  </div>
                )}
              </div>
            </div>

            <AtsInsightsPanel
              candidate={candidate}
              candidateId={candidateId}
              atsData={mockAtsData}
              loading={false}
              error={null}
            />

            <div className="rounded-2xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
              <div className="flex gap-6 text-xs text-slate-500">
                <p>
                  <span className="font-medium text-slate-700">Created: </span>
                  {candidate.createdAt
                    ? new Date(candidate.createdAt).toLocaleString("en-IN")
                    : "—"}
                </p>
                <p>
                  <span className="font-medium text-slate-700">Updated: </span>
                  {candidate.updatedAt
                    ? new Date(candidate.updatedAt).toLocaleString("en-IN")
                    : "—"}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>

      {isPreviewFullscreen && resumePreviewUrl && (
        <div className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-black/75 p-4 backdrop-blur-sm md:p-6">
          <div className="relative flex h-[92vh] w-full max-w-5xl flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl">
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
                  className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Open in New Tab</span>
                </a>
                <button
                  type="button"
                  onClick={() => setIsPreviewFullscreen(false)}
                  className="flex items-center gap-1.5 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                >
                  <Minimize2 className="h-3.5 w-3.5" />
                  <span className="hidden sm:inline">Exit Fullscreen</span>
                </button>
                <button
                  type="button"
                  onClick={() => setIsPreviewFullscreen(false)}
                  className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-600"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            </div>
            <div className="flex-1 bg-[#2b2b2b]">
              <iframe
                src={resumePreviewUrl}
                className="h-full w-full border-0"
                title="Resume Fullscreen"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default MockCandidateDetails;
