import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { User, CheckCircle2, FileText, Sparkles } from "lucide-react";
import Logo from "../components/Logo";
import {
  getGoogleAuthUrl,
  getMicrosoftAuthUrl,
  isAuthenticated,
  setToken,
} from "../services/auth";

const Login = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const [loadingProvider, setLoadingProvider] = useState(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");

  // Token in URL or already authenticated → go to dashboard
  useEffect(() => {
    const token = params.get("token");

    if (token) {
      setToken(token);
      navigate("/dashboard", { replace: true });
      return;
    }

    if (isAuthenticated()) {
      navigate("/dashboard", { replace: true });
    }
  }, [params, navigate]);

  const isFormValid =
    name.trim().length > 0 &&
    email.trim().length > 0 &&
    email.includes("@");

  const handleGoogleLogin = async () => {
    if (!isFormValid) {
      toast.error("Please fill in your name and valid email first.");
      return;
    }

    setLoadingProvider("google");

    try {
      localStorage.setItem("user_name", name);
      localStorage.setItem("user_email", email);
      sessionStorage.setItem("auth_provider", "google");

      const url = await getGoogleAuthUrl();
      if (!url) throw new Error("No auth URL returned");
      window.location.href = url;
    } catch (error) {
      console.error("Google login error:", error);
      toast.error("Login failed. Please try again.");
      setLoadingProvider(null);
    }
  };

  const handleMicrosoftLogin = async () => {
    if (!isFormValid) {
      toast.error("Please fill in your name and valid email first.");
      return;
    }

    setLoadingProvider("microsoft");

    try {
      localStorage.setItem("user_name", name);
      localStorage.setItem("user_email", email);
      sessionStorage.setItem("auth_provider", "microsoft");

      const url = await getMicrosoftAuthUrl();
      if (!url) throw new Error("No auth URL returned");
      window.location.href = url;
    } catch (error) {
      console.error("Microsoft login error:", error);
      toast.error("Login failed. Please try again.");
      setLoadingProvider(null);
    }
  };

  const inputClass =
    "w-full h-[54px] rounded-2xl border border-white/12 bg-white/[0.07] px-5 text-[15px] text-white placeholder:text-white/35 outline-none transition-all duration-300 focus:border-[#5eead4]/50 focus:bg-white/[0.11] focus:ring-4 focus:ring-[#5eead4]/10";

  const oauthBtnClass = (ready) =>
    `group relative mt-3 first:mt-6 w-full h-[56px] rounded-2xl border border-white/15 bg-white flex items-center justify-center gap-3 overflow-hidden transition-all duration-300 shadow-[0_12px_40px_rgba(0,0,0,0.2)] ${
      ready
        ? "hover:scale-[1.015] hover:shadow-[0_16px_48px_rgba(94,234,212,0.18)]"
        : "opacity-55 cursor-not-allowed"
    }`;

  return (
    <div
      className="flex min-h-screen w-full overflow-hidden bg-[#0f2433]"
      style={{ fontFamily: "'Plus Jakarta Sans', system-ui, sans-serif" }}
    >
      {/* ── LEFT — brand stage ── */}
      <div className="relative hidden w-[52%] overflow-hidden lg:flex lg:flex-col">
        {/* Atmosphere */}
        <div className="absolute inset-0 bg-gradient-to-br from-[#e8f1f6] via-[#f2f6f9] to-[#dce8ef]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_20%_0%,rgba(46,184,201,0.18),transparent_50%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_90%_80%,rgba(20,52,74,0.12),transparent_45%)]" />
        <div
          className="absolute inset-0 opacity-[0.35]"
          style={{
            backgroundImage:
              "radial-gradient(circle at 1px 1px, rgba(20,52,74,0.12) 1px, transparent 0)",
            backgroundSize: "28px 28px",
          }}
        />
        <div className="absolute -left-24 bottom-[-120px] h-[420px] w-[420px] rounded-full bg-[#2eb8c9]/20 blur-[100px]" />
        <div className="absolute right-[-80px] top-[-60px] h-[320px] w-[320px] rounded-full bg-[#14344a]/10 blur-[90px]" />

        {/* Logo */}
        <div className="relative z-20 flex items-center gap-3 px-12 pt-10">
          <div className="flex h-11 w-11 items-center justify-center overflow-hidden rounded-xl bg-white shadow-md ring-1 ring-[#14344a]/8">
            <Logo className="h-11 w-11" />
          </div>
          <div>
            <p className="text-sm font-bold tracking-tight text-[#14344a]">
              talent<span className="text-[#2eb8c9]">Floww</span>
            </p>
            <p className="text-[11px] font-medium text-[#14344a]/55">
              Recruitment Intelligence AI
            </p>
          </div>
        </div>

        <div className="relative z-10 flex flex-1 flex-col justify-center px-12 xl:px-16">
          <div className="login-fade-up mb-14 max-w-xl">
            {/* <p className="mb-4 inline-flex items-center gap-2 rounded-full border border-[#14344a]/10 bg-white/70 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-[#14344a]/70 shadow-sm backdrop-blur">
              <Sparkles className="h-3.5 w-3.5 text-[#2eb8c9]" />
              Built for recruiters
            </p> */}
            <h1 className="text-[3.4rem] font-extrabold leading-[1.02] tracking-[-0.045em] text-[#14344a] xl:text-[3.75rem]">
              talent
              <span className="bg-gradient-to-r from-[#2eb8c9] to-[#14b8a6] bg-clip-text text-transparent">
                Floww
              </span>
            </h1>
            <p className="mt-4 max-w-md text-[1.05rem] leading-relaxed text-[#14344a]/65">
              Screen smarter, shortlist faster, and keep every daily report in
              one calm workspace.
            </p>
          </div>

          {/* Product vignette */}
          <div className="login-fade-up-delay relative h-[340px] w-full max-w-[560px]">
            {/* Resume card */}
            <div className="login-float-card absolute left-0 top-8 z-20 h-[248px] w-[186px] rounded-[1.75rem] border border-white/80 bg-white/90 p-5 shadow-[0_28px_60px_rgba(20,52,74,0.14)] backdrop-blur-xl">
              <div className="mb-5 flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-[#e0f7fa] to-[#f0f4f8]">
                <User className="h-6 w-6 text-[#2eb8c9]" />
              </div>
              <div className="space-y-2.5">
                <div className="h-2 w-[72%] rounded-full bg-[#14344a]/15" />
                <div className="h-2 w-full rounded-full bg-[#14344a]/08" />
                <div className="h-2 w-[88%] rounded-full bg-[#14344a]/08" />
                <div className="pt-3 space-y-2.5">
                  <div className="h-2 w-full rounded-full bg-[#14344a]/07" />
                  <div className="h-2 w-[78%] rounded-full bg-[#14344a]/07" />
                  <div className="h-2 w-full rounded-full bg-[#14344a]/07" />
                  <div className="h-2 w-[64%] rounded-full bg-[#2eb8c9]/35" />
                </div>
              </div>
            </div>

            {/* Connection SVG */}
            <div className="absolute left-[130px] top-[70px] z-10 h-[170px] w-[300px]">
              <svg className="login-line h-full w-full" viewBox="0 0 300 170" fill="none">
                <defs>
                  <linearGradient id="tfLine" x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor="#2eb8c9" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="#14344a" stopOpacity="0.35" />
                  </linearGradient>
                </defs>
                <circle cx="70" cy="85" r="16" stroke="#2eb8c9" strokeWidth="2" opacity="0.7" />
                <circle cx="70" cy="85" r="6" fill="#2eb8c9" />
                <path d="M0 85 H54" stroke="url(#tfLine)" strokeWidth="2" />
                <path d="M86 85 C140 85, 160 28, 250 28" stroke="url(#tfLine)" strokeWidth="2" />
                <path d="M86 85 C140 85, 160 85, 250 85" stroke="url(#tfLine)" strokeWidth="2" />
                <path d="M86 85 C140 85, 160 142, 250 142" stroke="url(#tfLine)" strokeWidth="2" />
              </svg>
            </div>

            {/* Right floating cards */}
            <div className="absolute right-0 top-0 z-20 flex h-full w-[250px] flex-col justify-between py-4">
              <div className="login-float ml-8 flex items-center gap-3 rounded-2xl border border-white/90 bg-white/85 px-4 py-3 shadow-[0_18px_40px_rgba(20,52,74,0.1)] backdrop-blur-xl">
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-[#e0f7fa]">
                  <User className="h-4 w-4 text-[#2eb8c9]" />
                </div>
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#14344a]/45">
                    Candidate
                  </p>
                  <p className="text-[15px] font-semibold text-[#14344a]">Jane Doe</p>
                </div>
              </div>

              <div className="login-float-delay mx-4 rounded-[1.5rem] border border-white/70 bg-white/50 px-5 py-4 shadow-sm backdrop-blur-md">
                <div className="space-y-3">
                  {[0.9, 0.7, 0.55].map((w, i) => (
                    <div key={i} className="flex items-center gap-3">
                      <div className="h-3.5 w-3.5 rounded-full bg-[#2eb8c9]/80" />
                      <div
                        className="h-2 rounded-full bg-[#14344a]/12"
                        style={{ width: `${w * 100}%` }}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div className="relative h-[88px]">
                <div className="login-float absolute left-0 top-0 flex items-center gap-3 rounded-2xl border border-white/90 bg-white/90 px-4 py-3 shadow-[0_18px_40px_rgba(20,52,74,0.1)] backdrop-blur-xl">
                  <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#14344a]">
                    <FileText className="h-4 w-4 text-[#5eead4]" />
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#14344a]/45">
                      ATS score
                    </p>
                    <p className="text-[15px] font-bold text-[#14344a]">
                      92<span className="font-medium text-[#14344a]/45">/100</span>
                    </p>
                  </div>
                </div>

                <div className="login-float-delay absolute bottom-0 right-0 flex items-center gap-2.5 rounded-2xl border border-[#2eb8c9]/25 bg-gradient-to-br from-[#14344a] to-[#1c3d56] px-4 py-3 shadow-[0_18px_40px_rgba(20,52,74,0.25)]">
                  <div className="flex h-7 w-7 items-center justify-center rounded-full bg-[#2eb8c9]">
                    <CheckCircle2 className="h-4 w-4 text-white" />
                  </div>
                  <p className="text-[13px] font-semibold leading-tight text-white">
                    Filtered
                    <br />
                    Shortlist
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <p className="relative z-10 px-12 pb-8 text-xs text-[#14344a]/40">
          talentFloww · Recruitment Intelligence AI
        </p>
      </div>

      {/* ── RIGHT — sign in ── */}
      <div className="relative flex w-full flex-col items-center justify-center overflow-hidden px-5 py-10 sm:px-10 lg:w-[48%]">
        <div className="absolute inset-0 bg-[#14344a]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_50%_30%,rgba(46,184,201,0.16),transparent_55%)]" />
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_80%_90%,rgba(94,234,212,0.08),transparent_40%)]" />
        <div className="absolute left-1/2 top-1/2 h-[620px] w-[620px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-[#1e455e]/50 blur-[120px]" />

        {/* Mobile brand */}
        <div className="relative z-20 mb-8 flex items-center gap-3 lg:hidden">
          <div className="flex h-10 w-10 items-center justify-center overflow-hidden rounded-xl bg-white shadow-md">
            <Logo className="h-10 w-10" />
          </div>
          <p className="text-lg font-bold text-white">
            talent<span className="text-[#5eead4]">Floww</span>
          </p>
        </div>

        <div className="login-fade-up-delay-2 relative z-10 w-full max-w-[420px]">
          <div className="absolute inset-0 scale-[1.06] rounded-[2.4rem] bg-black/50 blur-3xl" />

          <div className="relative overflow-hidden rounded-[2rem] border border-white/15 bg-white/[0.08] px-7 py-9 shadow-[0_30px_90px_rgba(0,0,0,0.45)] backdrop-blur-2xl sm:px-9 sm:py-10">
            <div className="pointer-events-none absolute inset-0 bg-gradient-to-br from-white/20 via-transparent to-transparent" />
            <div className="pointer-events-none absolute -right-16 -top-16 h-40 w-40 rounded-full bg-[#5eead4]/15 blur-3xl" />

            <div className="relative z-10 flex flex-col items-center text-center">
              <h2 className="text-[2.35rem] font-bold leading-none tracking-[-0.04em] text-white sm:text-[2.75rem]">
                Welcome back
              </h2>
              <p className="mt-4 max-w-[300px] text-[15px] leading-relaxed text-white/65">
                Sign in to continue managing your recruitment pipeline.
              </p>

              <div className="mt-8 w-full space-y-3.5">
                <input
                  type="text"
                  placeholder="Full Name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className={inputClass}
                  autoComplete="name"
                />
                <input
                  type="email"
                  placeholder="Work Email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={inputClass}
                  autoComplete="email"
                />
              </div>

              <button
                type="button"
                onClick={handleGoogleLogin}
                disabled={loadingProvider !== null || !isFormValid}
                className={oauthBtnClass(isFormValid)}
              >
                {loadingProvider === "google" ? (
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-800" />
                ) : (
                  <>
                    <svg
                      className={`h-5 w-5 ${!isFormValid ? "grayscale opacity-50" : ""}`}
                      viewBox="0 0 24 24"
                    >
                      <path
                        fill="#4285F4"
                        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
                      />
                      <path
                        fill="#34A853"
                        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
                      />
                      <path
                        fill="#FBBC05"
                        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
                      />
                      <path
                        fill="#EA4335"
                        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
                      />
                    </svg>
                    <span className="text-[15px] font-semibold text-[#14344a]">
                      Continue with Google
                    </span>
                  </>
                )}
              </button>

              <button
                type="button"
                onClick={handleMicrosoftLogin}
                disabled={loadingProvider !== null || !isFormValid}
                className={oauthBtnClass(isFormValid)}
              >
                {loadingProvider === "microsoft" ? (
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-800" />
                ) : (
                  <>
                    <svg
                      className={`h-5 w-5 ${!isFormValid ? "grayscale opacity-50" : ""}`}
                      viewBox="0 0 23 23"
                    >
                      <rect x="0" y="0" width="10.5" height="10.5" fill="#f25022" />
                      <rect x="12.5" y="0" width="10.5" height="10.5" fill="#7fba00" />
                      <rect x="0" y="12.5" width="10.5" height="10.5" fill="#00a4ef" />
                      <rect x="12.5" y="12.5" width="10.5" height="10.5" fill="#ffb900" />
                    </svg>
                    <span className="text-[15px] font-semibold text-[#14344a]">
                      Continue with Microsoft
                    </span>
                  </>
                )}
              </button>

              <p className="mt-4 max-w-[280px] text-[12px] leading-relaxed text-white/45">
                Secure recruiter authentication powered by Google or Microsoft
                OAuth
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
