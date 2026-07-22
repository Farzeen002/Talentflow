/** Per-job shortlist persisted in localStorage until a backend API exists. */

const key = (jobId) => `recruiter_shortlist_${jobId}`;

export const loadShortlistIds = (jobId) => {
  if (!jobId) return new Set();
  try {
    const raw = localStorage.getItem(key(jobId));
    const arr = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(arr) ? arr : []);
  } catch {
    return new Set();
  }
};

export const saveShortlistIds = (jobId, ids) => {
  if (!jobId) return;
  localStorage.setItem(key(jobId), JSON.stringify([...ids]));
};

export const isShortlistedCandidate = (candidate, shortlistedIds) => {
  if (!candidate) return false;
  const candidateId = candidate.id ?? candidate.candidateId;
  if (candidateId && shortlistedIds?.has(candidateId)) return true;
  return (
    candidate.isShortlisted === true ||
    candidate.shortlisted === true ||
    candidate.status === "shortlisted"
  );
};
