/**
 * Blacklist helpers — API may return camelCase (isBlacklisted) or snake_case.
 */

/** @returns {boolean} true only when explicitly marked blacklisted */
export function isCandidateBlacklisted(candidate) {
  if (!candidate) return false;
  const bl = candidate.blacklist;
  if (bl == null) return false;
  if (typeof bl === "boolean") return bl === true;
  return bl.isBlacklisted === true || bl.is_blacklisted === true;
}

/** @returns {string | null} */
export function getBlacklistReason(candidate) {
  const bl = candidate?.blacklist;
  if (!bl || typeof bl !== "object") return null;
  return bl.reason ?? null;
}

export function getBlacklistAudit(candidate) {
  const bl = candidate?.blacklist;
  if (!bl || typeof bl !== "object") return null;
  return {
    isBlacklisted: isCandidateBlacklisted(candidate),
    reason: bl.reason ?? null,
    blacklistedAt: bl.blacklistedAt ?? bl.blacklisted_at ?? null,
    blacklistedBy: bl.blacklistedBy ?? bl.blacklisted_by ?? null,
    source: bl.source ?? null,
    restoredAt: bl.restoredAt ?? bl.restored_at ?? null,
    restoredBy: bl.restoredBy ?? bl.restored_by ?? bl.recruiter ?? null,
  };
}

export function getBlacklistErrorMessage(err) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  return "Blacklist operation failed.";
}
