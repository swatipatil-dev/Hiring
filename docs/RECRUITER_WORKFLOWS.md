# Recruiter Workflows

Six features that sit around the hiring pipeline. The backend lives in
`routes/recruiter_workflow_routes.py` (`/api/v1/hr/workflow`),
`routes/scorecard_routes.py` (`/api/v1/scorecards`) and one service per feature in
`src/services/`. Tests are in `test/test_recruiter_workflows.py`. The schema change is
the single migration `r1w2f3l4o5w6`.

| Feature | Service | Where recruiters see it |
|---|---|---|
| Duplicate detection & merging | `candidate_duplicates.py` | Workflow → Duplicates; the stage tracker (click a Pipeline row) |
| Stages, owners, deadlines, reminders | `pipeline_stages.py` | Workflow → Stages & owners, Workflow → Pending actions |
| Bulk actions with confirmation & audit | `bulk_actions.py` | Pipeline (tick rows), Workflow → Bulk history |
| Source tracking | `candidate_source.py` | Pipeline "Source" column, stage tracker, Workflow → Sources |
| Independent scorecards | `scorecards.py` | Interviewer: "My Scorecard"; HR: "Panel Scorecards" on an interview |
| Status updates & withdrawal | `candidate_status.py` | Candidate: My Applications |

## Duplicates and merging

**Detection.** The database already refuses a second application with the same email
for the same job. What gets through is the same person under a different email, or
someone HR typed in by hand. Two applications match on any of:

- normalised email (case-insensitive; for Gmail, dots and `+tag` are ignored);
- the last 10 digits of the phone number;
- the LinkedIn profile path;
- the same full name on the same job. This is a weak match and is labelled as one.

Matches across different jobs are shown for context only. Two applications to two jobs
are separate applications, not duplicates.

**Merging is same-job only, and it is controlled:**

1. Preview shows every field that differs, plus blockers and warnings.
   - **Blockers:** the application being merged away has an offer or is hired.
2. HR picks which value survives for each field. The kept application always keeps its
   own email. To keep the other email, use Swap.
3. The merged row is **kept, never deleted**. It gets `merged_into_id` and stage
   `merged`, and it is hidden from `/hr/jobs/{id}/candidates`, the duplicate scan, and
   bulk actions.
4. Its interviews and internal notes move to the kept application.
5. A `candidate_merges` row snapshots both sides, so **Undo** restores them exactly and
   moves interviews and notes back.

Any new candidate list should filter `Candidate.merged_into_id.is_(None)`.

## Stages, owners, deadlines and reminders

**Configuration.** A company's stages are the built-in list overlaid with its
`pipeline_stage_configs` rows. A row can relabel, reorder, set an owner, set a
deadline (`sla_days`) or deactivate a stage, and HR can add custom stages. Built-in
stages can only be deactivated, because other code writes them. Final stages (hired,
rejected, withdrawn) cannot have a deadline.

**Deadlines are computed, never stored.**
- A candidate entered their stage at the latest history row whose `to_stage` is their
  current stage. The deadline is that time plus `sla_days`.
- Every existing code path that moves candidates therefore keeps deadlines correct
  without knowing they exist.
- A stage visit is *due soon* within 24h of its deadline and *overdue* after it.

**Owner.** The stage's configured owner if there is one; otherwise the HR who owns the
candidate's job.

**Reminders.** `POST /api/v1/hr/workflow/reminders/run`
- Sends each owner one in-app notification and one digest email.
- Each stage visit is reminded once when it becomes due soon, then at most once per 24h
  while it is overdue (tracked in `stage_reminder_log`).
- Authentication:
  - the `WORKFLOW_REMINDER_TOKEN` header sweeps every company;
  - an HR/admin login sweeps only that company.
- Run it hourly with `deploy/cpanel/hireflow-reminders.timer`.
- `WORKFLOW_REMINDER_EMAIL=0` keeps the in-app notifications and skips the emails.

## Bulk actions

The actions are Shortlist, Reject, Move to stage and Add internal note, on up to 500
candidates.

**Two steps:**

1. **Preview** (`POST /bulk/preview`) classifies each selected candidate, then stores the
   batch in `bulk_operations`.
   - **Eligible:** will be changed.
   - **Skipped, with a reason:** not on your jobs, merged, withdrawn, hired, or already in
     the target stage.
   - **Warnings:** a resume flagged for manual review, an outstanding offer, or a rejected
     candidate being reopened.
2. **Execute** (`POST /bulk/{id}/execute`) needs `confirm_count` equal to the previewed
   eligible count.
   - It only runs within 15 minutes of the preview, and only for the person who previewed.
   - It re-checks every candidate, so anyone whose state changed in between is skipped and
     reported.

**What an executed batch leaves behind:**
- the `bulk_operations` row: who ran it, what they were shown, and the per-candidate
  outcome;
- one stage-history row per candidate, naming the batch;
- an `audit_log` row.

Rejection emails go out after the commit and can be switched off per batch. HR see
their own batches in Workflow → Bulk history; admins see everyone's.

The older `POST /api/v1/advanced/candidates/bulk-action` is unchanged for existing callers.

## Source tracking

`Candidate.source` is one of `direct`, `referral`, `job_portal`, `agency`,
`talent_pool` or `other`. Two further fields add context:
- `source_detail` names the portal, agency or campaign;
- referrals also carry `referrer_name` and `referrer_email`.

**How the source is set:**
- **Apply and Quick Apply:** the candidate is asked "How did you hear about this job?".
  A shared link can pre-fill it: `?source=referral&ref=Ravi`, or `?utm_source=linkedin`.
  Common aliases (LinkedIn, Naukri, …) map to `job_portal`. An unknown value is rejected
  with a 400 rather than guessed.
- **Moving someone from the talent pool** records `talent_pool` and keeps the pool's
  channel as the detail.
- **HR can correct it** from the stage tracker. Every change is audited.

Rows created before this feature have `source` NULL and are reported as "Not recorded".
They are deliberately not backfilled.

## Independent scorecards

Every panellist (the lead plus everyone in `panel_members`) writes their own
`interview_scorecards` row. The existing single `InterviewFeedback` row per interview
is unchanged and is still the consolidated result the rest of the app reads.

**Rules:**
- A draft is visible only to its author, not even to HR.
- A submitted scorecard is locked.
- **A panellist sees nobody else's scores for an interview until they submit their own.**
  The lead's existing consolidated feedback counts as their submission.
- HR on the job and admins who are not on the panel see every submitted scorecard.
- When every panellist has submitted, the job's HRs are notified.

The same rule now protects the candidate-level views (`/advanced/candidates/{id}/profile`,
`/scorecard`, `/compare`), which used to show an interviewer every colleague's scores
first. It also means interviewers can no longer filter candidate search by hire
recommendation. Any new view that shows feedback to interviewers must go through
`scorecards.feedback_visibility`.

## Candidate status and withdrawal

Candidates now see a plain-language status ("Under review", "Assessment",
"Interviewing"…) and a one-line explanation instead of internal stage keys.

`POST /api/v1/candidate/applications/{id}/withdraw` needs `confirm: true` and takes an
optional reason. Withdrawing:
- sets stage `withdrawn` and records when and why;
- cancels upcoming interviews and tests;
- withdraws a draft or sent offer;
- notifies every HR on the job.

It is refused once the application is hired, rejected, withdrawn or merged. HR see the
withdrawal and reason in the stage tracker.

## Deploying

1. Run `alembic upgrade head`.
2. Rebuild the frontend.
3. Optionally, install the reminder timer and set `WORKFLOW_REMINDER_TOKEN`; see
   [DEPLOY_UPDATE.md](DEPLOY_UPDATE.md) §5 for copying new units.

Nothing needs a backfill.
