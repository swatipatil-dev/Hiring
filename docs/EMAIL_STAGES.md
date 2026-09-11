# Which stages send email, and from which address

Reference for whoever configures Admin → Email Settings. Every other mail
document here is deployment- or ops-focused; this one is about what the product
actually sends.

Derived from the code, not from the UI. A row on the HR → Pipeline Notifications
screen does **not** prove anything sends mail for it — the authority is which
step keys reach `resolve_step_dispatch` / `resolve_step_sender`:

```bash
grep -rn 'resolve_step_dispatch(\|resolve_step_sender(' backend/routes backend/src/services
```

Re-run that when stages are added. Anything absent from its output sends nothing.

## Stages that send mail

Each of these resolves its sender through `pipeline_step_configs.mailbox_id`, so
each can point at a different mailbox. A stage left unmapped falls back to the
company default and still sends.

| Stage (`step_key`) | Fires when | Recipient | Call site |
|---|---|---|---|
| `application_received` | Candidate applies or quick-applies | Candidate | `candidate_routes.py:613`, `:848` |
| `test_invitation` | HR sends a test invite; round-2 invite | Candidate | `hr_routes.py:3456`, `:3612` |
| `mcq_result` | Candidate submits the MCQ test | Candidate | `evaluation_routes.py:199` |
| `interview_scheduled` | HR schedules an interview (two endpoints) | Candidate | `hr_routes.py:4459`, `:5073` |
| `panel_invite` | Same schedule action, one per panellist | **Interviewers (staff)** | `hr_routes.py:4518` |
| `offer_letter` | HR creates an offer | Candidate | `hr_routes.py:5327` |
| `offer_accepted` | Candidate accepts an offer | **HR (staff)** | `evaluation_routes.py:412-421` |
| `offer_rejected` | Candidate declines an offer | **HR (staff)** | `evaluation_routes.py:412-421` |
| `candidate_rejected` | Rejection, including bulk auto-reject | Candidate | `hr_routes.py:3458`, `email_service.py:835` |
| `custom_mail` | HR Compose Mail dialog | Whoever HR types | `hr_routes.py:5901` |

**Three go to staff, not candidates** — `panel_invite`, `offer_accepted`,
`offer_rejected`. Give them an internal-looking sender rather than a careers
address.

## Suggested addresses

Seven distinct mailboxes cover all ten stages. Names and addresses match
`scripts/seed_innomax_mailboxes.py`; host `mail.innomaxsol.com`, port `465`
(port 465 auto-selects implicit SSL), each with its own mailbox password.

| Mailbox | Address | Stages |
|---|---|---|
| **Auth** | `auth@…` | OTP + password reset — **assign to no stage**, see below |
| **Careers** | `careers@…` | `application_received` |
| **Assessments** | `assessments@…` | `test_invitation`, `mcq_result` |
| **Scheduling** | `scheduling@…` | `interview_scheduled`, `panel_invite` |
| **Offers** | `offers@…` | `offer_letter` |
| **HR** | `hr@…` | `offer_accepted`, `offer_rejected` |
| **Notifications** | `notifications@…` | `candidate_rejected` |

Consolidating to four also works — `Auth`, `Careers` (all candidate-facing
pre-offer mail), `Offers`, and `HR` (the three staff-facing stages). Fewer
mailboxes means fewer passwords to keep working; more mailboxes means more
per-mailbox hourly headroom, since the rate-limit window is keyed on the sending
account (`src/mailq/ratelimit.py:18-20`).

## Mail that is not stage-configurable

| Mail | Recipient | Sender |
|---|---|---|
| Verification OTP | Candidate | **`Auth` mailbox, by name** (`src/auth.py:317`, `auth_routes.py:178`) |
| Password reset OTP | Any user | **`Auth` mailbox, by name** (`auth_routes.py:350`) |
| Admin welcome | New tenant admin | Platform `official@` (`superadmin_routes.py:340`) |
| Support — new ticket | Superadmins | **env `SMTP_*` only** (`support_routes.py:186`) |
| Support — admin replied | Superadmins | **env `SMTP_*` only** (`support_routes.py:214`) |
| Support — superadmin replied | Client admin | Company default (`support_routes.py:234`) |

**`Auth` is matched by name, not by a stage mapping.** `AUTH_MAILBOX_NAME`
(`src/services/email_service.py:200`, default `"Auth"`) looks up a mailbox called
exactly that. Create it with that name and map it to nothing; OTP finds it. Rename
it and OTP silently drops to the company default — a real incident on this
deployment, where auth mail went out from a personal address for that reason.

**Support mail to superadmins ignores mailboxes entirely.** Those two call sites
pass no `smtp_config`, so they always use the env `SMTP_*` account. Creating a
`Support` mailbox will not change them.

## Verifying a mailbox actually works

Saving a mailbox verifies nothing — the password is encrypted and stored exactly
as typed. Two things follow:

1. Press **Test** on the mailbox row in Admin → Email Settings. It sends
   synchronously using that mailbox's own credentials and reports the real SMTP
   error (`POST /api/v1/admin/mailboxes/{id}/test`). A wrong password comes back
   as `535 Authentication failed` immediately.
2. Then map the stages and check **Admin → Mail Routing**. It shows the effective
   sender per stage and an amber `fallback_reason` when a mailbox is unusable.
   That panel proves *configuration*; only the Test button proves the *password*.

A mailbox is used only when it is active, has an SMTP user, **and** has a
password (`src/services/pipeline_notifications.py:86-101`). Fail any one and the
stage silently uses the company default.

Note "App Password" is a Gmail concept — a token generated because 2FA blocks
plain passwords. Mailboxes on `mail.innomaxsol.com` (or any cPanel host) take the
mailbox's own password.

## Removed: `shortlisted`

There was a "Candidate Shortlisted" row on the Pipeline Notifications screen. It
was configurable — mailbox, CC list, enabled toggle — and could never send
anything: `"shortlisted"` was never passed to either resolver, and its template
`send_candidate_shortlisted` has no call sites. It has been taken out of
`STEP_LABELS` so nobody assigns a mailbox to it.

Existing `pipeline_step_configs` rows with that key are inert, not broken —
`is_valid_step` rejects unknown keys and the save handler skips them
(`hr_routes.py:6135`). To turn the stage on later you need all three: a call site
where candidates are shortlisted, a `resolve_step_sender` lookup there, and the
key back in `STEP_LABELS`.
