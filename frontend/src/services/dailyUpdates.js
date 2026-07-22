import api from "./api";

/**
 * Daily Reports API — Phase 1
 * Base: /api/v1/reports
 * Auth: JWT Bearer (via api interceptor). Never send recruiterId.
 */

/** POST /reports/open — open or create draft (idempotent) */
export const openReport = (reportDate, reportKind) =>
  api.post("/reports/open", { reportDate, reportKind });

/** GET /reports/defaults?reportKind= */
export const getReportDefaults = (reportKind) =>
  api.get("/reports/defaults", { params: { reportKind } });

/** GET /reports — paginated history */
export const listReports = (params = {}) =>
  api.get("/reports", { params });

/** GET /reports/{reportId} */
export const getReport = (reportId) =>
  api.get(`/reports/${reportId}`);

/** PATCH /reports/{reportId}/recipients */
export const updateRecipients = (reportId, body) =>
  api.patch(`/reports/${reportId}/recipients`, body);

/** POST /reports/{reportId}/entries */
export const addEntry = (reportId, body) =>
  api.post(`/reports/${reportId}/entries`, body);

/** PATCH /reports/{reportId}/entries/{entryId} */
export const updateEntry = (reportId, entryId, body) =>
  api.patch(`/reports/${reportId}/entries/${entryId}`, body);

/** DELETE /reports/{reportId}/entries/{entryId} */
export const deleteEntry = (reportId, entryId) =>
  api.delete(`/reports/${reportId}/entries/${entryId}`);

/** PATCH /reports/{reportId}/lead/metrics */
export const updateLeadMetrics = (reportId, body) =>
  api.patch(`/reports/${reportId}/lead/metrics`, body);

const leadCollection = (name) => ({
  add: (reportId, text) =>
    api.post(`/reports/${reportId}/lead/${name}`, { text }),
  update: (reportId, itemId, text) =>
    api.patch(`/reports/${reportId}/lead/${name}/${itemId}`, { text }),
  remove: (reportId, itemId) =>
    api.delete(`/reports/${reportId}/lead/${name}/${itemId}`),
});

export const keyActivities = leadCollection("key-activities");
export const challengesRisks = leadCollection("challenges-risks");
export const planForTomorrow = leadCollection("plan-for-tomorrow");

/** POST /reports/{reportId}/submit — returns 200 with status sent|failed */
export const submitReport = (reportId) =>
  api.post(`/reports/${reportId}/submit`);

/** POST /reports/{reportId}/resend — failed reports only */
export const resendReport = (reportId) =>
  api.post(`/reports/${reportId}/resend`);
