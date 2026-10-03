# Tenant isolation

HireFlow is multi-tenant: every client company's data lives in the same database,
separated by a `company_id` column. This page covers what belongs to whom, how the
separation is enforced, and the rules new code must follow.

## What belongs to whom

**A person has a separate account per company.** `users` is unique on
`(company_id, email)` (`uq_users_company_email`). Someone who registers on two client
portals has two `User` rows, two passwords and two profiles, and neither company can see
the other's. There is **no shared, cross-company candidate profile**. A bare HireFlow
sign-up (no client portal) lands in the platform company (Innomax,
`src/tenancy/platform_company.py`).

**Everything about hiring is company-private**, carried on its own `company_id`:

| Data | Tables (not exhaustive) |
|---|---|
| Profile and CV the person gave this company | `candidate_profile_ext`, `candidate_work_experience`, `candidate_education`, `candidate_skills`, `candidate_certifications`, `candidate_projects` |
| Applications and pipeline | `candidates`, `candidate_stage_history` (incl. internal `__note__` rows), `ats_results`, `ats_score_events`, `screening_reviews` |
| Assessments | `interviews`, `interview_questions`, `questions` / question banks, `communication_answers`, code submissions, `proctoring_sessions` / `_violations` / `_reviews` / `_transcripts`, `assessment_explanations` |
| Decisions | `interview_feedback`, `offers` (salary, bonus) |
| Talent pool | `resume_pool`, `resume_pool_attachments` |
| Company operations | `job_postings`, `departments`, HR/interviewer profiles, mail templates and mailboxes, `email_log`, notifications, support requests |

Files follow their rows: a resume or evidence frame belongs to the company that owns the
row referencing it.

**One sanctioned cross-company read.** The admin Users tab lists the company's own users
*plus* people whose `User` row sits in another company but who have an application
(`candidates` row) in this one, e.g. a platform sign-up whose application is recorded here.
`routes/admin_routes.py::_visible_users_query` does this with an explicit bypass. The
`or_(User.company_id == me, User.id IN (applicants to me))` it applies is then the only
scoping, and both arms must stay.

## How it is enforced

Two layers, both required.

**1. Explicit filters in every handler.** Every query a route runs names the company:
`.filter(Model.company_id == current_user.company_id)`, or it reaches the row through a
parent it already scoped. This is the primary control. Route tests cover it per endpoint
(`test/test_tenant_isolation*.py`, `test/test_hr_tenant_scoping.py`).

**2. The automatic filter** (`src/tenancy/events.py`), a safety net for the query someone
forgets:

- `get_current_user` (`src/auth.py`) binds the caller's company into a contextvar. It is
  deliberately `async def`: FastAPI runs a sync dependency in a threadpool on a *copy* of
  the context, so a value set there never reaches the endpoint. That is how the filter sat
  dormant on every request until `test_tenant_binding_reaches_endpoint` caught it.
- While a company is bound, a `do_orm_execute` hook adds
  `with_loader_criteria(TenantScoped, company_id == bound)` to every ORM SELECT, UPDATE
  and DELETE. That covers `db.query()`, `select()`, `db.get()`, relationship lazy loads,
  joins, and bulk `.update()` / `.delete()`. A model opts in by subclassing
  `TenantScoped` (`src/tenancy/mixin.py`).
- A `before_flush` hook stamps the bound company onto new `TenantScoped` rows that don't
  set one. An explicit value is never overwritten.
- Naming another company in a filter does **not** escape it:
  `.filter(Job.company_id == other)` returns nothing inside a tenant request.

**What the automatic filter does not cover:**

- raw SQL (`text()`, `exec_driver_sql`): filter by hand
- anything with no tenant bound: superadmin requests (they bind `None`), public routes,
  background workers, scripts and the startup code. These must scope explicitly: the
  email worker takes `company_id` from its payload, for example.
- models that don't subclass `TenantScoped` (`companies`, `tenant_config`,
  `company_mailboxes`, `pipeline_step_configs`, `system_config`, …): filter by hand

**Bypasses** clear the filter. Use one only for a reviewed reason and say why in a comment:

```python
from src.tenancy import tenant_bypass
with tenant_bypass():                                            # whole block
    ...
db.query(User).execution_options(_tenant_bypass=True)           # one query
```

Current uses include per-user profile lookups by `user_id` (one profile per user, and a
legacy NULL-company row must be re-homed, not duplicated), file downloads, the audit log
and the Users tab above. List them with
`grep -rn "tenant_bypass\|_tenant_bypass" backend/routes backend/src`.

Refreshing an object already in the session is never filtered (`is_column_load`).
Otherwise a row loaded under a bypass would raise `ObjectDeletedError` when it expires.

## Support access (superadmin)

A superadmin binds no tenant, so the automatic filter does nothing for them. What stops
platform staff reading client data is the **support-access grant**
(`src/services/audit_log.py`):

- **Grants.** A client's admin grants access from Settings → Support access, with a
  reason and a lifetime (24 h by default, 7 days at most), and can revoke it at any time.
  A grant covers that one company only.
- **Needs a grant.** Anything holding candidate, interview or assessment data: an
  interview's questions and answer keys (`/mcq/...`, by interview or by question), a
  company's non-admin users, coding problems and submissions by id (HTTP and the live
  WebSocket), proctoring reports, evidence, reviews and live sessions, grading routes, and
  **adding an admin to a company that already has one**. Every such route calls
  `require_support_access` (or `staff_can_access_interview` with `db` and the request).
- **Needs no grant.** Company records and branding, aggregate counts, support tickets
  the client opened, the platform re-grade sweep, and adding the **first** admin to a
  company that has none (onboarding, or recovering an admin-less company).
- **Refused outright.** Anything that lists records across every tenant: question-bank
  search, `/mcq/sample`, `/judge/problems`, `/judge/submissions`. A superadmin opens one
  company's record, by id, under that company's grant (`refuse_superadmin_listing`).
  The interview-authoring routes (`/api/v1/interviewer/interviews/{id}`, templates) don't
  admit superadmin at all.
- **Audit.** Every request on the superadmin, support and database routers, every
  grant-gated access, every refusal, and every grant or revocation is an `audit_log` row.
  Rows carry the method, path and IP. Reads are `superadmin.read` and anything else is
  `superadmin.write`. Admin creation is `superadmin.create_admin`, and the company's
  existing admins get an in-app notice. SQL-console queries are
  `superadmin.sql_query` with the query text, and the platform re-grade sweep is
  `superadmin.regrade`. Ticket requests are filed under the ticket's company.
- **Fails closed.** If the audit row for a superadmin access can't be written, the
  access is refused with a 503. Other callers of `record_audit` keep its never-raise
  behaviour; pass `required=True` for anything that must not happen unrecorded.
- **Where the client sees it.** `GET /api/v1/support/access-log` (Settings → Support
  access) lists every row for the admin's own company.

New code that serves client data to staff: call `require_support_access(db, request,
user, company_id, resource)` before returning it, and don't give superadmin a path that
skips the company filter.

## Rows with no company

A row with `company_id IS NULL` belongs to no tenant, so the filter hides it from every
company, including the one it came from. These rows exist because, until the binding fix,
the insert-time stamp never ran during a request and several code paths omitted
`company_id`. Don't "fix" visibility with an `OR company_id IS NULL` branch: that shows the
row to *every* company. Fix the data:

```bash
python scripts/backfill_orphan_company_ids.py           # dry run: what would change
python scripts/backfill_orphan_company_ids.py --apply   # fill NULLs from each row's parent
python scripts/verify_tenant_isolation.py               # must end PASS
```

## Rules for new code

1. A new table holding company data gets a `company_id` column and subclasses
   `TenantScoped`, and its migration backfills `company_id` from the parent.
2. Scope explicitly in the route anyway. The automatic filter is a net, not the design.
3. Set `company_id` explicitly on insert, from the parent row or `current_user`. Background
   code has no tenant bound and gets no stamp.
4. Never add an `IS NULL` branch to a company check (see above).
5. A cross-company read uses `tenant_bypass()` / `_tenant_bypass`, re-applies its own
   scoping, and explains itself in a comment.
6. Test with **two** companies: company A's token must get 404 (not 403, which confirms the
   row exists) for company B's ids. See `test/test_tenant_isolation.py::two_companies`.
7. Background jobs carry `company_id` in their payload and never bind the contextvar: an RQ
   worker reuses one process for every job, so a missed reset would leak one company's
   scope into the next.

## Deploying the filter activation

The binding fix turns the automatic filter on for every request, so NULL-company rows
disappear from the UI the moment it deploys. **Backfill first**, on the new code but
before restarting the services (docs/DEPLOY_UPDATE.md, Step 4):

1. `alembic upgrade head`, then `schema_diff.py`.
2. `backfill_orphan_company_ids.py` (dry run), read it, then `--apply`.
3. `verify_tenant_isolation.py`: must end `PASS`. Its `total_null` rows are what will
   vanish; any still there have no parent to inherit a company from and need a person to
   decide.
4. Restart (Step 8).
