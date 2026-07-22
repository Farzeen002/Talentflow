# Backend Bug Report: Blacklisted candidates not appearing in `blacklist_only` list

**Product:** TalentFlow Recruiter Dashboard  
**Feature:** Soft blacklist (`PATCH /candidates/{id}/blacklist`)  
**Reported by:** Frontend / QA  
**Priority:** High — blacklist feature is partially broken in production UI  
**Date:** 2026-07-08

---

## Summary

Recruiters can blacklist a candidate via `PATCH /api/v1/candidates/{candidate_id}/blacklist`. The candidate is correctly **removed from normal job candidate lists** (`blacklist_only=false`), but they **do not appear on the Blacklisted tab** (`blacklist_only=true`).

**Root cause:** The job candidates list endpoint does not properly filter blacklisted candidates when `blacklist_only=true`, and/or does not include the `blacklist` object on list response rows.

---

## Expected behavior

| Action | Expected result |
|--------|-----------------|
| Recruiter blacklists a candidate | `blacklist.is_blacklisted` set to `true` in MongoDB |
| `GET /jobs/{jobId}/candidates?blacklist_only=false` | Blacklisted candidate **excluded** from list and counts |
| `GET /jobs/{jobId}/candidates?blacklist_only=true` | **Only** blacklisted candidates returned, each with `blacklist` populated |
| Blacklisted tab in UI | Shows blacklisted candidates with badge and optional reason |

---

## Actual behavior

1. **Blacklist PATCH works** — candidate disappears from "All Candidates" (active list exclusion works).
2. **Blacklisted list broken** — opening Blacklisted tab returns candidates with `blacklist: null` on every row.
3. **No row has `is_blacklisted: true`** in the list response, so the frontend correctly shows an empty tab.
4. **`data.total` on `blacklist_only=true` often equals the full active pool** (e.g. 6) instead of the blacklisted count (e.g. 1).

### Frontend console evidence

```
GET /jobs/DBA003/candidates?view=all&blacklist_only=true&page=1&limit=20

Response:
  apiTotal: 6
  rawCount: 6
  rawCandidates: [
    { candidateId: "...", name: "ABHISEK MAHALA", blacklist: null },
    { candidateId: "...", name: "Amay Ghuge", blacklist: null },
    ... (all 6 rows have blacklist: null)
  ]

After client validation:
  explicitBlacklistedCount: 0
  displayedCount: 0
```

### MongoDB document shape (correct storage)

Active candidate example:
```json
{
  "candidate_id": "423d5f8c-2879-4a23-a47f-0256264ac94e",
  "job_id": "DBA003",
  "blacklist": {
    "is_blacklisted": false,
    "reason": null,
    "blacklisted_at": null,
    "blacklisted_by": null,
    "source": "recruiter",
    "restored_at": null,
    "restored_by": null
  }
}
```

After `PATCH /blacklist`, expected:
```json
"blacklist": {
  "is_blacklisted": true,
  "reason": "Fake resume",
  "blacklisted_at": "2026-07-08T10:00:00Z",
  "blacklisted_by": "<recruiter_id>",
  "source": "recruiter"
}
```

**Note:** Data is stored correctly in Mongo; the **list API** is not surfacing or filtering on this field.

---

## Affected endpoints

### Primary

```
GET /api/v1/jobs/{job_id}/candidates
```

Query params used by frontend:
- `view=all`
- `blacklist_only=true` | `blacklist_only=false` (always sent explicitly)
- `page`, `limit`, `sort=created_at_desc`

### Related (working)

```
PATCH /api/v1/candidates/{candidate_id}/blacklist
PATCH /api/v1/candidates/{candidate_id}/unblacklist
GET  /api/v1/candidates/{candidate_id}   ← should return blacklist badge on detail
```

---

## Required fix

### 1. Filter `blacklist_only` correctly

When `blacklist_only=true`:
```python
# MongoDB filter (snake_case in DB)
{
    "job_id": job_id,
    "recruiter_id": recruiter_id,
    "blacklist.is_blacklisted": True
}
```

When `blacklist_only=false` (default active lists):
```python
{
    "job_id": job_id,
    "recruiter_id": recruiter_id,
    "$or": [
        {"blacklist.is_blacklisted": {"$ne": True}},
        {"blacklist.is_blacklisted": False},
        {"blacklist": {"$exists": False}},  # legacy rows if any
    ]
}
```

Use a single canonical check consistent with `PATCH /blacklist` writes.

### 2. Include `blacklist` on every list row

`CandidateSummary` in list responses must include:

```json
{
  "candidateId": "uuid",
  "name": "Thakur Sandeep",
  "currentRole": "...",
  "blacklist": {
    "isBlacklisted": true,
    "is_blacklisted": true,
    "reason": "Fake resume",
    "blacklistedAt": "2026-07-08T10:00:00Z",
    "blacklistedBy": "recruiter-uuid",
    "source": "recruiter"
  }
}
```

For active candidates:
```json
"blacklist": {
  "isBlacklisted": false,
  "is_blacklisted": false,
  "reason": null,
  "blacklistedAt": null
}
```

**Do not omit `blacklist` or return `null`** when the field exists on the document.

### 3. Fix `total` in list response

| Query | `total` meaning |
|-------|-----------------|
| `blacklist_only=false` | Count of **active** (non-blacklisted) candidates |
| `blacklist_only=true` | Count of **blacklisted** candidates only |

`total` must match the filter, not always the full job candidate count.

### 4. Counts on `GET /jobs/{job_id}` (optional but recommended)

If `job.counts.total` is returned on job detail, it should reflect **active** candidates only (exclude blacklisted). Add optional `job.counts.blacklisted` if useful for sidebar.

---

## Acceptance criteria

**Setup:** Job `DBA003` with 7 candidates. Blacklist 1 candidate via `PATCH /blacklist`.

| # | Test | Expected |
|---|------|----------|
| 1 | `GET .../candidates?blacklist_only=false` | 6 candidates; blacklisted ID not in list |
| 2 | `GET .../candidates?blacklist_only=true` | 1 candidate; that ID present |
| 3 | Blacklisted row includes `blacklist.isBlacklisted === true` (or `is_blacklisted`) | Pass |
| 4 | `total` on `blacklist_only=true` response | `1` |
| 5 | `total` on `blacklist_only=false` response | `6` |
| 6 | `PATCH /unblacklist` then `blacklist_only=true` | 0 candidates |
| 7 | Unblacklisted candidate reappears in `blacklist_only=false` | Pass |

---

## Frontend behavior (no change needed after fix)

The UI only displays a candidate on the Blacklisted tab when:

```javascript
candidate.blacklist?.isBlacklisted === true
// OR
candidate.blacklist?.is_blacklisted === true
```

If `blacklist` is `null` or `is_blacklisted` is missing/false, the row is **not** shown. This is intentional to avoid false positives.

---

## API request examples

**Active candidates (All / Filtered tabs):**
```http
GET /api/v1/jobs/DBA003/candidates?view=all&page=1&limit=20&sort=created_at_desc&blacklist_only=false
Authorization: Bearer <token>
```

**Blacklisted tab:**
```http
GET /api/v1/jobs/DBA003/candidates?view=all&page=1&limit=20&sort=created_at_desc&blacklist_only=true
Authorization: Bearer <token>
```

**Blacklist a candidate:**
```http
PATCH /api/v1/candidates/{candidate_id}/blacklist
Content-Type: application/json

{ "reason": "Fake resume" }
```

---

## References

- Frontend service: `src/services/candidates.js` — sends `blacklist_only` as boolean query param
- Frontend validation: `src/lib/blacklistHelpers.js` — `isCandidateBlacklisted()`
- Blacklist PATCH spec: soft-blacklist, audit fields preserved on unblacklist

---

## Contact

For questions or to verify the fix, reproduce with browser DevTools → Network tab on the Blacklisted tab and confirm the list response includes `blacklist.is_blacklisted: true` for blacklisted rows.
