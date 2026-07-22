import { NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  ClipboardList,
  LogOut,
  FileSpreadsheet,
  History,
  Users,
} from "lucide-react";
import { logout } from "../services/auth";
import Logo from "./Logo";

const navItems = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/jobs", label: "Jobs", icon: ClipboardList },
  {
    to: "/centralized-candidates",
    label: "Centralized",
    icon: Users,
  },
  { to: "/daily-update", label: "Daily Reports", icon: FileSpreadsheet },
  { to: "/daily-update/history", label: "Report History", icon: History },
];

const AppLayout = ({ children, title, subtitle, action }) => {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-[#f3f5f8]">
      {/* ── Desktop sidebar — TalentFlow dark navy ── */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 flex-col bg-[#14344a] lg:flex">
        <div
          className="flex h-[72px] cursor-pointer items-center gap-3 border-b border-white/10 px-5"
          onClick={() => navigate("/dashboard")}
        >
          <div className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-xl bg-white shadow-sm">
            <Logo className="h-10 w-10" />
          </div>
          <div className="min-w-0">
            <p className="whitespace-nowrap text-[15px] font-semibold tracking-tight text-white">
              talent<span className="text-[#5eead4]">Floww</span>
            </p>
            <p className="whitespace-nowrap text-[11px] text-slate-300/90">
              Recruitment Intelligence AI
            </p>
          </div>
        </div>

        <nav className="flex-1 space-y-1.5 overflow-y-auto px-3 py-4">
          {navItems.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/jobs" || to === "/daily-update"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-all ${
                  isActive
                    ? "bg-[#1e455e] text-white shadow-sm"
                    : "text-slate-300 hover:bg-white/5 hover:text-white"
                }`
              }
            >
              <Icon className="h-[18px] w-[18px] shrink-0 opacity-90" strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-white/10 p-3">
          <button
            type="button"
            onClick={logout}
            className="flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-slate-300 transition-all hover:bg-white/5 hover:text-white"
          >
            <LogOut className="h-[18px] w-[18px]" strokeWidth={1.75} />
            Logout
          </button>
        </div>
      </aside>

      {/* ── Mobile top bar — same navy brand ── */}
      <div className="fixed inset-x-0 top-0 z-30 flex h-14 items-center justify-between bg-[#14344a] px-3 lg:hidden supports-[padding:env(safe-area-inset-top)]:pt-[env(safe-area-inset-top)] sm:h-16 sm:px-4">
        <button
          type="button"
          className="flex min-w-0 items-center gap-2.5"
          onClick={() => navigate("/dashboard")}
        >
          <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-lg bg-white sm:h-10 sm:w-10">
            <Logo className="h-full w-full" />
          </div>
          <div className="min-w-0 text-left">
            <p className="whitespace-nowrap text-sm font-semibold text-white">
              talent<span className="text-[#5eead4]">Floww</span>
            </p>
            <p className="hidden whitespace-nowrap text-[11px] text-slate-300 sm:block">
              Recruitment Intelligence AI
            </p>
          </div>
        </button>
        <button
          type="button"
          onClick={logout}
          aria-label="Logout"
          className="rounded-lg p-2 text-slate-300 hover:bg-white/10 hover:text-white"
        >
          <LogOut className="h-4 w-4" />
        </button>
      </div>

      {/* ── Mobile bottom nav — navy ── */}
      <nav className="fixed inset-x-0 bottom-0 z-30 flex justify-around border-t border-white/10 bg-[#14344a] px-1 pb-[max(0.5rem,env(safe-area-inset-bottom))] pt-1.5 lg:hidden">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/jobs" || to === "/daily-update"}
            className={({ isActive }) =>
              `flex min-w-0 flex-1 flex-col items-center gap-0.5 rounded-lg px-1 py-1.5 text-[10px] font-medium sm:text-xs ${
                isActive ? "text-white" : "text-slate-400"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={`flex h-8 w-8 items-center justify-center rounded-lg ${
                    isActive ? "bg-[#1e455e]" : ""
                  }`}
                >
                  <Icon className="h-5 w-5 shrink-0" strokeWidth={1.75} />
                </span>
                <span className="max-w-full truncate">
                  {label === "Daily Reports"
                    ? "Reports"
                    : label === "Report History"
                      ? "History"
                      : label === "Centralized"
                        ? "Central"
                        : label}
                </span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Main content */}
      <main className="min-w-0 pb-[calc(4.5rem+env(safe-area-inset-bottom))] pt-16 lg:ml-64 lg:pb-0 lg:pt-0">
        <div className="mx-auto w-full max-w-full px-0">
          {(title || action) && (
            <header className="mb-6 flex flex-col gap-3 px-3 sm:mb-8 sm:flex-row sm:items-center sm:justify-between sm:gap-4 sm:px-6 lg:px-8 lg:pt-6">
              <div className="min-w-0">
                {title && (
                  <h1 className="text-2xl font-bold tracking-tight text-slate-900 sm:text-3xl">
                    {title}
                  </h1>
                )}
                {subtitle && (
                  <p className="mt-1 text-sm text-slate-500 sm:mt-1.5">{subtitle}</p>
                )}
              </div>
              {action}
            </header>
          )}
          {children}
        </div>
      </main>
    </div>
  );
};

export default AppLayout;
