# TalentFlow — Daily Updates API Specification

Base URL: `/api/v1`  
Auth: `Authorization: Bearer <jwt>` on all endpoints unless noted.

## Workflow summary

1. **Recruiters** submit candidate submission rows during the day.
2. **Team leads** submit their daily performance summary.
3. Data **auto-stacks** into a consolidated report — **HR does not manually compile**.
4. At **19:00 Asia/Kolkata**, a server cron builds the PDF and emails management from the HR sender mailbox.
5. After send, that day's submissions are **locked** (read-only).

---

## Roles

| Role        | `auth/me.role` | Can submit | Can view consolidated |
|-------------|----------------|------------|------------------------|
| Recruiter   | `recruiter`    | Own recruiter rows | No |
| Team Lead   | `team_lead`    | Own team-lead report | Optional read |
| HR          | `hr`           | No (read-only) | Yes |

Extend existing endpoint:

### `GET /auth/me`

```json
{
  "recruiter_id": "uuid",
  "email": "nischal@infomaticscorp.com",
  "name": "Nischal Kumar",
  "role": "recruiter",
  "oauth_status": "active",
  "created_at": "2026-01-01T00:00:00Z"
}
```

---

## 1. Schedule & email config

### `GET /daily-updates/schedule-config`

**Auth:** any authenticated user

**Response 200**

```json
{
  "reportTime": "19:00",
  "timezone": "Asia/Kolkata",
  "cutoffTime": "18:45",
  "managementTo": ["meenaz@infomaticscorp.com"],
  "managementCc": [
    "taoffshore@infomaticscorp.com",
    "poojakulal682@infomaticscorp.com"
  ],
  "hrSenderEmail": "hr@infomaticscorp.com",
  "hrSenderName": "TalentFlow HR"
}
```

---

## 2. Recruiter endpoints

### `GET /daily-updates?date={YYYY-MM-DD}&reportType=recruiter`

**Auth:** recruiter — returns **own** submission only

**Response 200**

```json
{
  "update": {
    "id": "uuid",
    "reportType": "recruiter",
    "reportDate": "2026-06-22",
    "recruiterId": "uuid",
    "recruiterName": "Nischal Kumar",
    "onLeave": false,
    "submissions": [
      {
        "requirementCode": "MZN-IC010",
        "candidateName": "RAM VIMALA KUMARAN A P",
        "jobTitle": "Estimation Engineer",
        "candidateEmail": "ramvimalakumaranap@gmail.com",
        "candidatePhone": "966-571880358",
        "locationBranch": "",
        "client": "AL-Muzain",
        "status": "Submitted",
        "submissionDate": "2026-06-22",
        "recruiterName": "Nischal Kumar"
      }
    ],
    "submittedAt": "2026-06-22T10:30:00Z",
    "updatedAt": "2026-06-22T16:00:00Z",
    "lockedAt": null
  },
  "canEdit": true,
  "emailedAt": null
}
```

**Response 404** — no submission for this date yet

**`canEdit: false` when:** consolidated email already sent, or past cutoff time

---

### `POST /daily-updates`

**Auth:** recruiter

**Request body**

```json
{
  "reportType": "recruiter",
  "reportDate": "2026-06-22",
  "onLeave": false,
  "submissions": [
    {
      "requirementCode": "MZN-IC010",
      "candidateName": "RAM VIMALA KUMARAN A P",
      "jobTitle": "Estimation Engineer",
      "candidateEmail": "ramvimalakumaranap@gmail.com",
      "candidatePhone": "966-571880358",
      "locationBranch": "",
      "client": "AL-Muzain",
      "status": "Submitted"
    }
  ]
}
```

**Validation**

| Field | Rule |
|-------|------|
| `reportDate` | Required, not in the future |
| `onLeave` | If `true`, `submissions` may be empty |
| `submissions[]` | If not on leave: ≥1 row |
| `requirementCode` | Required per row |
| `candidateName` | Required per row |
| `jobTitle` | Required per row |
| `client` | Required per row |
| `status` | Required per row |
| `candidateEmail` | Optional, valid email if present |

**Server-set fields (ignore from client):** `recruiterId`, `recruiterName` from JWT

**Responses**

| Code | Meaning |
|------|---------|
| `201` | Created |
| `409` | Already exists and locked |
| `422` | Validation error |

---

### `PUT /daily-updates/{reportDate}?reportType=recruiter`

**Auth:** recruiter — own record only

Same body as `POST`. Allowed only while `canEdit === true`.

| Code | Meaning |
|------|---------|
| `200` | Updated |
| `404` | No existing submission |
| `409` | Locked / already emailed |

---

### Allowed `status` values (recruiter rows)

```
Submitted
Submitted to LSC Portal
Submitted to LBC Portal
Submitted to SAP Fieldglass
Submitted to VTT
Scheduled for Interview
Interview Completed
Offer in Progress
Joining Confirmed
Rejected / Held
Yet to Submit
```

---

## 3. Team lead endpoints

### `GET /daily-updates?date={YYYY-MM-DD}&reportType=team_lead`

**Auth:** team_lead — own report only

**Response 200**

```json
{
  "update": {
    "id": "uuid",
    "reportType": "team_lead",
    "reportDate": "2026-06-22",
    "recruiterId": "uuid",
    "recruiterName": "Vineeth Kumar",
    "teamLeadReport": {
      "recruitmentSummary": {
        "totalRequirementsManaged": 6,
        "profilesReceivedFromRecruiters": 12,
        "profilesApprovedForSubmission": 8,
        "profilesRejectedHeld": 4,
        "profilesSubmittedToClients": 3,
        "interviewsCompleted": 1,
        "offersInProgress": 1,
        "joiningsConfirmed": 1
      },
      "keyActivitiesCompleted": "Reviewed and approved candidate profiles for client submissions.\nConducted one internal recruiter interview.",
      "challengesRisks": "Pending client feedback on critical positions is impacting interview scheduling.",
      "planForTomorrow": "Increase quality profile submissions for priority requirements.",
      "overallStatus": "On Track"
    },
    "submittedAt": "2026-06-22T12:00:00Z",
    "lockedAt": null
  },
  "canEdit": true,
  "emailedAt": null
}
```

---

### `POST /daily-updates` / `PUT /daily-updates/{reportDate}?reportType=team_lead`

**Auth:** team_lead

**Request body**

```json
{
  "reportType": "team_lead",
  "reportDate": "2026-06-22",
  "teamLeadReport": {
    "recruitmentSummary": {
      "totalRequirementsManaged": 6,
      "profilesReceivedFromRecruiters": 12,
      "profilesApprovedForSubmission": 8,
      "profilesRejectedHeld": 4,
      "profilesSubmittedToClients": 3,
      "interviewsCompleted": 1,
      "offersInProgress": 1,
      "joiningsConfirmed": 1
    },
    "keyActivitiesCompleted": "…",
    "challengesRisks": "…",
    "planForTomorrow": "…",
    "overallStatus": "On Track"
  }
}
```

**`overallStatus` enum:** `On Track` | `At Risk` | `Behind`

**Validation:** all `recruitmentSummary` fields are non-negative integers; `keyActivitiesCompleted` required.

---

## 4. HR endpoints (read-only — auto-stacked)

HR never re-enters recruiter/lead data. Consolidated data is **computed live** from all `daily_updates` for the date.

### `GET /daily-updates/consolidated?date={YYYY-MM-DD}`

**Auth:** `role === hr` (optional: management read-only)

**Response 200**

```json
{
  "reportDate": "2026-06-22",
  "stats": {
    "recruitersSubmitted": 3,
    "recruitersPending": 1,
    "totalSubmissions": 8,
    "teamLeadsSubmitted": 1,
    "onLeaveCount": 1
  },
  "performanceSummary": [
    {
      "recruiterName": "Rahul",
      "onLeave": false,
      "requirementsCovered": 3,
      "submissionsSapFieldglass": 2,
      "profilesSubmittedVtt": 0,
      "profilesSubmittedLbcPortal": 2,
      "profilesYetToSubmit": 0,
      "interviewsR1": 0,
      "interviewsR2": 0,
      "finalSelect": 0,
      "closures": 0,
      "backout": 0
    },
    {
      "recruiterName": "Prapthi",
      "onLeave": true
    }
  ],
  "allSubmissions": [
    {
      "recruiterName": "Nischal",
      "submissionDate": "2026-06-22",
      "requirementCode": "MZN-IC010",
      "candidateName": "RAM VIMALA KUMARAN A P",
      "jobTitle": "Estimation Engineer",
      "candidateEmail": "ramvimalakumaranap@gmail.com",
      "candidatePhone": "966-571880358",
      "locationBranch": "",
      "client": "AL-Muzain",
      "status": "Submitted"
    }
  ],
  "teamLeadReports": [
    {
      "recruiterName": "Vineeth Kumar",
      "teamLeadReport": { }
    }
  ],
  "emailStatus": {
    "scheduledAt": "2026-06-22T19:00:00+05:30",
    "sentAt": null,
    "status": "pending"
  }
}
```

**`emailStatus.status` enum:** `pending` | `sent` | `failed`

**Recompute trigger:** on every recruiter/team-lead `POST`/`PUT` (or query live at read time).

---

### `GET /daily-updates/consolidated/{date}/pdf`

**Auth:** hr, team_lead, or admin

**Response 200:** `Content-Type: application/pdf`

Preview of consolidated report (same template used for the 7 PM email).

---

### `GET /daily-updates/consolidated/{date}/status`

**Auth:** hr

```json
{
  "reportDate": "2026-06-22",
  "status": "sent",
  "sentAt": "2026-06-22T13:30:00Z",
  "recipients": {
    "to": ["meenaz@infomaticscorp.com"],
    "cc": ["taoffshore@infomaticscorp.com"]
  },
  "pdfUrl": "https://storage.googleapis.com/…/2026-06-22.pdf",
  "error": null
}
```

---

## 5. Automatic 7 PM email (server cron — not called from UI)

**Schedule:** `0 19 * * *` in `Asia/Kolkata`

**Internal steps:**

1. Load all `daily_updates` where `report_date = today`.
2. Build consolidated payload (same shape as `GET /consolidated`).
3. Derive `performanceSummary` KPI rows from recruiter submissions (counts by status/client).
4. Render PDF:
   - Table 1: Recruiter performance summary (all recruiters + totals row)
   - Table 2: All candidate submission rows
   - Section 3: Team lead narrative reports
5. Send email from HR service account (Gmail API / Microsoft Graph):

| Field | Example |
|-------|---------|
| From | `TalentFlow HR <hr@infomaticscorp.com>` |
| To | `meenaz@infomaticscorp.com` |
| Cc | `taoffshore@…`, team leads, etc. |
| Subject | `Daily Performance Update – 22/06/2026` |
| Body | Short intro + attached PDF |
| Attachment | `Daily-Report-22-06-2026.pdf` |

6. Upsert `consolidated_daily_reports` with `email_status: sent`, `sent_at`.
7. Set `locked_at` on all included `daily_updates`.
8. **Idempotent:** if already `sent` for that date → skip.

### Optional admin retry (not used by HR in normal flow)

### `POST /daily-updates/cron/send-daily-report`

**Auth:** `X-Cron-Secret` header or admin role only

```json
{
  "reportDate": "2026-06-22",
  "force": false
}
```

---

## 6. Helper endpoints

### `GET /daily-updates/recent?limit=10&reportType=recruiter`

**Auth:** current user — own recent submissions

```json
{
  "updates": [
    {
      "id": "uuid",
      "reportDate": "2026-06-22",
      "reportType": "recruiter",
      "submissionCount": 4,
      "onLeave": false,
      "submittedAt": "2026-06-22T16:00:00Z",
      "emailedAt": "2026-06-22T13:30:00Z"
    }
  ]
}
```

### `GET /daily-updates/{id}/pdf`

**Auth:** owner of the update

Individual preview PDF (optional).

### Optional: `POST /daily-updates/remind-pending`

**Auth:** cron or hr — email recruiters who have not submitted by 17:00.

---

## 7. MongoDB collections

### `daily_updates`

```javascript
{
  _id: ObjectId,
  recruiter_id: "uuid",           // from JWT
  report_type: "recruiter" | "team_lead",
  report_date: "2026-06-22",      // ISO date string
  on_leave: false,                // recruiter only
  submissions: [ ],               // recruiter only
  team_lead_report: { },          // team_lead only
  submitted_at: ISODate,
  updated_at: ISODate,
  locked_at: ISODate | null,      // set after 7 PM email
  consolidated_report_id: ObjectId | null
}
```

**Unique index:** `{ recruiter_id: 1, report_date: 1, report_type: 1 }`

### `consolidated_daily_reports`

```javascript
{
  _id: ObjectId,
  report_date: "2026-06-22",
  performance_summary: [ ],
  all_submissions: [ ],
  team_lead_reports: [ ],
  pdf_gcs_path: "reports/2026-06-22.pdf",
  email_status: "pending" | "sent" | "failed",
  sent_at: ISODate | null,
  recipients_to: [ ],
  recipients_cc: [ ],
  error: null,
  created_at: ISODate,
  updated_at: ISODate
}
```

**Unique index:** `{ report_date: 1 }`

---

## 8. API checklist (minimum to ship)

| # | Method | Endpoint | Caller |
|---|--------|----------|--------|
| 1 | GET | `/auth/me` (+ `role`) | Frontend |
| 2 | GET | `/daily-updates/schedule-config` | Frontend |
| 3 | GET | `/daily-updates?date=&reportType=recruiter` | Recruiter |
| 4 | POST | `/daily-updates` (`reportType: recruiter`) | Recruiter |
| 5 | PUT | `/daily-updates/{date}?reportType=recruiter` | Recruiter |
| 6 | GET | `/daily-updates?date=&reportType=team_lead` | Team Lead |
| 7 | POST | `/daily-updates` (`reportType: team_lead`) | Team Lead |
| 8 | PUT | `/daily-updates/{date}?reportType=team_lead` | Team Lead |
| 9 | GET | `/daily-updates/consolidated?date=` | HR (auto-built) |
| 10 | GET | `/daily-updates/consolidated/{date}/pdf` | HR / preview |
| 11 | GET | `/daily-updates/consolidated/{date}/status` | HR |
| 12 | GET | `/daily-updates/recent?limit=` | Frontend |
| 13 | — | **Cron @ 19:00 IST** | Server only |

**Not required:** HR manual finalize POST — stacking and email are fully automatic.

---

## 9. Frontend service mapping

| Frontend function | API |
|-------------------|-----|
| `getScheduleConfig()` | GET `/daily-updates/schedule-config` |
| `getDailyUpdateByDate(date, reportType)` | GET `/daily-updates` |
| `submitDailyUpdate(payload)` | POST `/daily-updates` |
| `updateDailyUpdate(date, payload)` | PUT `/daily-updates/{date}` |
| `getConsolidatedDailyReport(date)` | GET `/daily-updates/consolidated` |
| `downloadConsolidatedPdf(date)` | GET `/daily-updates/consolidated/{date}/pdf` |
| `listRecentDailyUpdates(limit)` | GET `/daily-updates/recent` |

Source: `src/services/dailyUpdates.js`  
Schema: `src/lib/dailyUpdateSchema.js`
