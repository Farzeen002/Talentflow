import api from "./api";

/**
 * GET /api/v1/jobs/{jobId}/candidates
 *
 * @param {string} jobId
 * @param {{
 *   view?: "filtered" | "all" | "blacklisted",
 *   page?: number,
 *   limit?: number,
 *   sort?: string
 * }} options
 * @returns {Promise<import("axios").AxiosResponse<CandidateListResponse>>}
 *
 * Response shape (CandidateListResponse):
 * {
 *   jobId: string,
 *   view: "filtered" | "all" | "blacklisted",
 *   total: number,       ← count for the selected view
 *   filtered: number,    ← candidates passing all 4 screening criteria
 *   page: number,
 *   limit: number,
 *   candidates: CandidateSummary[]
 * }
 *
 * view=filtered | all → active candidates only (blacklisted excluded)
 * view=blacklisted    → only blacklisted candidates; rows include blacklist object
 */
export const listCandidates = (
  jobId,
  {
    view = "filtered",
    page = 1,
    limit = 20,
    sort = "created_at_desc",
  } = {}
) => {
  const params = { view, page, limit, sort };
  return api.get(`/jobs/${jobId}/candidates`, { params });
};

/**
 * GET /api/v1/candidates/{candidateId}
 *
 * Recruiter-isolated: backend queries { candidate_id, recruiter_id }.
 * A wrong candidateId returns 404, never another recruiter's data.
 *
 * @param {string} candidateId - UUID string from CandidateSummary.candidateId
 * @returns {Promise<import("axios").AxiosResponse<CandidateDetail>>}
 */
export const getCandidate = (candidateId) =>
  api.get(`/candidates/${candidateId}`);

/**
 * GET /api/v1/candidates/{candidateId}/resume?action=preview
 *
 * Fetch a signed URL for previewing the candidate's resume inline.
 *
 * @param {string} candidateId - UUID string
 * @returns {Promise<import("axios").AxiosResponse<ResumeUrlResponse>>}
 *
 * Response shape:
 * {
 *   candidateId: string,
 *   url: string (signed GCS URL),
 *   expiresInSeconds: number,
 *   fileType: string,
 *   filename: string,
 *   action: "preview",
 *   note: null
 * }
 */
export const getResumePreview = (candidateId) =>
  api.get(`/candidates/${candidateId}/resume`, { params: { action: "preview" } });

/**
 * GET /api/v1/candidates/{candidateId}/resume?action=download
 *
 * Fetch a signed URL for downloading the candidate's resume.
 *
 * @param {string} candidateId - UUID string
 * @returns {Promise<import("axios").AxiosResponse<ResumeUrlResponse>>}
 *
 * Response shape:
 * {
 *   candidateId: string,
 *   url: string (signed GCS URL with attachment disposition),
 *   expiresInSeconds: number,
 *   fileType: string,
 *   filename: string,
 *   action: "download",
 *   note: null
 * }
 */
export const getResumeDownload = (candidateId) =>
  api.get(`/candidates/${candidateId}/resume`, { params: { action: "download" } });

/**
 * PATCH /api/v1/candidates/{candidateId}/blacklist
 *
 * Soft-blacklist — candidate stays in DB but is excluded from lists, counts, and ATS.
 *
 * @param {string} candidateId
 * @param {{ reason?: string }} [options]
 */
export const blacklistCandidate = (candidateId, { reason } = {}) =>
  api.patch(
    `/candidates/${candidateId}/blacklist`,
    reason != null && reason !== "" ? { reason } : {},
    { skipErrorToast: true }
  );

/**
 * PATCH /api/v1/candidates/{candidateId}/unblacklist
 *
 * Restore a blacklisted candidate to active status.
 * Expected: isBlacklisted=false and reason cleared (null) so reason does not
 * resurface on active list/detail UIs after restore.
 */
export const unblacklistCandidate = (candidateId) =>
  api.patch(`/candidates/${candidateId}/unblacklist`, {}, { skipErrorToast: true });
