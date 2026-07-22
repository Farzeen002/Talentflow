import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Briefcase,
  Cpu,
  FileText,
  RefreshCw,
  PlusCircle,
  Upload,
  Sparkles,
  GitBranch,
  CheckCircle2,
  AlertTriangle,
  LayoutDashboard,
  FileSpreadsheet,
  LogOut,
  Download,
  Ban,
  ShieldCheck,
  BarChart3,
  Clock,
} from "lucide-react";
import Logo from "../components/Logo";

const FEATURES = [
  {
    icon: Briefcase,
    title: "Job posting",
    body: "Create roles, set screening rules, and open a candidate board per job.",
  },
  {
    icon: Cpu,
    title: "AI screening",
    body: "ATS scores filtered candidates against the job description.",
  },
  {
    icon: FileText,
    title: "Applicant tracking",
    body: "Profiles, resumes, shortlist, and blacklist in one candidate view.",
  },
  {
    icon: RefreshCw,
    title: "Daily updates",
    body: "Submit recruiter progress sheets and download PDFs without Excel.",
  },
];

const WORKFLOW = [
  {
    icon: PlusCircle,
    title: "Create Job",
    body: "Define the role, priority, and screening rules.",
  },
  {
    icon: Upload,
    title: "Import Candidates",
    body: "Bring in applicants and keep pipeline counts live.",
  },
  {
    icon: Sparkles,
    title: "AI Scans and Scores",
    body: "Rank filtered candidates so the strongest appear first.",
  },
  {
    icon: GitBranch,
    title: "Manage Pipeline",
    body: "Shortlist, blacklist, and move talent forward on the board.",
  },
];

const LandingPage = () => {
  const navigate = useNavigate();
  const goLogin = () => navigate("/login");

  return (
    <div
      className="min-h-screen overflow-x-hidden bg-[#eef2f5] text-slate-900 antialiased"
      style={{ fontFamily: '"Plus Jakarta Sans", system-ui, sans-serif' }}
    >
      <style>{`
        @keyframes tf-float {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-8px); }
        }
        @keyframes tf-fade-up {
          from { opacity: 0; transform: translateY(18px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .tf-float { animation: none; }
        .tf-fade-up { animation: tf-fade-up 0.7s ease-out both; }
        .tf-fade-up-delay { animation: tf-fade-up 0.7s ease-out 0.12s both; }
        .tf-fade-up-delay-2 { animation: tf-fade-up 0.7s ease-out 0.24s both; }
      `}</style>

      {/* Header */}
      <header className="sticky top-0 z-40 border-b border-slate-200/70 bg-white/85 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between px-5 sm:h-[4.25rem] sm:px-8 lg:px-10">
          <button
            type="button"
            onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
            className="rounded-xl transition hover:opacity-90"
          >
            <Logo variant="wordmark" />
          </button>
          <button
            type="button"
            onClick={goLogin}
            className="inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-[#0f2a3c] hover:shadow-md"
          >
            Sign in
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </header>

      {/* Hero — full first viewport, no clipping */}
      <section className="relative flex min-h-[calc(100dvh-4.25rem)] flex-col justify-center">
        <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
          <div
            className="absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse 90% 70% at 100% 0%, rgba(46,184,201,0.14), transparent 45%), radial-gradient(ellipse 60% 50% at 0% 20%, rgba(20,52,74,0.08), transparent 50%), linear-gradient(165deg, #e4ebf0 0%, #eef2f5 40%, #f7f9fb 100%)",
            }}
          />
          <div
            className="absolute inset-0 opacity-[0.4]"
            style={{
              backgroundImage:
                "linear-gradient(rgba(20,52,74,0.04) 1px, transparent 1px), linear-gradient(90deg, rgba(20,52,74,0.04) 1px, transparent 1px)",
              backgroundSize: "48px 48px",
              maskImage:
                "radial-gradient(ellipse 80% 70% at 50% 30%, black 20%, transparent 75%)",
            }}
          />
        </div>

        <div className="relative mx-auto grid w-full max-w-[1400px] flex-1 items-center gap-10 px-5 py-12 sm:px-8 lg:grid-cols-[1fr_1.15fr] lg:gap-12 lg:px-10 lg:py-16">
          <div className="tf-fade-up max-w-xl">
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[#2eb8c9]">
              Recruitment workspace
            </p>
            <h1 className="mt-4 text-4xl font-extrabold tracking-tight text-[#14344a] sm:text-5xl xl:text-[3.55rem] xl:leading-[1.06]">
              talent<span className="text-[#2eb8c9]">Floww</span>
            </h1>
            <p className="mt-2 text-xl font-semibold tracking-tight text-slate-700 sm:text-2xl">
              Screen. Score. Update — in one place.
            </p>
            <p className="mt-5 text-base leading-relaxed text-slate-600 sm:text-lg">
              Manage job pipelines, review candidates with ATS ranking, and
              submit daily hiring updates without leaving your desk.
            </p>
            <button
              type="button"
              onClick={goLogin}
              className="mt-8 inline-flex items-center gap-2 rounded-xl bg-[#14344a] px-7 py-3.5 text-sm font-semibold text-white shadow-[0_12px_28px_-8px_rgba(20,52,74,0.55)] transition hover:-translate-y-0.5 hover:bg-[#0f2a3c]"
            >
              Sign in to workspace
              <ArrowRight className="h-4 w-4" />
            </button>

            <div className="mt-10 flex flex-wrap gap-3">
              {["ATS scoring", "Candidate boards", "Daily sheets"].map((label) => (
                <span
                  key={label}
                  className="rounded-full border border-slate-200/80 bg-white/80 px-3.5 py-1.5 text-xs font-semibold text-slate-600 shadow-sm backdrop-blur"
                >
                  {label}
                </span>
              ))}
            </div>
          </div>

          {/* Product frame */}
          <div className="tf-fade-up-delay relative min-w-0 pb-6 lg:pb-4">
            <div className="relative">
              <div className="absolute -inset-4 rounded-[1.75rem] bg-gradient-to-br from-[#14344a]/10 via-transparent to-[#2eb8c9]/15 blur-xl" />
              <div className="relative overflow-hidden rounded-2xl border border-slate-200/90 bg-white shadow-[0_32px_80px_-28px_rgba(20,52,74,0.5)] ring-1 ring-black/[0.04]">
                <div className="flex items-center gap-1.5 border-b border-slate-100 bg-slate-50/90 px-4 py-2.5">
                  <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
                  <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
                  <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
                  <span className="ml-3 text-[11px] font-medium text-slate-400">
                    talentFloww · Candidate profile
                  </span>
                </div>

                <div className="flex min-h-[360px] sm:min-h-[400px] lg:min-h-[min(52vh,520px)]">
                  <aside className="hidden w-[188px] shrink-0 flex-col bg-[#14344a] md:flex">
                    <div className="flex items-center gap-2 border-b border-white/10 px-3 py-3.5">
                      <Logo className="h-8 w-8 rounded-md" />
                      <div className="min-w-0">
                        <p className="truncate text-[11px] font-semibold text-white">
                          talent<span className="text-[#5eead4]">Floww</span>
                        </p>
                        <p className="truncate text-[8px] text-slate-300">
                          Recruitment Intelligence AI
                        </p>
                      </div>
                    </div>
                    <nav className="flex flex-1 flex-col gap-1 px-2 py-3">
                      {[
                        { icon: LayoutDashboard, label: "Dashboard" },
                        { icon: Briefcase, label: "Jobs", active: true },
                        { icon: FileSpreadsheet, label: "Daily Update" },
                        { icon: PlusCircle, label: "Create Job" },
                      ].map(({ icon: Icon, label, active }) => (
                        <div
                          key={label}
                          className={`flex items-center gap-2 rounded-lg px-2.5 py-2 text-[11px] font-medium ${
                            active
                              ? "bg-[#1e455e] text-white shadow-sm"
                              : "text-slate-300"
                          }`}
                        >
                          <Icon className="h-3.5 w-3.5" strokeWidth={1.75} />
                          {label}
                        </div>
                      ))}
                    </nav>
                    <div className="border-t border-white/10 px-2 py-3">
                      <div className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[11px] text-slate-300">
                        <LogOut className="h-3.5 w-3.5" strokeWidth={1.75} />
                        Logout
                      </div>
                    </div>
                  </aside>

                  <div className="flex min-w-0 flex-1 flex-col bg-[#f3f5f8]">
                    <div className="border-b border-slate-200 bg-white px-4 py-3.5">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex items-center gap-3">
                          <div className="flex h-11 w-11 items-center justify-center rounded-full bg-[#14344a] text-sm font-bold text-white shadow-sm">
                            AR
                          </div>
                          <div>
                            <p className="text-sm font-bold text-slate-900">
                              Alex Rodriguez
                            </p>
                            <p className="text-[11px] text-slate-500">
                              Frontend Engineer · 6.2 yrs
                            </p>
                          </div>
                        </div>
                        <div className="flex gap-1.5">
                          <span className="inline-flex items-center gap-1 rounded-lg bg-[#14344a] px-2.5 py-1.5 text-[10px] font-semibold text-white">
                            <Download className="h-3 w-3" />
                            Resume
                          </span>
                          <span className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[10px] font-semibold text-slate-700">
                            Shortlist
                          </span>
                          <span className="inline-flex items-center gap-1 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5 text-[10px] font-semibold text-red-700">
                            <Ban className="h-3 w-3" />
                          </span>
                        </div>
                      </div>
                    </div>

                    <div className="grid flex-1 gap-3 p-3 sm:grid-cols-2 sm:p-4">
                      <div className="space-y-3">
                        <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
                          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                            Experience
                          </p>
                          <div className="mt-2 grid grid-cols-2 gap-2">
                            {[
                              ["Exp.", "6.2 yrs"],
                              ["Notice", "30d"],
                              ["CTC", "18 LPA"],
                              ["Expected", "24 LPA"],
                            ].map(([l, v]) => (
                              <div
                                key={l}
                                className="rounded-lg bg-slate-50 px-2.5 py-2"
                              >
                                <p className="text-[9px] text-slate-500">{l}</p>
                                <p className="text-xs font-semibold text-slate-800">
                                  {v}
                                </p>
                              </div>
                            ))}
                          </div>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
                          <div className="mb-2 flex items-center justify-between">
                            <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                              Screening
                            </p>
                            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] font-semibold text-emerald-700">
                              Verified
                            </span>
                          </div>
                          {["Relocation OK", "Interview ready"].map((row) => (
                            <div
                              key={row}
                              className="mb-1.5 flex items-center justify-between rounded-lg bg-slate-50 px-2.5 py-1.5 last:mb-0"
                            >
                              <span className="text-[10px] font-medium text-slate-600">
                                {row}
                              </span>
                              <span className="rounded-full bg-emerald-100 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">
                                Eligible
                              </span>
                            </div>
                          ))}
                        </div>
                      </div>

                      <div className="hidden flex-col gap-3 sm:flex">
                        <div className="flex flex-1 flex-col rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
                          <p className="text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                            Resume
                          </p>
                          <div className="mt-2 flex flex-1 items-center justify-center rounded-lg bg-[#1a2332]">
                            <div className="py-8 text-center">
                              <FileText className="mx-auto h-7 w-7 text-white/35" />
                              <p className="mt-2 text-[10px] text-white/65">
                                alex-rodriguez.pdf
                              </p>
                            </div>
                          </div>
                        </div>
                        <div className="rounded-xl border border-slate-200 bg-white p-3 shadow-sm">
                          <div className="flex items-center justify-between">
                            <div>
                              <p className="text-xs font-semibold text-slate-800">
                                ATS match
                              </p>
                              <p className="text-[10px] text-slate-500">
                                vs job description
                              </p>
                            </div>
                            <span className="rounded-lg bg-emerald-50 px-2.5 py-1 text-sm font-bold text-emerald-700">
                              94%
                            </span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Capabilities — full section band */}
      <section className="w-full bg-white">
        <div className="mx-auto w-full max-w-[1400px] px-5 py-20 sm:px-8 sm:py-24 lg:px-10 lg:py-28">
          <div className="max-w-3xl">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#2eb8c9]">
              Workspace
            </p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight text-[#14344a] sm:text-4xl lg:text-[2.75rem]">
              Built for how recruiters work
            </h2>
            <p className="mt-4 text-base text-slate-600 sm:text-lg">
              Four tools in one navy workspace — no spreadsheet hop between
              jobs, resumes, and end-of-day updates.
            </p>
          </div>

          <div className="mt-14 grid gap-5 sm:grid-cols-2 xl:grid-cols-4">
            {FEATURES.map(({ icon: Icon, title, body }, i) => (
              <article
                key={title}
                className="group relative min-h-[220px] overflow-hidden rounded-2xl border border-slate-200 bg-[#f7f9fb] p-6 transition duration-300 hover:-translate-y-1 hover:border-[#14344a]/20 hover:bg-white hover:shadow-[0_20px_40px_-24px_rgba(20,52,74,0.35)] sm:p-7"
              >
                <div className="absolute right-4 top-4 text-4xl font-extrabold text-slate-200/80 transition group-hover:text-[#14344a]/10">
                  {String(i + 1).padStart(2, "0")}
                </div>
                <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-[#14344a] text-white shadow-sm">
                  <Icon className="h-5 w-5" strokeWidth={1.75} />
                </div>
                <h3 className="mt-5 text-base font-bold text-[#14344a] sm:text-lg">
                  {title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-slate-600">
                  {body}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Pipelines — full section band */}
      <section className="w-full bg-[#eef2f5]">
        <div className="mx-auto w-full max-w-[1400px] px-5 py-20 sm:px-8 sm:py-24 lg:px-10 lg:py-28">
          <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#2eb8c9]">
                Pipelines
              </p>
              <h2 className="mt-2 text-3xl font-bold tracking-tight text-[#14344a] sm:text-4xl lg:text-[2.75rem]">
                Every open role, one board away
              </h2>
              <p className="mt-4 text-base leading-relaxed text-slate-600 sm:text-lg">
                Jump from dashboard counts into filtered candidates, ATS
                progress, and shortlists without losing context.
              </p>
              <ul className="mt-8 space-y-3">
                {[
                  {
                    icon: ShieldCheck,
                    text: "Screening rules applied on every candidate",
                  },
                  {
                    icon: BarChart3,
                    text: "Live filtered, shortlisted, and blacklist counts",
                  },
                  {
                    icon: Clock,
                    text: "Daily update sheets ready before report time",
                  },
                ].map(({ icon: Icon, text }) => (
                  <li
                    key={text}
                    className="flex items-center gap-3 rounded-xl border border-slate-200/80 bg-white px-4 py-3.5 shadow-sm"
                  >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#14344a]/8 text-[#14344a]">
                      <Icon className="h-4 w-4" />
                    </span>
                    <span className="text-sm font-medium text-slate-700">
                      {text}
                    </span>
                  </li>
                ))}
              </ul>
            </div>

            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_24px_50px_-28px_rgba(20,52,74,0.4)]">
              <div className="flex items-center justify-between border-b border-slate-100 bg-gradient-to-r from-slate-50 to-white px-5 py-4">
                <div>
                  <p className="text-sm font-bold text-[#14344a]">Dashboard</p>
                  <p className="text-xs text-slate-500">Active hiring pipelines</p>
                </div>
                <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 ring-1 ring-emerald-100">
                  4 open
                </span>
              </div>
              <div className="divide-y divide-slate-100">
                {[
                  {
                    title: "Senior Frontend Engineer",
                    id: "FE-204",
                    people: "28",
                    status: "Active",
                    tone: "bg-emerald-50 text-emerald-700",
                  },
                  {
                    title: "QA Automation Lead",
                    id: "QA-118",
                    people: "16",
                    status: "Screening",
                    tone: "bg-sky-50 text-sky-700",
                  },
                  {
                    title: "DevOps Specialist",
                    id: "OPS-091",
                    people: "11",
                    status: "Active",
                    tone: "bg-emerald-50 text-emerald-700",
                  },
                  {
                    title: "Product Designer",
                    id: "UX-055",
                    people: "19",
                    status: "Review",
                    tone: "bg-[#14344a]/10 text-[#14344a]",
                  },
                ].map((job) => (
                  <div
                    key={job.id}
                    className="flex items-center justify-between gap-3 px-5 py-4 transition hover:bg-slate-50/80"
                  >
                    <div>
                      <p className="text-sm font-semibold text-slate-900">
                        {job.title}
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {job.id} · {job.people} candidates
                      </p>
                    </div>
                    <span
                      className={`shrink-0 rounded-full px-2.5 py-1 text-[11px] font-semibold ${job.tone}`}
                    >
                      {job.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Workflow — full section band */}
      <section className="w-full bg-white">
        <div className="mx-auto w-full max-w-[1400px] px-5 py-20 sm:px-8 sm:py-24 lg:px-10 lg:py-28">
          <div className="max-w-3xl">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#2eb8c9]">
              Flow
            </p>
            <h2 className="mt-2 text-3xl font-bold tracking-tight text-[#14344a] sm:text-4xl lg:text-[2.75rem]">
              Create → import → score → decide
            </h2>
          </div>

          <div className="mt-14 grid items-start gap-10 lg:grid-cols-[0.88fr_1.12fr] lg:gap-14">
            <ol className="relative space-y-3">
              <div
                className="absolute bottom-6 left-[1.35rem] top-6 w-px bg-slate-200"
                aria-hidden
              />
              {WORKFLOW.map(({ icon: Icon, title, body }, index) => (
                <li
                  key={title}
                  className="relative flex gap-4 rounded-2xl border border-slate-100 bg-[#f7f9fb] p-4 transition hover:border-[#14344a]/15 hover:bg-white hover:shadow-md sm:p-5"
                >
                  <div className="relative z-10 flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-[#14344a] text-white shadow-sm">
                    <Icon className="h-5 w-5" strokeWidth={1.75} />
                  </div>
                  <div>
                    <p className="text-[10px] font-semibold uppercase tracking-wide text-[#2eb8c9]">
                      Step {index + 1}
                    </p>
                    <h3 className="mt-0.5 text-base font-bold text-slate-900">
                      {title}
                    </h3>
                    <p className="mt-1 text-sm leading-relaxed text-slate-600">
                      {body}
                    </p>
                  </div>
                </li>
              ))}
            </ol>

            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-[0_24px_50px_-28px_rgba(20,52,74,0.4)]">
              <div className="border-b border-slate-100 bg-slate-50 px-5 py-4">
                <p className="text-sm font-bold text-[#14344a]">
                  AI Scans and Scores
                </p>
                <p className="text-xs text-slate-500">
                  Senior Frontend Engineer · FE-204
                </p>
              </div>
              <div className="divide-y divide-slate-100">
                {[
                  {
                    name: "Alex Rodriguez",
                    role: "Frontend Engineer",
                    score: 94,
                    tags: [
                      { label: "Compliant", ok: true },
                      { label: "React", ok: true },
                    ],
                  },
                  {
                    name: "Alex Thompson",
                    role: "Full-Stack Engineer",
                    score: 88,
                    tags: [
                      { label: "Compliant", ok: true },
                      { label: "TypeScript", ok: true },
                    ],
                  },
                  {
                    name: "Jordan Blake",
                    role: "UI Engineer",
                    score: 76,
                    tags: [
                      { label: "Compliant", ok: true },
                      { label: "Notice risk", ok: false },
                    ],
                  },
                ].map((c) => (
                  <div
                    key={c.name}
                    className="flex flex-wrap items-center justify-between gap-3 px-5 py-4 transition hover:bg-slate-50/70"
                  >
                    <div className="flex items-center gap-3">
                      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[#14344a] text-xs font-bold text-white">
                        {c.name
                          .split(" ")
                          .map((n) => n[0])
                          .join("")}
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-slate-900">
                          {c.name}
                        </p>
                        <p className="text-xs text-slate-500">{c.role}</p>
                        <div className="mt-1.5 flex flex-wrap gap-1.5">
                          {c.tags.map((t) => (
                            <span
                              key={t.label}
                              className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold ${
                                t.ok
                                  ? "bg-emerald-50 text-emerald-700"
                                  : "bg-amber-50 text-amber-700"
                              }`}
                            >
                              {t.ok ? (
                                <CheckCircle2 className="h-3 w-3" />
                              ) : (
                                <AlertTriangle className="h-3 w-3" />
                              )}
                              {t.label}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                    <div className="text-right">
                      <p className="text-[10px] font-medium uppercase tracking-wide text-slate-400">
                        Score
                      </p>
                      <p className="text-xl font-bold text-[#14344a]">
                        {c.score}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Sign-in band */}
      <section className="relative overflow-hidden bg-[#14344a] py-16 sm:py-20">
        <div
          className="pointer-events-none absolute inset-0 opacity-40"
          aria-hidden
          style={{
            background:
              "radial-gradient(ellipse 50% 80% at 90% 50%, rgba(46,184,201,0.25), transparent)",
          }}
        />
        <div className="relative mx-auto flex max-w-[1400px] flex-col items-start justify-between gap-6 px-5 sm:px-8 lg:flex-row lg:items-center lg:px-10">
          <div>
            <h2 className="text-2xl font-bold tracking-tight text-white sm:text-3xl">
              Open your talentFloww workspace
            </h2>
            <p className="mt-2 text-sm text-slate-300 sm:text-base">
              Continue jobs, candidates, and daily updates where you left off.
            </p>
          </div>
          <button
            type="button"
            onClick={goLogin}
            className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-white px-7 py-3.5 text-sm font-semibold text-[#14344a] shadow-sm transition hover:bg-slate-100"
          >
            Sign in
            <ArrowRight className="h-4 w-4" />
          </button>
        </div>
      </section>

      <footer className="border-t border-slate-200 bg-white py-8">
        <div className="mx-auto flex max-w-[1400px] flex-col items-center justify-between gap-4 px-5 sm:flex-row sm:px-8 lg:px-10">
          <Logo variant="wordmark" />
          <p className="text-xs text-slate-500">
            © {new Date().getFullYear()} talentFloww
          </p>
        </div>
      </footer>
    </div>
  );
};

export default LandingPage;
