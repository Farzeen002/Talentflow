// import { useEffect, useState, useRef, useCallback } from "react";
// import { getAtsStatus } from "../services/ats";

// // Terminal states where polling should stop completely
// const TERMINAL_STATUSES = ["completed", "partially_failed", "failed"];

// /**
//  * Custom hook to poll ATS status.
//  *
//  * Polling behaviour:
//  *  - While status is "queued" or "processing": polls at `activePollInterval` (default 3 s).
//  *  - While status is "idle": polls at `passivePollInterval` (default 30 s).
//  *  - When status reaches a terminal state (completed, partially_failed, failed):
//  *    stops polling completely.
//  *  - Set `enabled = false` to pause all polling entirely.
//  *
//  * @param {string}  jobId               - Job ID to poll for
//  * @param {boolean} [enabled=true]      - Master switch; set false to suspend polling
//  * @param {number}  [activePollInterval=3000]  - Interval (ms) while actively scoring
//  * @param {number}  [passivePollInterval=30000] - Interval (ms) when idle
//  */
// export const useAtsStatus = (
//   jobId,
//   enabled = true,
//   activePollInterval = 3000,
//   passivePollInterval = 30000,
// ) => {
//   const [atsStatus, setAtsStatus] = useState(null);
//   const [loading, setLoading] = useState(false);
//   const [error, setError] = useState(null);
//   const pollTimeoutRef = useRef(null);
//   const mountedRef = useRef(true);
//   const instanceIdRef = useRef(`ats-${Math.random().toString(36).slice(2, 9)}`);

//   const instanceId = instanceIdRef.current;
//   const debugLog = (msg) => {
//     if (typeof window !== "undefined" && window.__DEBUG_ATS_POLLING) {
//       console.log(`[${instanceId}] ${msg}`);
//     }
//   };

//   const clearPoll = (reason = "cleanup") => {
//     if (pollTimeoutRef.current) {
//       debugLog(`Clearing poll: ${reason}`);
//       clearTimeout(pollTimeoutRef.current);
//       pollTimeoutRef.current = null;
//     }
//   };

//   const fetchStatus = useCallback(async () => {
//     if (!jobId) return null;

//     try {
//       setError(null);
//       const { data } = await getAtsStatus(jobId);
//       if (mountedRef.current) setAtsStatus(data);
//       debugLog(`Fetched status: ${data?.status}`);
//       return data;
//     } catch (err) {
//       console.error("Failed to fetch ATS status:", err);
//       if (mountedRef.current) setError("Failed to fetch ATS status");
//       return null;
//     }
//   }, [jobId]);

//   useEffect(() => {
//     mountedRef.current = true;
//     debugLog("Effect mounted");

//     if (!enabled || !jobId) {
//       clearPoll("disabled or no jobId");
//       return;
//     }

//     const poll = async () => {
//       clearPoll("before fetch");

//       setLoading(true);
//       const data = await fetchStatus();
//       if (mountedRef.current) setLoading(false);

//       if (!mountedRef.current) {
//         debugLog("Component unmounted, stopping poll");
//         return;
//       }

//       // Check if terminal state reached — stop polling completely
//       if (TERMINAL_STATUSES.includes(data?.status)) {
//         debugLog(`Terminal state reached: ${data.status}. Stopping poll.`);
//         clearPoll("terminal state");
//         return;
//       }

//       // Actively scoring → short interval; idle → longer interval
//       const isActive =
//         data?.status === "processing" || data?.status === "queued";
//       const nextInterval = isActive ? activePollInterval : passivePollInterval;

//       debugLog(`Scheduling next poll in ${nextInterval}ms (status: ${data?.status})`);
//       pollTimeoutRef.current = setTimeout(poll, nextInterval);
//     };

//     poll();

//     return () => {
//       debugLog("Effect cleanup");
//       mountedRef.current = false;
//       clearPoll("unmount");
//     };
//   }, [jobId, enabled, activePollInterval, passivePollInterval, fetchStatus]);

//   const isActive =
//     atsStatus?.status === "processing" || atsStatus?.status === "queued";

//   return {
//     atsStatus,
//     loading,
//     error,
//     /** true while status is "queued" or "processing" */
//     isActive,
//     /** @deprecated use isActive instead */
//     isProcessing: isActive,
//     refetch: fetchStatus,
//   };
// };

// export default useAtsStatus;


import { useEffect, useState, useRef, useCallback } from "react";
import { getAtsStatus } from "../services/ats";

const TERMINAL_STATUSES = [
  "completed",
  "partially_failed",
  "failed",
];

const normalizeAtsResponse = (response) => {
  if (!response) return null;

  // Backend contract:
  // {
  //   jobId: "...",
  //   atsRun: {...}
  // }

  if (response?.atsRun) {
    return {
      jobId: response.jobId,
      ...response.atsRun,
    };
  }

  // Fallback support for flat responses
  return response;
};

/**
 * @param {object} [options]
 * @param {boolean} [options.pollWhenIdle=false] - When false, stop after first fetch if status is idle.
 * @param {boolean} [options.forcePoll=false] - When true (e.g. progress modal open), keep polling on idle.
 */
export const useAtsStatus = (
  jobId,
  enabled = true,
  activePollInterval = 3000,
  passivePollInterval = 30000,
  options = {},
) => {
  const { pollWhenIdle = false, forcePoll = false } = options;
  const [atsStatus, setAtsStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const pollTimeoutRef = useRef(null);
  const pollFnRef = useRef(null);
  const mountedRef = useRef(false);
  const optionsRef = useRef({ pollWhenIdle, forcePoll });
  optionsRef.current = { pollWhenIdle, forcePoll };

  const clearPoll = useCallback(() => {
    if (pollTimeoutRef.current) {
      clearTimeout(pollTimeoutRef.current);
      pollTimeoutRef.current = null;
    }
  }, []);

  const scheduleNextPoll = useCallback(
    (status) => {
      if (!mountedRef.current || TERMINAL_STATUSES.includes(status)) {
        return;
      }

      const isRunActive =
        status === "queued" || status === "processing";
      const { pollWhenIdle: idlePoll, forcePoll: force } = optionsRef.current;

      if (!isRunActive && !idlePoll && !force) {
        return;
      }

      clearPoll();
      const nextInterval = isRunActive
        ? activePollInterval
        : passivePollInterval;

      pollTimeoutRef.current = setTimeout(() => {
        pollFnRef.current?.();
      }, nextInterval);
    },
    [activePollInterval, passivePollInterval, clearPoll]
  );

  const fetchStatus = useCallback(async () => {
    if (!jobId) return null;

    try {
      setError(null);

      const { data } = await getAtsStatus(jobId);

      const normalized = normalizeAtsResponse(data);

      if (mountedRef.current) {
        setAtsStatus(normalized);
      }

      return normalized;
    } catch (err) {
      console.error("Failed to fetch ATS status:", err);

      if (mountedRef.current) {
        setError("Failed to fetch ATS status");
      }

      return null;
    }
  }, [jobId]);

  const refetch = useCallback(async () => {
    const data = await fetchStatus();
    scheduleNextPoll(data?.status ?? "idle");
    return data;
  }, [fetchStatus, scheduleNextPoll]);

  useEffect(() => {
    mountedRef.current = true;

    if (!enabled || !jobId) {
      clearPoll();
      return;
    }

    const poll = async () => {
      clearPoll();

      if (mountedRef.current) {
        setLoading(true);
      }

      const data = await fetchStatus();

      if (mountedRef.current) {
        setLoading(false);
      }

      if (!mountedRef.current) return;

      scheduleNextPoll(data?.status ?? "idle");
    };

    pollFnRef.current = poll;
    poll();

    return () => {
      mountedRef.current = false;
      clearPoll();
    };
  }, [
    jobId,
    enabled,
    fetchStatus,
    clearPoll,
    scheduleNextPoll,
    forcePoll,
  ]);

  const status = atsStatus?.status ?? "idle";

  const isActive =
    status === "queued" ||
    status === "processing";

  return {
    atsStatus,
    loading,
    error,
    isActive,
    isProcessing: isActive,
    refetch,
  };
};

export default useAtsStatus;