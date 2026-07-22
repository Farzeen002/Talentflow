import axios from "axios";
import { toast } from "sonner";

const api = axios.create({
  baseURL: import.meta.env.VITE_BASE_URL,
  headers: { "Content-Type": "application/json" },
});

/* ── Request interceptor: attach Bearer token ── */
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("auth_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

/* ── Helper: extract a human-readable message from any error response ── */
const extractErrorMessage = (error) => {
  const detail = error?.response?.data?.detail;

  // Pydantic 422 — detail is an array of field-level errors
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const field = d.loc?.[1] ?? d.loc?.[0] ?? "field";
        return `${field}: ${d.msg}`;
      })
      .join(" · ");
  }

  // Normal string detail
  if (typeof detail === "string") return detail;

  return (
    error?.response?.data?.message ||
    error?.message ||
    "Something went wrong"
  );
};

/* ── Retry helper: one retry after 1 s for 500; exponential for network ── */
const withRetry = async (fn, retries = 1, delayMs = 1000) => {
  try {
    return await fn();
  } catch (err) {
    const status = err?.response?.status;
    const isNetworkError = !err?.response;

    if (retries > 0 && (status === 500 || isNetworkError)) {
      await new Promise((r) => setTimeout(r, delayMs));
      return withRetry(fn, retries - 1, delayMs * 2);
    }
    throw err;
  }
};

/**
 * Prevent identical error toasts from stacking when several API calls
 * fail at once (e.g. multiple daily-update 404s on page load).
 * Sonner's `id` already replaces an open toast; we also throttle for
 * cases where the previous toast already dismissed.
 */
const recentToastKeys = new Map();
const TOAST_DEDUP_MS = 2500;

const showErrorToast = (message, status) => {
  const key = `${status ?? "x"}:${message}`;
  const now = Date.now();
  const last = recentToastKeys.get(key) ?? 0;
  if (now - last < TOAST_DEDUP_MS) return;
  recentToastKeys.set(key, now);

  // Prune old keys occasionally
  if (recentToastKeys.size > 40) {
    for (const [k, t] of recentToastKeys) {
      if (now - t > TOAST_DEDUP_MS) recentToastKeys.delete(k);
    }
  }

  toast.error(message, { id: key });
};

/* ── Response interceptor: global error handling ── */
api.interceptors.response.use(
  (res) => res,
  (error) => {
    const status = error?.response?.status;

    if (status === 401) {
      // Identity cannot be established — expired / invalid token
      localStorage.removeItem("auth_token");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }

    if (status === 403) {
      // OAuth explicitly revoked — identity known but access denied
      localStorage.removeItem("auth_token");
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
      return Promise.reject(error);
    }

    // Allow individual calls to opt-out of the global toast
    // (e.g. background polling / optional endpoints that handle 404 locally)
    const skipToast = error?.config?.skipErrorToast;
    if (!skipToast) {
      showErrorToast(extractErrorMessage(error), status);
    }

    return Promise.reject(error);
  }
);

export { withRetry, extractErrorMessage };
export default api;
