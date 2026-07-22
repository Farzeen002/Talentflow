const storageKey = (jobId) => `talentflow_job_${jobId}_list`;

/**
 * Persist scroll position and last-viewed candidate when leaving the job list.
 * Used to restore position after in-app back or browser back from candidate details.
 */
export function saveJobCandidatesListState(jobId, state) {
  if (!jobId) return;
  try {
    sessionStorage.setItem(
      storageKey(jobId),
      JSON.stringify({
        scrollTop: state.scrollTop ?? 0,
        candidateId: state.candidateId ?? null,
        page: state.page ?? 1,
        activeTabIdx: state.activeTabIdx ?? 0,
        pendingRestore: true,
      })
    );
  } catch {
    /* sessionStorage unavailable */
  }
}

export function loadJobCandidatesListState(jobId) {
  if (!jobId) return null;
  try {
    const raw = sessionStorage.getItem(storageKey(jobId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearListRestorePending(jobId) {
  const saved = loadJobCandidatesListState(jobId);
  if (!saved) return;
  try {
    sessionStorage.setItem(
      storageKey(jobId),
      JSON.stringify({ ...saved, pendingRestore: false })
    );
  } catch {
    /* ignore */
  }
}

export function hasPendingListRestore(jobId) {
  return Boolean(loadJobCandidatesListState(jobId)?.pendingRestore);
}

/**
 * Restore list scroll inside the candidate panel only — never use scrollIntoView on
 * the document, which would scroll the page and hide the tab bar above the list.
 */
export function restoreJobCandidatesListView(container, { scrollTop = 0, candidateId }) {
  if (!container) return;

  // window.scrollTo({ top: 0, left: 0, behavior: "instant" });

  if (scrollTop > 0) {
    container.scrollTop = scrollTop;
  }

  if (scrollTop > 0) {
    // If the saved scrollTop already keeps the row visible, preserve it exactly.
    // If not, do a minimal container-only adjustment so the candidate remains visible
    // (no scrollIntoView, no page scroll).
    if (candidateId) {
      const row = document.getElementById(`candidate-row-${candidateId}`);
      if (row) {
        const pad = 16;
        const containerRect = container.getBoundingClientRect();
        const rowRect = row.getBoundingClientRect();
        const relativeTop =
          rowRect.top - containerRect.top + container.scrollTop;

        const viewTop = container.scrollTop;
        const viewBottom = viewTop + container.clientHeight;
        const rowTop = relativeTop;
        const rowBottom = relativeTop + rowRect.height;

        const fullyVisible =
          rowTop >= viewTop + pad && rowBottom <= viewBottom - pad;

        if (!fullyVisible) {
          if (rowTop < viewTop + pad) {
            container.scrollTop = Math.max(0, rowTop - pad);
          } else if (rowBottom > viewBottom - pad) {
            container.scrollTop = rowBottom - container.clientHeight + pad;
          }
        }
      }
    }

    return;
  }

  // If we don't have a meaningful saved scrollTop (or it's 0), ensure the
  // last viewed candidate is visible.
  if (!candidateId) return;

  const row = document.getElementById(`candidate-row-${candidateId}`);
  if (!row) return;

  const containerRect = container.getBoundingClientRect();
  const rowRect = row.getBoundingClientRect();
  const relativeTop = rowRect.top - containerRect.top + container.scrollTop;

  container.scrollTop = Math.max(
    0,
    relativeTop - (container.clientHeight - rowRect.height) / 2
  );
}
