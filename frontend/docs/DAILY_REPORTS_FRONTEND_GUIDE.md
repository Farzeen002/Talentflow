# Daily Reports — Frontend Integration (Phase 1)

**Audience:** React frontend  
**Base URL:** `/api/v1` (via `VITE_BASE_URL`)  
**Auth:** JWT Bearer on every request — never send `recruiterId`

This app implements the Phase 1 Daily Reports REST contract: open → draft CRUD → submit → `sent` | `failed` → resend.

## UI entry points

| Route | Component | Notes |
|-------|-----------|--------|
| `/daily-update` | `src/Pages/DailyUpdate.jsx` | Kind toggle, date, recipients, submit/resend |
| `/daily-update/history` | `src/Pages/DailyReportsHistory.jsx` | Sent / draft / failed history with filters |
| — | `RecruiterReportForm.jsx` | Entry CRUD when `reportKind === "recruiter"` |
| — | `LeadReportForm.jsx` | Metrics + text lists when `reportKind === "lead"` |
| — | `ReportHistory.jsx` | Shared history list + summary cards |

Service layer: `src/services/dailyUpdates.js`  
Schema/helpers: `src/lib/dailyUpdateSchema.js`

## Kind choice (not auth role)

After login, on **Daily Reports**, the user picks **Recruiter** or **Lead Recruiter**.

- Choosing one locks the editor (and history filter) to that `reportKind` only.
- Switching to the other choice unlocks the other type instead — they are never mixed in one form.
- Choice is remembered in localStorage until changed.

## Lifecycle (frontend)

1. `POST /reports/open` `{ reportDate, reportKind }` → store `reportId`
2. Persist every Add / Edit / Delete via kind-specific endpoints; replace local state with response
3. `PATCH .../recipients` while `status === "draft"`
4. `POST .../submit` → **HTTP 200** with body `status: "sent" | "failed"` (email failure is not a 5xx when freeze succeeded)
5. If `failed` → `POST .../resend` only (content frozen)
6. History: `GET /reports` → row → `GET /reports/{reportId}` (skip open for frozen reports)

## Status UI

| Status | Edit content | Edit recipients | Actions |
|--------|--------------|-----------------|---------|
| `draft` | Yes | Yes | Submit |
| `sent` | No | No | Read-only |
| `failed` | No | No | Resend |

## Do not

- Hardcode To/CC (use open / defaults)
- Allow edit after `sent` / `failed`
- Treat submit email failure as HTTP error when body status is `failed`
- Validate `jobId` against the Jobs module (free text)
- Invent `submissionStatus` values outside the enum

## Lookback

Business dates use **Asia/Kolkata**. Client date picker allows today and up to 2 previous calendar days (`minReportDate()`); server config may differ — trust 422 messages.

## Obsolete

- `docs/DAILY_UPDATES_API.md` — previous Excel-style `/daily-updates` contract
- Unused legacy forms: `RecruiterDailyForm`, `TeamLeadDailyForm`, `HrConsolidatedView`, `RecruiterProgressSheet`
