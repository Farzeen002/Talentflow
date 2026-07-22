/**
 * Daily Reports — Phase 1 schema helpers
 * reportKind: recruiter | lead
 * User role from /auth/me locks which kind they can use (not both).
 */

export const REPORT_KINDS = [
  {
    id: "recruiter",
    label: "Recruiter",
    description: "Daily candidate submission rows",
  },
  {
    id: "lead",
    label: "Lead Recruiter",
    description: "Daily summary, metrics & plans",
  },
];

/**
 * Map /auth/me role → reportKind.
 * A person is either recruiter or lead — never both in the UI.
 * Accepts: recruiter | team_lead | lead | teamLead
 */
export function reportKindFromRole(role) {
  const r = String(role ?? "")
    .trim()
    .toLowerCase()
    .replace(/-/g, "_");
  if (r === "team_lead" || r === "lead" || r === "teamlead") return "lead";
  if (r === "recruiter") return "recruiter";
  return null;
}

export function kindLabel(kind) {
  return REPORT_KINDS.find((k) => k.id === kind)?.label ?? kind;
}

/** Asia/Kolkata business date YYYY-MM-DD */
export function todayIsoDate() {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

/** Lookback: today + up to 2 previous calendar days (Asia/Kolkata) */
export function minReportDate() {
  const today = todayIsoDate();
  const d = new Date(`${today}T12:00:00+05:30`);
  d.setDate(d.getDate() - 2);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

export const SUBMISSION_STATUS_OPTIONS = [
  { value: "submitted", label: "Submitted" },
  { value: "on_hold", label: "On Hold" },
  { value: "rejected", label: "Rejected" },
  { value: "client_review", label: "Client Review" },
  { value: "interview_scheduled", label: "Interview Scheduled" },
  { value: "offer_released", label: "Offer Released" },
  { value: "joined", label: "Joined" },
];

export const RECRUITER_ENTRY_COLUMNS = [
  { key: "jobId", label: "Job ID", required: true },
  { key: "candidateName", label: "Candidate Name", required: true },
  { key: "jobName", label: "Job Name", required: true },
  { key: "candidateContactNumber", label: "Contact", required: true },
  { key: "candidateEmail", label: "Email", required: true, type: "email" },
  { key: "poc", label: "POC", required: true },
  { key: "client", label: "Client", required: true },
  { key: "submissionStatus", label: "Status", required: true, type: "status" },
  { key: "remarks", label: "Remarks", required: false },
];

export const EMPTY_ENTRY_DRAFT = () => ({
  jobId: "",
  candidateName: "",
  jobName: "",
  candidateContactNumber: "",
  candidateEmail: "",
  poc: "",
  client: "",
  submissionStatus: "",
  remarks: "",
});

export const LEAD_METRIC_SECTIONS = [
  {
    key: "recruitmentSummary",
    title: "Recruitment summary",
    fields: [{ key: "requirementsManaged", label: "Requirements managed" }],
  },
  {
    key: "teamProfileReview",
    title: "Team profile review",
    fields: [
      { key: "profilesReceived", label: "Profiles received" },
      { key: "profilesApproved", label: "Profiles approved" },
      { key: "profilesRejected", label: "Profiles rejected" },
    ],
  },
  {
    key: "leadRecruitmentDelivery",
    title: "Lead recruitment delivery",
    fields: [
      { key: "profilesSubmitted", label: "Profiles submitted" },
      { key: "interviews", label: "Interviews" },
      { key: "offers", label: "Offers" },
      { key: "joinings", label: "Joinings" },
    ],
  },
];

const KIND_STORAGE_KEY = "talentfloww_daily_report_kind";

/** Saved Recruiter/Lead choice, or null if user has not chosen yet */
export function loadSavedKind() {
  try {
    const v = localStorage.getItem(KIND_STORAGE_KEY);
    if (v === "recruiter" || v === "lead") return v;
  } catch {
    /* ignore */
  }
  return null;
}

export function saveKind(kind) {
  try {
    if (kind === "recruiter" || kind === "lead") {
      localStorage.setItem(KIND_STORAGE_KEY, kind);
    }
  } catch {
    /* ignore */
  }
}

export function clearSavedKind() {
  try {
    localStorage.removeItem(KIND_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function isDraft(report) {
  return report?.status === "draft";
}

export function isFailed(report) {
  return report?.status === "failed";
}

export function isSent(report) {
  return report?.status === "sent";
}

export function canEditReport(report) {
  return isDraft(report);
}

export function displayRecipients(report) {
  if (!report) return { to: [], cc: [] };
  if (report.status !== "draft" && report.recipientsSnapshot) {
    return report.recipientsSnapshot;
  }
  return report.recipients ?? { to: [], cc: [] };
}

/** Client-side completeness before submit (server still validates) */
export function recruiterReadyToSubmit(report) {
  const entries = report?.payload?.entries ?? [];
  if (entries.length < 1) return { ok: false, message: "Add at least one candidate entry." };
  const required = [
    "jobId",
    "candidateName",
    "jobName",
    "candidateContactNumber",
    "candidateEmail",
    "poc",
    "client",
    "submissionStatus",
  ];
  for (const [i, e] of entries.entries()) {
    for (const k of required) {
      if (e[k] == null || String(e[k]).trim() === "") {
        return {
          ok: false,
          message: `Entry ${i + 1}: fill all required fields before submit.`,
        };
      }
    }
  }
  const to = report?.recipients?.to ?? [];
  if (!to.length) return { ok: false, message: "Add at least one To recipient." };
  return { ok: true };
}

export function leadReadyToSubmit(report) {
  const p = report?.payload ?? {};
  const metrics = [
    p.recruitmentSummary?.requirementsManaged,
    p.teamProfileReview?.profilesReceived,
    p.teamProfileReview?.profilesApproved,
    p.teamProfileReview?.profilesRejected,
    p.leadRecruitmentDelivery?.profilesSubmitted,
    p.leadRecruitmentDelivery?.interviews,
    p.leadRecruitmentDelivery?.offers,
    p.leadRecruitmentDelivery?.joinings,
  ];
  if (metrics.some((m) => m == null || m === "")) {
    return {
      ok: false,
      message: "Enter all metrics (use 0 if none today).",
    };
  }
  if ((p.keyActivities ?? []).length < 1) {
    return { ok: false, message: "Add at least one key activity." };
  }
  if ((p.planForTomorrow ?? []).length < 1) {
    return { ok: false, message: "Add at least one plan-for-tomorrow item." };
  }
  const to = report?.recipients?.to ?? [];
  if (!to.length) return { ok: false, message: "Add at least one To recipient." };
  return { ok: true };
}

export function statusBadgeClass(status) {
  if (status === "sent") return "bg-emerald-50 text-emerald-800 border-emerald-200";
  if (status === "failed") return "bg-red-50 text-red-800 border-red-200";
  return "bg-amber-50 text-amber-800 border-amber-200";
}

export function statusLabel(status) {
  if (status === "sent") return "Sent";
  if (status === "failed") return "Failed";
  return "Draft";
}

export function submissionStatusLabel(value) {
  return (
    SUBMISSION_STATUS_OPTIONS.find((o) => o.value === value)?.label ?? value ?? "—"
  );
}

/** Normalize empty string → omit or null for API patches */
export function entryPayloadFromForm(form) {
  const out = {};
  for (const [k, v] of Object.entries(form)) {
    if (v === "" || v == null) continue;
    out[k] = typeof v === "string" ? v.trim() : v;
  }
  return out;
}

export function parseMetricInput(raw) {
  if (raw === "" || raw == null) return null;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.floor(n);
}
