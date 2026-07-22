import {
  TEAM_LEAD_SUMMARY_FIELDS,
  OVERALL_STATUS_OPTIONS,
} from "../../lib/dailyUpdateSchema";

const inputCls =
  "h-9 w-full rounded-lg border border-slate-300 bg-white px-2.5 text-sm text-slate-800 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100";

const textareaCls =
  "w-full rounded-xl border border-slate-300 px-3 py-2 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100";

const SECTIONS = [
  { id: "requirements", title: "Requirements Managed" },
  { id: "review", title: "Team Profile Review" },
  { id: "delivery", title: "Recruitment Delivery" },
];

export default function TeamLeadDailyForm({
  teamLeadReport,
  setTeamLeadReport,
  errors,
  canEdit,
}) {
  const setSummary = (key, value) => {
    setTeamLeadReport((prev) => ({
      ...prev,
      recruitmentSummary: { ...prev.recruitmentSummary, [key]: value },
    }));
  };

  const setField = (key, value) => {
    setTeamLeadReport((prev) => ({ ...prev, [key]: value }));
  };

  return (
    <div className="space-y-6">
      <div className="rounded-2xl border border-slate-200 bg-slate-50 px-5 py-4 text-sm text-slate-700">
        <span className="font-semibold">Format:</span> Same as the Team Lead daily performance
        email — recruitment summary, activities, risks, and plan for tomorrow.
      </div>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-bold text-slate-900">Recruitment Summary</h2>

        {SECTIONS.map((section) => (
          <div key={section.id} className="mt-5">
            <h3 className="mb-3 text-sm font-semibold text-slate-800">{section.title}</h3>
            <div className="grid gap-3 sm:grid-cols-2">
              {TEAM_LEAD_SUMMARY_FIELDS.filter((f) => f.section === section.id).map((field) => (
                <div key={field.key}>
                  <label className="mb-1 block text-xs font-medium text-slate-600">
                    {field.label}
                  </label>
                  <input
                    type="number"
                    min={0}
                    disabled={!canEdit}
                    value={teamLeadReport.recruitmentSummary[field.key]}
                    onChange={(e) => setSummary(field.key, e.target.value)}
                    className={inputCls}
                  />
                  {errors.summaryErrors?.[field.key] && (
                    <p className="mt-0.5 text-xs text-red-600">
                      {errors.summaryErrors[field.key]}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </section>

      <section className="rounded-3xl border border-slate-200 bg-white p-6 shadow-sm space-y-5">
        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-900">
            Key Activities Completed
          </label>
          <textarea
            rows={5}
            disabled={!canEdit}
            value={teamLeadReport.keyActivitiesCompleted}
            onChange={(e) => setField("keyActivitiesCompleted", e.target.value)}
            placeholder="One activity per line — e.g. Reviewed and approved candidate profiles…"
            className={textareaCls}
          />
          {errors.keyActivitiesCompleted && (
            <p className="mt-1 text-xs text-red-600">{errors.keyActivitiesCompleted}</p>
          )}
        </div>

        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-900">
            Challenges / Risks
          </label>
          <textarea
            rows={4}
            disabled={!canEdit}
            value={teamLeadReport.challengesRisks}
            onChange={(e) => setField("challengesRisks", e.target.value)}
            placeholder="Pending client feedback, aging requirements…"
            className={textareaCls}
          />
        </div>

        <div>
          <label className="mb-2 block text-sm font-semibold text-slate-900">
            Plan for Tomorrow
          </label>
          <textarea
            rows={4}
            disabled={!canEdit}
            value={teamLeadReport.planForTomorrow}
            onChange={(e) => setField("planForTomorrow", e.target.value)}
            placeholder="Focus areas for the next working day…"
            className={textareaCls}
          />
        </div>

        <div className="max-w-xs">
          <label className="mb-2 block text-sm font-semibold text-slate-900">
            Overall Status
          </label>
          <select
            disabled={!canEdit}
            value={teamLeadReport.overallStatus}
            onChange={(e) => setField("overallStatus", e.target.value)}
            className={inputCls}
          >
            {OVERALL_STATUS_OPTIONS.map((opt) => (
              <option key={opt} value={opt}>
                {opt}
              </option>
            ))}
          </select>
          {errors.overallStatus && (
            <p className="mt-1 text-xs text-red-600">{errors.overallStatus}</p>
          )}
        </div>
      </section>
    </div>
  );
}
