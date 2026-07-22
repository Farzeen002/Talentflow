import api from "./api";

/**
 * GET /api/v1/jobs
 *
 * @param {number} page
 * @param {number} limit
 * @param {{ status?: "active"|"paused"|"closed", includeArchived?: boolean }} [options]
 * @returns {Promise<import("axios").AxiosResponse<JobsListResponse>>}
 *
 * Archived jobs are excluded by default; pass includeArchived: true to list them.
 */
export const listJobs = (page = 1, limit = 20, options = {}) => {
  const params = { page, limit };

  if (options.status) params.status = options.status;

  if (options.includeArchived) params.includeArchived = true;

  return api.get("/jobs", { params });
};

/**
 * GET /api/v1/jobs/importable
 *
 * Naukri job codes found on this recruiter's ingested candidates that have not
 * yet been created in the jobs collection. Used by Create Job to auto-fill
 * Job ID + Job Title. Does NOT create a job — call POST /jobs after selection.
 *
 * Response (typical):
 * {
 *   jobs: [
 *     { jobId: "DBA003", title: "QA Test Engineer", candidateCount: 12 }
 *   ]
 * }
 *
 * candidateCount is informational for the dropdown only — not persisted.
 */
export const listImportableJobs = () =>
  api.get("/jobs/importable", { skipErrorToast: true });

/**
 * GET /api/v1/jobs/{jobId}
 */
export const getJob = (jobId) => api.get(`/jobs/${jobId}`);

/**
 * POST /api/v1/jobs
 */
export const createJob = (payload) => api.post("/jobs", payload);

/**
 * PATCH /api/v1/jobs/{jobId}
 *
 * All fields optional. Safe fields apply immediately.
 * title, description, employmentType trigger JD re-analysis + ATS score invalidation.
 */
export const updateJob = (jobId, payload) =>
  api.patch(`/jobs/${jobId}`, payload);

/**
 * DELETE /api/v1/jobs/{jobId}
 *
 * Allowed only when no candidates are linked (409 otherwise — use isArchived).
 */
export const deleteJob = (jobId) => api.delete(`/jobs/${jobId}`);
