import { useNavigate } from "react-router-dom";
import { ArrowLeft, History } from "lucide-react";
import ReportHistory from "../components/dailyUpdate/ReportHistory";
import { loadSavedKind, kindLabel } from "../lib/dailyUpdateSchema";

/**
 * Dedicated Daily Reports history page — follows kind locked on Daily Reports.
 */
const DailyReportsHistory = () => {
  const navigate = useNavigate();
  const lockedKind = loadSavedKind();

  return (
    <div className="flex flex-col gap-4 p-3 pb-10 sm:gap-6 sm:p-6 lg:p-8">
      <section className="rounded-2xl border border-slate-200 bg-white px-4 py-5 shadow-sm sm:rounded-3xl sm:px-6 sm:py-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="mb-2 inline-flex items-center gap-2 text-xs font-medium text-slate-500">
              <History className="h-3.5 w-3.5" />
              Daily Reports
            </div>
            <h1 className="text-xl font-bold text-slate-900 sm:text-2xl">
              Report history
            </h1>
            <p className="mt-1 max-w-2xl text-sm text-slate-600">
              {lockedKind
                ? `${kindLabel(lockedKind)} reports only — sent, drafts, and failed. Change type on Daily Reports.`
                : "Choose Recruiter or Lead on Daily Reports first, then history will show that type only."}
            </p>
          </div>
          <button
            type="button"
            onClick={() => navigate("/daily-update")}
            className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to editor
          </button>
        </div>
      </section>

      <ReportHistory />
    </div>
  );
};

export default DailyReportsHistory;
