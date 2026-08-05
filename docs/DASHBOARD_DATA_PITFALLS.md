# Why DB data doesn't show on the dashboards — root causes & prevention

A standing reference for the recurring bug: **rows exist in `recruitment_db`, but a
dashboard renders empty or partial.** Read this before changing anything that touches
queries, models, or dashboard data fetching.

Evidence was gathered from the backend queries, the React dashboards, and git history
(`e61ce52`, `6f41a70`, `6b46bf5`, `9dbfe2e`, `0383f8a`, `186c9d2`, `26bb0ff`, `ed8f2ec`).
All three angles point at the **same root mechanism** plus a handful of repeat offenders.

---

## The one mechanism behind almost all of it

`src/tenancy/events.py:58-92` auto-injects `WHERE <table>.company_id = <your company>`
onto **every** ORM query for a `TenantScoped` model, whenever a tenant is bound.

In SQL, `NULL = anything` is never true. Therefore:

> **Any row whose `company_id` is NULL becomes permanently invisible the moment a tenant
> is bound.** The data is in the table; the dashboard query silently skips it.

This is why "orphan" rows vanish (orphans in the local DB default to Innomax IT
Solutions). Every recurring mistake below is some way of *creating a NULL `company_id`
row* or *skipping the tenant filter entirely*.

> **Safety net (added):** `src/tenancy/events.py` now also has a write-side
> `before_flush` hook (`_stamp_tenant_on_insert`) that automatically stamps
> `company_id = <current tenant>` onto any **new** `TenantScoped` row that was created
> without one. So you no longer have to remember to set `company_id` on every insert
> inside an authenticated request — it's filled in for you. The cases it deliberately
> does **not** cover are below; treat the checklist as still mandatory for those.

---

## The recurring mistakes (with evidence)

### 1. Inserting a row without setting `company_id`
New / auto-created rows default to NULL → invisible per the mechanism above.

- `0383f8a` — auto-created `HRProfile` had no `company_id` → HR saw no candidates.
- `9dbfe2e` — registration auto-creates a placeholder `Candidate` with no `company_id`.
- `6f41a70` — jobs/candidates created with NULL `company_id`.

**Reason:** you trust the parent's `company_id` is set, but for legacy/imported rows or
auto-created side-effect rows it isn't, and the child inherits NULL.

### 2. The `if current_user.company_id:` guard around the filter
`routes/admin_routes.py:234-235, 465-467, 498-499, 587-588, 436` (pattern repeats).

```python
query = db.query(User)...
if current_user.company_id:          # <-- guard
    query = query.filter(User.company_id == current_user.company_id)
```

**Reason:** when `company_id` is falsy the filter is skipped, so the page shows ALL
tenants (a leak) — and, combined with #1, the rows you *wanted* are the NULL ones that
the auto-filter still hides on other code paths. The guard masks the real problem
instead of failing loudly.

### 3. Reading `company_id` off a stale ORM object
`26bb0ff` and `6f41a70` both had to add `db.refresh(job)`.

**Reason:** SQLAlchemy's identity map returns a cached object whose `company_id` is still
the old NULL; the child row you create from it inherits NULL.

### 4. Adding a DB/model field but not surfacing it in the API response
`186c9d2` — `job_category` existed on `JobPosting` but the jobs endpoint didn't return
it, so the frontend auto-fill broke.

**Reason:** a column change touches the model **and** every endpoint that serializes that
object **and** the frontend that reads it. Miss one layer and the UI shows blank.

### 5. Ambiguous query returning the wrong row
`9dbfe2e` — `filter(Candidate.email == email).first()` matched both the placeholder
(no job/company) and the real application row, non-deterministically.

**Reason:** filtering by a partial key when duplicates exist. Filter by the full unique
key (e.g. `email` + `job_id`).

### 6. Silent frontend fetch failures: `.catch(() => {})`
`frontend/src/pages/hr/HRDashboard.jsx:340-344, 353` (pattern repeats across the file).

```js
api.get('/hr/test-results').then(r => setTestResults(r.data || [])).catch(() => {})
```

**Reason:** a 401 (expired token), 500, or network blip is swallowed → state stays `[]`
→ the section renders its empty state ("No test results") even though the data is in the
DB and the API would have returned it. There's no signal that anything failed, so it
looks like "missing data" rather than "failed request." This is the one that most often
*looks* like a DB problem but isn't.

### 7. (Related, opposite symptom) Fully unscoped dashboard queries
`routes/advanced_features_routes.py:1712-1751`, `routes/enhanced_interviewer_routes.py:1227`
count/list across all tenants with no company filter at all. The symptom is *too much*
data, but it's the same root cause: tenant scoping applied by hand, unevenly.

---

## Prevention checklist

Run down this list before committing a change:

- [ ] **Inserting any row inside an authenticated request?** The `before_flush` hook now
  stamps `company_id` for you from the bound tenant. You still MUST set it explicitly when
  (a) creating a row for a *different* tenant, or (b) creating it where no tenant is bound
  — registration, superadmin tooling, startup seeders, or inside a `tenant_bypass()` block.
- [ ] **Writing a tenant query?** Resolve `company_id` once at the top and assume the
  filter always applies. Delete `if current_user.company_id:` guards — if it's NULL,
  raise, don't silently widen the query.
- [ ] **Changed a model/DB field?** Grep every endpoint that serializes that object and
  the frontend that reads it. A field added in the DB is not visible in the UI until all
  three layers agree on the name.
- [ ] **Reading `company_id` from an ORM object you may have touched?** `db.refresh()` first.
- [ ] **Querying for one specific entity?** Filter by the full unique key, not a partial one.
- [ ] **Writing frontend fetches?** Never `.catch(() => {})`. At minimum `console.error`
  and distinguish "request failed" from "no data." An empty dashboard should never be silent.
- [ ] **After any migration/backfill?** Check for orphans — any new per-company table with
  `company_id IS NULL` rows means future dashboards will hide them.
- [ ] **New table holding per-company data?** Add `company_id` (+ index), make it
  `TenantScoped`, and backfill existing rows in the same migration.

---

## Quick triage when "the data isn't showing"

1. Is the row's `company_id` actually set? `SELECT id, company_id FROM <table> WHERE ...`
   — if NULL, that's #1, the auto-filter is hiding it.
2. Does the API return the data when you call it directly (with a valid token)? If yes,
   the bug is frontend (#6 / #4). If no, it's a query/scoping bug (#1–#3, #5, #7).
3. Open the browser network tab — a 401/500 with an empty render is #6, not missing data.
