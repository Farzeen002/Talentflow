/**
 * Shared ATS status labels and candidate insight field normalization.
 */

export const ATS_STATUS_CONFIG = {
  idle: {
    label: "Not started",
    sublabel: "ATS scoring has not been run for this job yet",
    badgeCls: "bg-slate-100 text-slate-700 border-slate-200",
    dotCls: "bg-slate-400",
  },
  queued: {
    label: "Queued",
    sublabel: "Waiting for the scoring worker to start",
    badgeCls: "bg-violet-100 text-violet-800 border-violet-200",
    dotCls: "bg-violet-500 animate-pulse",
  },
  processing: {
    label: "Processing",
    sublabel: "Candidates are being scored in the background",
    badgeCls: "bg-[#14344a]/10 text-[#14344a] border-[#14344a]/20",
    dotCls: "bg-[#14344a] animate-pulse",
  },
  completed: {
    label: "Completed",
    sublabel: "ATS scoring finished for this job",
    badgeCls: "bg-emerald-100 text-emerald-800 border-emerald-200",
    dotCls: "bg-emerald-500",
  },
  partially_failed: {
    label: "Partially failed",
    sublabel:
      "Some candidates could not be scored — review the run details",
    badgeCls: "bg-amber-100 text-amber-800 border-amber-200",
    dotCls: "bg-amber-500",
  },
  failed: {
    label: "Failed",
    sublabel: "ATS scoring failed — try running it again",
    badgeCls: "bg-red-100 text-red-800 border-red-200",
    dotCls: "bg-red-500",
  },
};

export const ATS_COMPLETE_STATUSES = [
  "completed",
  "partially_failed",
];

export const ATS_ACTIVE_STATUSES = [
  "queued",
  "processing",
];

/**
 * Normalize ATS payload from any backend shape.
 */
export const normalizeAtsStatus = (atsStatus) => {
  if (!atsStatus) return null;

  const data =
    atsStatus?.atsRun ||
    atsStatus?.ats_run ||
    atsStatus?.run ||
    atsStatus;

  return {
    jobId:
      atsStatus?.jobId ||
      atsStatus?.job_id ||
      null,

    status:
      data?.status ||
      "idle",

    totalCandidates:
      data?.totalCandidates ??
      data?.total_candidates ??
      0,

    processedCandidates:
      data?.processedCandidates ??
      data?.processed_candidates ??
      0,

    failedCandidates:
      data?.failedCandidates ??
      data?.failed_candidates ??
      0,

    skippedExistingCandidates:
      data?.skippedExistingCandidates ??
      data?.skipped_existing_candidates ??
      0,

    skippedResumeMissing:
      data?.skippedResumeMissing ??
      data?.skipped_resume_missing ??
      0,

    startedAt:
      data?.startedAt ??
      data?.started_at ??
      null,

    completedAt:
      data?.completedAt ??
      data?.completed_at ??
      null,
  };
};

export const getAtsStatusConfig = (status) =>
  ATS_STATUS_CONFIG[status] ??
  ATS_STATUS_CONFIG.idle;

export const isAtsRunComplete = (status) =>
  ATS_COMPLETE_STATUSES.includes(status);

export const isAtsRunActive = (status) =>
  ATS_ACTIVE_STATUSES.includes(status);

export const getCandidateAtsScore = (candidate) =>
  candidate?.atsScore ??
  candidate?.ats_score ??
  candidate?.score ??
  candidate?.score_breakdown?.final_score ??
  candidate?.score_breakdown?.raw_score ??
  candidate?.ats?.score ??
  candidate?.ats?.matchScore ??
  candidate?.ats?.match_score ??
  candidate?.matchScore ??
  candidate?.match_score ??
  null;

export const candidateHasAtsScore = (candidate) => {
  const score = getCandidateAtsScore(candidate);
  return score !== null && score !== undefined;
};

export const countCandidatesWithAtsScore = (
  candidates
) =>
  (candidates ?? []).filter(
    candidateHasAtsScore
  ).length;

/**
 * True when rankings can be shown.
 */
export const jobHasAtsScores = (
  atsStatus,
  candidates
) => {
  const normalized =
    normalizeAtsStatus(atsStatus);

  if (
    isAtsRunComplete(
      normalized?.status
    )
  ) {
    return true;
  }

  if (
    (normalized?.processedCandidates ??
      0) > 0
  ) {
    return true;
  }

  return (
    countCandidatesWithAtsScore(
      candidates
    ) > 0
  );
};

/**
 * Progress string.
 */
export const formatAtsProgress = (
  atsStatus
) => {
  const normalized =
    normalizeAtsStatus(atsStatus);

  if (!normalized) return null;

  const processed =
    normalized.processedCandidates ?? 0;

  const total =
    normalized.totalCandidates;

  if (
    total === null ||
    total === undefined
  ) {
    return `${processed} scored`;
  }

  return `${processed} / ${total} scored`;
};

/**
 * Breakdown for ATS dashboard.
 */
export const computeAtsRunBreakdown = (
  atsStatus
) => {
  const normalized =
    normalizeAtsStatus(atsStatus);

  const status =
    normalized?.status ?? "idle";

  const total =
    normalized?.totalCandidates ?? 0;

  const done =
    normalized?.processedCandidates ?? 0;

  const failed =
    normalized?.failedCandidates ?? 0;

  const skippedExisting =
    normalized?.skippedExistingCandidates ??
    0;

  const skippedNoResume =
    normalized?.skippedResumeMissing ??
    0;

  const accounted =
    done +
    failed +
    skippedExisting +
    skippedNoResume;

  const inQueue =
    status === "queued"
      ? total
      : 0;

  const remaining =
    status === "queued"
      ? total
      : status === "processing"
      ? Math.max(
          0,
          total - accounted
        )
      : 0;

  const progressPercent =
    total > 0
      ? Math.min(
          100,
          Math.round(
            (accounted / total) * 100
          )
        )
      : 0;

  return {
    status,
    total,
    done,
    failed,
    skippedExisting,
    skippedNoResume,
    skippedTotal:
      skippedExisting +
      skippedNoResume,
    inQueue,
    remaining,
    accounted,
    progressPercent,
    isActive:
      isAtsRunActive(status),
    isEnded:
      isAtsRunComplete(status) ||
      status === "failed",
  };
};

/**
 * Normalize ATS insight fields.
 */
export const extractAtsInsights = (
  candidate
) => {
  const root =
    candidate?.atsInsights ??
    candidate?.ats_insights ??
    candidate?.ats ??
    candidate?.llm_evaluation ??
    candidate ??
    {};

  const pickList = (...vals) => {
    for (const v of vals) {
      if (
        Array.isArray(v) &&
        v.length
      ) {
        return v;
      }

      if (
        typeof v === "string" &&
        v.trim()
      ) {
        return [v.trim()];
      }
    }

    return [];
  };

  const matchingSkills =
    pickList(
      root.matchingSkills,
      root.matching_skills,
      root.matched_skills,
      root.matchedSkills,
      candidate?.matchingSkills,
      candidate?.matching_skills,
      candidate?.matched_skills,
      candidate?.matchedSkills
    );

  const missingSkills =
    pickList(
      root.missingSkills,
      root.missing_skills,
      candidate?.missingSkills,
      candidate?.missing_skills
    );

  const summary =
    root.summary ??
    root.rationale ??
    root.explanation ??
    candidate?.atsSummary ??
    candidate?.ats_summary ??
    candidate?.summary ??
    candidate?.llm_evaluation?.summary ??
    null;

  const strengths =
    pickList(
      root.strengths,
      candidate?.strengths
    );

  const gaps =
    pickList(
      root.gaps,
      root.weaknesses,
      candidate?.gaps
    );

  return {
    matchingSkills,
    missingSkills,
    summary,
    strengths,
    gaps,
  };
};