import api from "./api";

/**
 * GET /auth/login
 * Returns { auth_url: string } — redirect the browser to auth_url for Google OAuth.
 * Note: auth_url uses snake_case (Google OAuth standard).
 */
export const getGoogleAuthUrl = async () => {
  const { data } = await api.get("/auth/login");
  return data.auth_url;
};

/** Check if a valid token exists in localStorage */
export const isAuthenticated = () => !!localStorage.getItem("auth_token");

/** Persist the JWT to localStorage */
export const setToken = (token) => localStorage.setItem("auth_token", token);
export const saveToken = setToken;

/** Clear the JWT from localStorage */
export const clearToken = () => localStorage.removeItem("auth_token");

/**
 * GET /auth/callback?code={code}
 * Exchanges the Google OAuth code for a JWT.
 *
 * TokenResponse shape: { access_token: string, token_type: "bearer" }
 * (local dev only — production uses httpOnly cookie redirect)
 */
export const exchangeCodeForToken = async (code) => {
  const { data } = await api.get("/auth/callback", {
    params: { code },
  });

  // API returns access_token (snake_case per TokenResponse interface)
  const token = data?.access_token || data?.token || data?.jwt;
  if (token) setToken(token);
  return data;
};

/**
 * GET /auth/me
 * Returns RecruiterProfile: { recruiter_id|recruiterId, email, name, oauth_status, ... }
 */
export const getMe = () => api.get("/auth/me");

/**
 * GET /auth/microsoft/login
 * Constructs and returns the Microsoft OAuth 2.0 consent URL.
 */
export const getMicrosoftAuthUrl = async () => {
  const { data } = await api.get("/auth/microsoft/login");
  return data.auth_url;
};

/**
 * GET /auth/microsoft/callback
 * Exchanges the Microsoft authorisation code for OAuth tokens.
 */
export const exchangeMicrosoftCodeForToken = async (code) => {
  const { data } = await api.get("/auth/microsoft/callback", {
    params: { code },
  });

  const token = data?.access_token || data?.token || data?.jwt;
  if (token) setToken(token);
  return data;
};

/**
 * POST /auth/revoke
 * Revokes the Google OAuth token for this recruiter.
 * Called on explicit logout.
 */
export const logout = async () => {
  try {
    await api.post("/auth/revoke");
  } catch (error) {
    // Revoke may fail if token is already expired — clear locally anyway
    console.warn("Logout revoke failed, clearing local session anyway.", error);
  } finally {
    clearToken();
    window.location.href = "/login";
  }
};

