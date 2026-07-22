const STORAGE_KEY = "talentflow_centralized_candidates_list";

export function saveCentralizedListState(state) {
  try {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        scrollY: state.scrollY ?? 0,
        tab: state.tab ?? "yours",
        search: state.search ?? "",
        recruiterFilter: state.recruiterFilter ?? "",
        candidateId: state.candidateId ?? null,
        pendingRestore: true,
      })
    );
  } catch {
    /* ignore */
  }
}

export function loadCentralizedListState() {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearCentralizedListRestorePending() {
  const saved = loadCentralizedListState();
  if (!saved) return;
  try {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...saved, pendingRestore: false })
    );
  } catch {
    /* ignore */
  }
}
