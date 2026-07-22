import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Loader2 } from "lucide-react";
import {
  setToken,
  isAuthenticated,
  exchangeCodeForToken,
  exchangeMicrosoftCodeForToken,
} from "../services/auth";

const AuthCallback = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState("");

  useEffect(() => {
    const finishLogin = async () => {
      try {
        // Case 1: Backend redirected back with ?token=... (direct JWT in URL)
        const token = params.get("token");
        if (token) {
          setToken(token);
          toast.success("Login successful!");
          navigate("/dashboard", { replace: true });
          return;
        }

        // Case 2: Google or Microsoft OAuth code exchange — GET /auth/callback?code=...
        const code = params.get("code");
        if (code) {
          const provider = sessionStorage.getItem("auth_provider") || "google";
          try {
            if (provider === "microsoft") {
              await exchangeMicrosoftCodeForToken(code);
            } else {
              await exchangeCodeForToken(code);
            }
            if (isAuthenticated()) {
              sessionStorage.removeItem("auth_provider");
              toast.success("Login successful!");
              navigate("/dashboard", { replace: true });
              return;
            }
          } catch (err) {
            const status = err?.response?.status;
            if (status === 403) {
              setErrorMsg(
                provider === "microsoft"
                  ? "Access was revoked. Please re-authenticate with Microsoft."
                  : "Access was revoked. Please re-authenticate with Google."
              );
            } else {
              setErrorMsg("Login failed. Please try again.");
            }
            setLoading(false);
            return;
          }
        }

        // Case 3: Already authenticated (page refreshed on /auth/callback)
        if (isAuthenticated()) {
          navigate("/dashboard", { replace: true });
          return;
        }

        // No token, no code, not authenticated — back to login
        setLoading(false);
        navigate("/login", { replace: true });
      } catch (err) {
        console.error("Auth callback error:", err);
        setErrorMsg("An unexpected error occurred. Please try again.");
        setLoading(false);
      }
    };

    finishLogin();
  }, [params, navigate]);

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4 text-slate-900">
      <div className="rounded-3xl border border-slate-200 bg-white p-8 shadow-xl text-center max-w-sm w-full">
        {loading ? (
          <>
            <Loader2 className="mx-auto h-8 w-8 animate-spin text-[#14344a]" />
            <p className="mt-4 text-lg font-medium">Finishing login…</p>
            <p className="mt-1 text-sm text-slate-500">
              Verifying your credentials with {sessionStorage.getItem("auth_provider") === "microsoft" ? "Microsoft" : "Google"}.
            </p>
          </>
        ) : errorMsg ? (
          <>
            <p className="text-lg font-semibold text-red-600">Login Failed</p>
            <p className="mt-2 text-sm text-slate-500">{errorMsg}</p>
            <button
              onClick={() => navigate("/login", { replace: true })}
              className="mt-6 rounded-xl bg-[#14344a] px-6 py-2 text-sm font-medium text-white hover:bg-[#0f2a3c]"
            >
              Back to Login
            </button>
          </>
        ) : (
          <>
            <p className="text-lg font-medium">Redirecting…</p>
            <p className="mt-2 text-sm text-slate-500">
              Please sign in again if needed.
            </p>
          </>
        )}
      </div>
    </div>
  );
};

export default AuthCallback;
