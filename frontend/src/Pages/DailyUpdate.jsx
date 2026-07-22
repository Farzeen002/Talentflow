import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import {
  Loader2,
  AlertCircle,
  CheckCircle2,
  RefreshCw,
  Send,
  Briefcase,
  Mail,
  History,
  Pencil,
  X,
} from "lucide-react";
import { getMe } from "../services/auth";
import {
  openReport,
  updateRecipients,
  submitReport,
  resendReport,
  listReports,
  getReport,
  getReportDefaults,
} from "../services/dailyUpdates";
import {
  REPORT_KINDS,
  todayIsoDate,
  minReportDate,
  saveKind,
  kindLabel,
  canEditReport,
  isFailed,
  isSent,
  isDraft,
  displayRecipients,
  recruiterReadyToSubmit,
  leadReadyToSubmit,
  statusBadgeClass,
  statusLabel,
} from "../lib/dailyUpdateSchema";
import RecruiterReportForm from "../components/dailyUpdate/RecruiterReportForm";
import LeadReportForm from "../components/dailyUpdate/LeadReportForm";
import EmailChipsInput, {
  parseEmailList,
} from "../components/dailyUpdate/EmailChipsInput";

/** Normalize GET /reports/defaults body → { to, cc } string arrays */
function normalizeDefaults(data) {
  if (!data || typeof data !== "object") return { to: [], cc: [] };
  const toRaw =
    data.to ??
    data.defaultTo ??
    data.default_to ??
    data.recipients?.to ??
    [];
  const ccRaw =
    data.cc ??
    data.defaultCc ??
    data.default_cc ??
    data.recipients?.cc ??
    [];
  return {
    to: parseEmailList(toRaw),
    cc: parseEmailList(ccRaw),
  };
}

const DailyUpdate = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [reportKind, setReportKind] = useState(null);
  const [kindChosen, setKindChosen] = useState(false);
  /** Once a draft/sent exists for the day, type cannot be switched */
  const [kindLocked, setKindLocked] = useState(false);
  const [booting, setBooting] = useState(true);
  const [reportDate, setReportDate] = useState(todayIsoDate());
  const [report, setReport] = useState(null);
  const [user, setUser] = useState(null);
  const [opening, setOpening] = useState(false);
  const [alreadySent, setAlreadySent] = useState(false);
  const [checkingSent, setCheckingSent] = useState(false);
  const skipOpenRef = useRef(false);
  /** Keeps a History → View/Continue report from being cleared by boot/sync */
  const historyHydrateRef = useRef(false);
  const [submitting, setSubmitting] = useState(false);
  const [resending, setResending] = useState(false);
  const [recipientsSaving, setRecipientsSaving] = useState(false);
  const [recipientsEditing, setRecipientsEditing] = useState(false);
  const [toEmails, setToEmails] = useState([]);
  const [ccEmails, setCcEmails] = useState([]);
  const [defaultsLoading, setDefaultsLoading] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const reportRef = useRef(null);
  reportRef.current = report;

  const editable = canEditReport(report);

  useEffect(() => {
    getMe()
      .then((res) => setUser(res.data ?? res))
      .catch(() => setUser(null));
  }, []);

  const applyReport = useCallback((data, { keepRecipientsIfEmpty = false } = {}) => {
    setReport(data);
    setAlreadySent(data?.status === "sent");
    const r = displayRecipients(data);
    const to = parseEmailList(r.to);
    const cc = parseEmailList(r.cc);
    if (to.length || cc.length) {
      setToEmails(to);
      setCcEmails(cc);
    } else if (!keepRecipientsIfEmpty) {
      setToEmails([]);
      setCcEmails([]);
    }
    // keepRecipientsIfEmpty: leave current chips (e.g. server defaults) alone
    setRecipientsEditing(false);
  }, []);

  const loadKindDefaults = useCallback(async (kind) => {
    if (!kind) {
      setToEmails([]);
      setCcEmails([]);
      return;
    }
    setDefaultsLoading(true);
    try {
      const { data } = await getReportDefaults(kind);
      const normalized = normalizeDefaults(data);
      setToEmails(normalized.to);
      setCcEmails(normalized.cc);
    } catch {
      setToEmails([]);
      setCcEmails([]);
    } finally {
      setDefaultsLoading(false);
    }
  }, []);

  // When kind is chosen but no report yet — show server defaults as chips
  useEffect(() => {
    if (!kindChosen || !reportKind || report?.reportId || alreadySent) return;
    loadKindDefaults(reportKind);
  }, [
    kindChosen,
    reportKind,
    report?.reportId,
    alreadySent,
    loadKindDefaults,
  ]);

  /** Open or create draft — only when user starts working (or loads from history). */
  const ensureDraft = useCallback(
    async (date = reportDate, kind = reportKind) => {
      if (!kind) {
        toast.error("Choose Recruiter or Lead Recruiter first.");
        return null;
      }
      if (alreadySent) {
        toast.info(
          "Email already sent for this date. Open Report History to view it."
        );
        return null;
      }
      const existing = reportRef.current;
      if (existing?.reportId && existing.reportKind === kind) {
        if (existing.status === "sent") {
          setAlreadySent(true);
          setReport(null);
          return null;
        }
        return existing;
      }

      setOpening(true);
      try {
        const { data } = await openReport(date, kind);
        if (data.status === "sent") {
          setReport(null);
          setAlreadySent(true);
          return null;
        }
        applyReport(data, { keepRecipientsIfEmpty: true });
        saveKind(kind);
        setKindLocked(true);
        return data;
      } catch (err) {
        const status = err?.response?.status;
        if (status === 409) {
          setReport(null);
          setAlreadySent(true);
        } else if (status === 422) {
          const detail = err?.response?.data?.detail;
          toast.error(
            typeof detail === "string"
              ? detail
              : "Invalid date or report kind."
          );
          setReport(null);
        } else {
          setReport(null);
        }
        return null;
      } finally {
        setOpening(false);
      }
    },
    [reportDate, reportKind, alreadySent, applyReport]
  );

  // First open: let user choose. If today already has draft/sent → lock that type.
  useEffect(() => {
    if (location.state?.report || historyHydrateRef.current) {
      setBooting(false);
      return;
    }
    let cancelled = false;
    (async () => {
      const today = todayIsoDate();
      try {
        const { data } = await listReports({
          reportDate: today,
          page: 1,
          limit: 40,
        });
        if (cancelled) return;
        const items = data.items ?? [];

        const resumable = items.find(
          (i) => i.status === "draft" || i.status === "failed"
        );
        if (resumable) {
          setReportDate(today);
          setReportKind(resumable.reportKind);
          setKindChosen(true);
          setKindLocked(true);
          saveKind(resumable.reportKind);
          const { data: full } = await getReport(resumable.reportId);
          if (!cancelled) applyReport(full);
          return;
        }

        const sent = items.find((i) => i.status === "sent");
        if (sent) {
          setReportDate(today);
          setReportKind(sent.reportKind);
          setKindChosen(true);
          setKindLocked(true);
          setAlreadySent(true);
          setReport(null);
          saveKind(sent.reportKind);
          return;
        }

        // Nothing for today — leave both options unlocked for first-time choice
        setKindChosen(false);
        setReportKind(null);
        setKindLocked(false);
        setAlreadySent(false);
        setReport(null);
      } catch {
        /* ignore — empty first-visit state */
      } finally {
        if (!cancelled) setBooting(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applyReport, location.state]);

  // When kind + date change: block if sent, otherwise resume draft/failed
  useEffect(() => {
    if (booting) return;
    if (!kindChosen || !reportKind || !reportDate) return;
    let cancelled = false;
    (async () => {
      setCheckingSent(true);
      try {
        const { data } = await listReports({
          reportDate,
          reportKind,
          page: 1,
          limit: 20,
        });
        if (cancelled) return;
        const items = data.items ?? [];
        const sent = items.find((i) => i.status === "sent");
        if (sent) {
          setAlreadySent(true);
          setKindLocked(true);
          setRecipientsEditing(false);
          // Keep a sent report on screen if user opened it via History → View
          const current = reportRef.current;
          if (
            historyHydrateRef.current ||
            (current?.status === "sent" &&
              current.reportDate === reportDate &&
              current.reportKind === reportKind)
          ) {
            if (!current?.reportId && sent.reportId) {
              const { data: full } = await getReport(sent.reportId);
              if (!cancelled) applyReport(full);
            }
            return;
          }
          setReport(null);
          return;
        }
        setAlreadySent(false);

        const current = reportRef.current;
        if (
          current?.reportId &&
          current.reportDate === reportDate &&
          current.reportKind === reportKind &&
          current.status !== "sent"
        ) {
          setKindLocked(true);
          return;
        }

        const resumable = items.find(
          (i) => i.status === "draft" || i.status === "failed"
        );
        if (resumable) {
          const { data: full } = await getReport(resumable.reportId);
          if (!cancelled) {
            applyReport(full);
            setKindLocked(true);
          }
        } else if (
          !current ||
          current.reportDate !== reportDate ||
          current.reportKind !== reportKind
        ) {
          setReport(null);
          // No existing work for this date+kind — allow switching until they start
          if (!kindLocked || current?.reportKind !== reportKind) {
            setKindLocked(false);
          }
        }
      } catch {
        if (!cancelled) setAlreadySent(false);
      } finally {
        if (!cancelled) setCheckingSent(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [booting, kindChosen, reportKind, reportDate, applyReport]); // eslint-disable-line react-hooks/exhaustive-deps

  // Hydrate from Report History (Continue / View)
  useEffect(() => {
    const incoming = location.state?.report;
    if (!incoming?.reportId) return;
    if (skipOpenRef.current) {
      skipOpenRef.current = false;
      return;
    }
    historyHydrateRef.current = true;
    setReportKind(incoming.reportKind);
    setKindChosen(true);
    setKindLocked(true);
    saveKind(incoming.reportKind);
    setReportDate(incoming.reportDate);
    setBooting(false);
    // Always show the report (including sent) — View from history is read-only
    applyReport(incoming);
    navigate(location.pathname, { replace: true, state: {} });
  }, [location.state, location.pathname, applyReport, navigate]);

  const handleKindChoice = (kind) => {
    if (kindLocked) {
      toast.info(
        `Report type is locked to ${kindLabel(reportKind)} because work already exists for this day.`
      );
      return;
    }
    if (kind === reportKind && kindChosen) return;
    historyHydrateRef.current = false;
    setReportKind(kind);
    setKindChosen(true);
    saveKind(kind);
    setReport(null);
    setAlreadySent(false);
    setRecipientsEditing(false);
    setToEmails([]);
    setCcEmails([]);
  };

  const handleDateChange = (value) => {
    historyHydrateRef.current = false;
    setReportDate(value);
    setReport(null);
    setAlreadySent(false);
    setKindLocked(false);
    setRecipientsEditing(false);
    setToEmails([]);
    setCcEmails([]);
  };

  const startEditRecipients = async () => {
    if (!editable && report && !isDraft(report)) return;
    // Snapshot chips before ensureDraft so empty draft recipients don't wipe defaults
    const previousTo = [...toEmails];
    const previousCc = [...ccEmails];

    const draft = report?.reportId ? report : await ensureDraft();
    if (!draft || draft.status !== "draft") return;

    const r = displayRecipients(draft);
    const to = parseEmailList(r.to);
    const cc = parseEmailList(r.cc);
    if (to.length || cc.length) {
      setToEmails(to);
      setCcEmails(cc);
    } else {
      setToEmails(previousTo);
      setCcEmails(previousCc);
    }
    setRecipientsEditing(true);
  };

  const cancelEditRecipients = () => {
    const r = displayRecipients(report);
    const to = parseEmailList(r?.to);
    const cc = parseEmailList(r?.cc);
    if (report?.reportId && (to.length || cc.length)) {
      setToEmails(to);
      setCcEmails(cc);
    } else if (reportKind) {
      loadKindDefaults(reportKind);
    } else {
      setToEmails([]);
      setCcEmails([]);
    }
    setRecipientsEditing(false);
  };

  const saveRecipients = async () => {
    const draft = report?.reportId ? report : await ensureDraft();
    if (!draft?.reportId || draft.status !== "draft") return;
    setRecipientsSaving(true);
    try {
      const { data } = await updateRecipients(draft.reportId, {
        to: parseEmailList(toEmails),
        cc: parseEmailList(ccEmails),
      });
      applyReport(data);
      setRecipientsEditing(false);
      toast.success("Recipients saved");
    } catch (err) {
      const detail = err?.response?.data?.detail;
      toast.error(
        typeof detail === "string" ? detail : "Failed to update recipients"
      );
    } finally {
      setRecipientsSaving(false);
    }
  };

  const handleSubmit = async () => {
    if (!report?.reportId) return;
    const check =
      report.reportKind === "lead"
        ? leadReadyToSubmit(report)
        : recruiterReadyToSubmit(report);
    if (!check.ok) {
      toast.error(check.message);
      setConfirmOpen(false);
      return;
    }

    setSubmitting(true);
    try {
      const { data } = await submitReport(report.reportId);
      setConfirmOpen(false);
      if (data.status === "sent") {
        toast.success("Report submitted and emailed.");
        setAlreadySent(true);
        setReport(null);
        setRecipientsEditing(false);
      } else if (data.status === "failed") {
        applyReport(data);
        toast.error(
          data.delivery?.lastError ||
            "Submitted but email failed. You can Resend."
        );
      } else {
        applyReport(data);
      }
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 422) {
        toast.error(
          typeof detail === "string"
            ? detail
            : "Validation failed — fix missing fields and try again."
        );
      } else if (status === 409) {
        toast.info("Report state changed — refreshing…");
        await ensureDraft();
      }
      setConfirmOpen(false);
    } finally {
      setSubmitting(false);
    }
  };

  const handleResend = async () => {
    if (!report?.reportId) return;
    setResending(true);
    try {
      const { data } = await resendReport(report.reportId);
      applyReport(data);
      if (data.status === "sent") toast.success("Email resent successfully.");
      else
        toast.error(
          data.delivery?.lastError || "Resend failed. Try again later."
        );
    } catch (err) {
      if (err?.response?.status === 409) {
        toast.info("Report is not in failed state — refreshing…");
        await ensureDraft();
      }
    } finally {
      setResending(false);
    }
  };

  const showEditorShell = kindChosen && !(alreadySent && !report);
  const hasDraft = Boolean(report?.reportId);

  return (
    <div className="flex flex-col gap-4 p-3 pb-10 sm:gap-6 sm:p-6 lg:p-8">
      <section className="rounded-2xl border border-slate-200 bg-white px-4 py-5 shadow-sm sm:rounded-3xl sm:px-6 sm:py-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-xl font-bold text-slate-900 sm:text-2xl">
              Daily Reports
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              First time: choose Recruiter or Lead. If you already started or
              sent a report for the day, that type stays locked.
            </p>
            {user && (
              <p className="mt-2 text-xs text-slate-500">
                Signed in as{" "}
                <span className="font-medium">{user.name}</span> · {user.email}
              </p>
            )}
          </div>
          <div className="flex shrink-0 flex-wrap gap-2">
            <button
              type="button"
              onClick={() => navigate("/daily-update/history")}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              <History className="h-4 w-4" />
              Report history
            </button>
            <button
              type="button"
              onClick={() => navigate("/jobs")}
              className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#0f2a3c]"
            >
              <Briefcase className="h-4 w-4" />
              View Jobs
            </button>
          </div>
        </div>

        <div className="mt-5">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
            Select report type
          </p>
          <div className="grid grid-cols-1 gap-2 sm:flex sm:flex-wrap">
            {REPORT_KINDS.map((kind) => {
              const active = kindChosen && reportKind === kind.id;
              const disabledOther = kindLocked && !active;
              return (
                <button
                  key={kind.id}
                  type="button"
                  disabled={disabledOther}
                  onClick={() => handleKindChoice(kind.id)}
                  className={`rounded-xl px-4 py-3 text-left transition sm:min-w-[12rem] ${
                    active
                      ? "bg-[#14344a] text-white shadow-md ring-2 ring-[#14344a]/30"
                      : disabledOther
                        ? "cursor-not-allowed border border-slate-100 bg-slate-50 text-slate-400 opacity-60"
                        : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"
                  }`}
                >
                  <span className="block text-sm font-semibold">{kind.label}</span>
                  <span
                    className={`mt-0.5 block text-[11px] ${
                      active
                        ? "text-slate-300"
                        : disabledOther
                          ? "text-slate-400"
                          : "text-slate-500"
                    }`}
                  >
                    {kind.description}
                  </span>
                  {active && (
                    <span className="mt-2 inline-block text-[10px] font-semibold uppercase tracking-wide text-[#5eead4]">
                      {kindLocked ? "Locked" : "Selected"}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          {kindLocked && (
            <p className="mt-2 text-xs text-slate-500">
              Type locked for this day because a draft or sent report already
              exists.
            </p>
          )}
        </div>

        {kindChosen && (
          <div className="mt-4 flex flex-wrap items-end gap-3">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-slate-600">
                Report date
              </span>
              <input
                type="date"
                value={reportDate}
                min={minReportDate()}
                max={todayIsoDate()}
                onChange={(e) => handleDateChange(e.target.value)}
                className="h-10 rounded-xl border border-slate-200 bg-white px-3 text-sm outline-none focus:border-[#14344a]/40"
              />
            </label>
            {hasDraft && (
              <button
                type="button"
                disabled={opening}
                onClick={() => ensureDraft()}
                className="inline-flex h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                {opening ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                Refresh
              </button>
            )}
          </div>
        )}
      </section>

      {booting ? (
        <div className="flex h-40 items-center justify-center gap-2 text-slate-600">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading daily reports…
        </div>
      ) : !kindChosen ? (
        <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-6 py-12 text-center">
          <p className="text-sm font-semibold text-slate-800">
            Choose Recruiter or Lead Recruiter
          </p>
          <p className="mt-1 text-xs text-slate-500">
            Pick one above to begin. A draft is created only after you start
            filling the form.
          </p>
        </div>
      ) : checkingSent && !report ? (
        <div className="flex h-40 items-center justify-center gap-2 text-slate-600">
          <Loader2 className="h-5 w-5 animate-spin" />
          Checking if a report was already sent…
        </div>
      ) : alreadySent && !report ? (
        <div className="rounded-2xl border border-amber-300 bg-amber-50 p-6 text-center sm:p-8">
          <AlertCircle className="mx-auto h-10 w-10 text-amber-500" />
          <p className="mt-3 text-base font-semibold text-amber-950">
            You have already sent the mail for {reportDate}
          </p>
          <p className="mx-auto mt-2 max-w-md text-sm text-amber-900/85">
            Your {kindLabel(reportKind)} report for this day was submitted and
            emailed. You cannot send it again. Check Report History to view what
            was sent.
          </p>
          <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
            <button
              type="button"
              onClick={() => navigate("/daily-update/history")}
              className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-5 py-2.5 text-sm font-semibold text-white hover:bg-[#0f2a3c]"
            >
              <History className="h-4 w-4" />
              Open Report History
            </button>
          </div>
        </div>
      ) : showEditorShell ? (
        <>
          {opening && (
            <div className="flex items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white py-6 text-sm text-slate-600">
              <Loader2 className="h-4 w-4 animate-spin" />
              Creating draft…
            </div>
          )}

          {hasDraft && (
            <div
              className={`flex flex-wrap items-start gap-3 rounded-2xl border px-4 py-3 sm:px-5 ${
                isSent(report)
                  ? "border-emerald-200 bg-emerald-50"
                  : isFailed(report)
                    ? "border-red-200 bg-red-50"
                    : "border-amber-200 bg-amber-50"
              }`}
            >
              {isSent(report) ? (
                <CheckCircle2 className="mt-0.5 h-5 w-5 text-emerald-700" />
              ) : isFailed(report) ? (
                <AlertCircle className="mt-0.5 h-5 w-5 text-red-700" />
              ) : (
                <Mail className="mt-0.5 h-5 w-5 text-amber-700" />
              )}
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span
                    className={`inline-flex rounded-full border px-2.5 py-0.5 text-xs font-semibold ${statusBadgeClass(report.status)}`}
                  >
                    {statusLabel(report.status)}
                  </span>
                  <span className="text-xs text-slate-600">
                    {report.reportDate} · {kindLabel(report.reportKind)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-slate-700">
                  {isDraft(report) &&
                    "Draft — not submitted. Edits save as you go."}
                  {isSent(report) &&
                    `Submitted & emailed${
                      report.delivery?.sentAt
                        ? ` · ${new Date(report.delivery.sentAt).toLocaleString("en-IN")}`
                        : ""
                    }. Read-only — you cannot send again.`}
                  {isFailed(report) &&
                    `Submitted but email failed: ${
                      report.delivery?.lastError || "Unknown error"
                    }`}
                </p>
              </div>
              {isSent(report) && (
                <button
                  type="button"
                  onClick={() => navigate("/daily-update/history")}
                  className="inline-flex shrink-0 items-center gap-1.5 rounded-xl border border-emerald-300 bg-white px-3 py-1.5 text-xs font-medium text-emerald-900 hover:bg-emerald-50"
                >
                  <History className="h-3.5 w-3.5" />
                  Back to history
                </button>
              )}
            </div>
          )}

          {!hasDraft && (
            <div className="rounded-2xl border border-dashed border-slate-300 bg-slate-50 px-4 py-3 text-sm text-slate-600">
              No draft yet for {kindLabel(reportKind)} · {reportDate}. Start
              typing in the form below to create one.
            </div>
          )}

          {/* Recipients — defaults from GET /reports/defaults; chips for edit */}
          <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">
                  Recipients
                </h3>
                <p className="mt-0.5 text-xs text-slate-500">
                  {isSent(report)
                    ? "Recipients used for the sent email (read-only)."
                    : "Click Edit to add or remove emails, then Save."}
                </p>
              </div>
              {(editable || !hasDraft) && !recipientsEditing && (
                <button
                  type="button"
                  disabled={opening || defaultsLoading}
                  onClick={startEditRecipients}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  <Pencil className="h-3.5 w-3.5" />
                  Edit
                </button>
              )}
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              {defaultsLoading && !recipientsEditing ? (
                <div className="col-span-full flex items-center gap-2 text-sm text-slate-500">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Loading recipient defaults…
                </div>
              ) : (
                <>
                  <EmailChipsInput
                    label="Recipient Emails"
                    emails={toEmails}
                    onChange={setToEmails}
                    disabled={!recipientsEditing}
                    placeholder="Type email and press Enter"
                  />
                  <EmailChipsInput
                    label="CC Emails"
                    emails={ccEmails}
                    onChange={setCcEmails}
                    disabled={!recipientsEditing}
                    placeholder="Type email and press Enter"
                  />
                </>
              )}
            </div>
            {recipientsEditing && (
              <div className="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  disabled={recipientsSaving}
                  onClick={saveRecipients}
                  className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2 text-sm font-semibold text-white hover:bg-[#0f2a3c] disabled:opacity-50"
                >
                  {recipientsSaving ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : null}
                  Save
                </button>
                <button
                  type="button"
                  disabled={recipientsSaving}
                  onClick={cancelEditRecipients}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  <X className="h-3.5 w-3.5" />
                  Cancel
                </button>
              </div>
            )}
          </section>

          {reportKind === "lead" ? (
            <LeadReportForm
              report={report}
              canEdit={!hasDraft || editable}
              ensureDraft={ensureDraft}
              onReportChange={applyReport}
            />
          ) : (
            <RecruiterReportForm
              report={report}
              canEdit={!hasDraft || editable}
              ensureDraft={ensureDraft}
              onReportChange={applyReport}
            />
          )}

          {hasDraft && (
            <div className="flex flex-wrap items-center justify-end gap-3">
              {editable && (
                <button
                  type="button"
                  onClick={() => setConfirmOpen(true)}
                  className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-6 py-2.5 text-sm font-semibold text-white hover:bg-[#0f2a3c]"
                >
                  <Send className="h-4 w-4" />
                  Submit report
                </button>
              )}
              {isFailed(report) && (
                <button
                  type="button"
                  disabled={resending}
                  onClick={handleResend}
                  className="inline-flex items-center gap-2 rounded-xl bg-red-700 px-6 py-2.5 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-50"
                >
                  {resending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  Resend email
                </button>
              )}
            </div>
          )}
        </>
      ) : null}

      {confirmOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h3 className="text-lg font-bold text-slate-900">Submit report?</h3>
            <p className="mt-2 text-sm text-slate-600">
              This freezes the report and sends email from your Outlook mailbox.
              You will not be able to edit afterward.
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                disabled={submitting}
                onClick={() => setConfirmOpen(false)}
                className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={handleSubmit}
                className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {submitting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                Confirm submit
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default DailyUpdate;
