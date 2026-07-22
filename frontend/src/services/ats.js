// import api from "./api";
// import { normalizeAtsStatus } from "../lib/atsHelpers";

// /**
//  * POST /api/v1/jobs/{job_id}/calculate-ats
//  * Manually trigger incremental ATS score calculation for filtered candidates.
//  * 
//  * Response: 202 (processing runs in background)
//  * 
//  * @param {string} jobId - Job ID
//  * @returns {Promise<import("axios").AxiosResponse>}
//  */
// export const calculateAts = (jobId) =>
//   api.post(`/jobs/${jobId}/calculate-ats`);

// /**
//  * GET /api/v1/jobs/{job_id}/ats-status
//  *
//  * Current ATS run state — poll every 3–5s while status is "queued" or "processing".
//  *
//  * Response:
//  * - status: idle | queued | processing | completed | partially_failed | failed
//  * - mode: incremental | force
//  * - totalCandidates: filtered candidates in this run
//  * - processedCandidates: successfully scored so far
//  * - failedCandidates: LLM / storage errors
//  * - skippedExistingCandidates: valid score already existed
//  * - skippedResumeMissing: no extracted resume
//  * - triggeredAt / completedAt: ISO timestamps (completedAt null while running)
//  *
//  * @param {string} jobId
//  */
// export const getAtsStatus = async (jobId) => {
//   const response = await api.get(`/jobs/${jobId}/ats-status`, {
//     // Polling call — suppress the global error toast so a 404 / 500
//     // before any ATS run exists doesn't spam the user every 30 s.
//     skipErrorToast: true,
//   });

//   return {
//     ...response,
//     data: normalizeAtsStatus(response.data),
//   };
// };

// /**
//  * POST /api/v1/jobs/{job_id}/rerun-ats
//  * Force a complete ATS re-run — scores all filtered candidates unconditionally,
//  * ignoring any existing scores or JD version.
//  * 
//  * Response: 202 (processing runs in background)
//  * 
//  * @param {string} jobId - Job ID
//  * @returns {Promise<import("axios").AxiosResponse>}
//  */
// export const rerunAts = (jobId) =>
//   api.post(`/jobs/${jobId}/rerun-ats`);

import api from "./api";
import { normalizeAtsStatus } from "../lib/atsHelpers";

/**
 * POST /api/v1/jobs/{job_id}/calculate-ats
 */
export const calculateAts = (jobId) =>
  api.post(`/jobs/${jobId}/calculate-ats`);

/**
 * GET /api/v1/jobs/{job_id}/ats-status
 */
export const getAtsStatus = async (jobId) => {
  const response = await api.get(`/jobs/${jobId}/ats-status`, {
    skipErrorToast: true,
  });

  return {
    ...response,
    data: normalizeAtsStatus(response.data),
  };
};

/**
 * POST /api/v1/jobs/{job_id}/rerun-ats
 */
export const rerunAts = (jobId) =>
  api.post(`/jobs/${jobId}/rerun-ats`);

/**
 * NEW
 * GET /api/v1/candidates/{candidateId}/ats-score?jobId={jobId}
 */
export const fetchCandidateAtsScore = async (
  candidateId,
  jobId
) => {
  const response = await api.get(
    `/candidates/${candidateId}/ats-score`,
    {
      params: { jobId },
      skipErrorToast: true,
    }
  );

  return response.data;
};
