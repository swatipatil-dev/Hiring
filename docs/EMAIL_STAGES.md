# Which stages send email, and from which address

Reference for whoever configures Admin → Email Settings. How sending works —
senders, routes, the outbox, blocked mail — is in [EMAIL_API.md](EMAIL_API.md);
this one is about what the product actually sends.

Derived from the code, not from the UI. A row on the HR → Pipeline Notifications
screen does **not** prove anything sends mail for it — the authority is which
step keys reach `resolve_step_dispatch` / `resolve_step_sender`:

```bash
grep -rn 'resolve_step_dispatch(\|resolve_step_sender(' backend/routes backend/src/services
```

Re-run that when stages are added. Anything absent from its output sends nothing.

## Stages that send mail

Each of these resolves its sender through the step's route (`email_routes`, edited
in HR → Pipeline Notifications), so each can point at a different mailbox. A stage
left unmapped uses the company default. A stage switched off sends nothing. A
stage whose mailbox cannot send is **blocked** — it does not quietly use another
address — and Admin → Mail Routing says why.

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

## The addresses we actually create

`scripts/seed_innomax_mailboxes.py` creates **four** mailboxes on
`mail.innomaxskills.com`, port `465` (465 auto-selects implicit SSL), each taking
its own mailbox password:

| Mailbox | Address | Stages |
|---|---|---|
| **Auth** | `auth@innomaxskills.com` | OTP + password reset — **assign to no stage**, see below |
| **Assessments** | `assessments@innomaxskills.com` | `test_invitation`, `mcq_result` |
| **Scheduling** | `scheduling@innomaxskills.com` | `interview_scheduled`, `panel_invite` |
| **Offers** | `offers@innomaxskills.com` | `offer_letter` |

The remaining five stages — `application_received`, `offer_accepted`,
`offer_rejected`, `candidate_rejected`, `custom_mail` — are left unmapped **on
purpose**. An unmapped stage sends from the company default, which is correct until
a mailbox exists for it. Do not map a stage to a mailbox that is not in
`MAILBOXES`.

If you later want them separated, the natural split is `Careers` for
`application_received`, `HR` for the two staff-facing offer replies, and
`Notifications` for `candidate_rejected`. Fewer mailboxes means fewer passwords to
keep working; more mailboxes means more per-mailbox hourly headroom, since the
rate-limit window is keyed on the sending account
(`src/mailq/ratelimit.py:18-20`).

> **A seeded mailbox cannot send until someone types its password.** The script
> inserts `smtp_pass=None` deliberately (`seed_innomax_mailboxes.py:90-95`) — it has
> no way to know the passwords. Until one is entered in Admin → Email Settings the
> mailbox shows *Needs attention*, and any stage pointed at it is **blocked** (it
> used to fall back to the company default, which is how mail went out from the
> wrong address). The settings migration only routed stages to mailboxes that could
> already send, so the upgrade itself did not block anything that worked before.

## Mail that is not stage-configurable

| Mail | Recipient | Sender |
|---|---|---|
| Verification OTP | Candidate | `auth.verify_email` route → company default → **platform auth route** (`src/auth.py`, `auth_routes.py`) |
| Resend OTP | Candidate | same as verification |
| Password reset OTP | Any user | `auth.password_reset` route → company default → **platform auth route** |
| Admin welcome | New tenant admin | Platform company's `platform.tenant_welcome` route — Innomax Official (`superadmin_routes.py`) |
| Support — new ticket | Superadmins | Platform company's `support.ticket_to_platform` route — Platform System |
| Support — admin replied | Superadmins | Platform company's `support.reply_to_platform` route |
| Support — superadmin replied | Client admin | Platform company's `support.reply_to_admin` route (it used to be the client's own default) |

**Auth mail has its own chain**, shared by all three call sites through
`resolve_auth_sender`: the company's `auth.*` route → the company default → the
**platform company's auth route**. The last step is the only place one company's
mail uses another company's sender, and only for these codes, so a company that
has not connected a mailbox can still register users. The platform's auth route is
seeded from `SMTP_*` (`src/email_api/bootstrap.py`) and shown in Mail Routing.

Auth mail is no longer found by mailbox *name*. The settings migration turned a
working mailbox called `AUTH_MAILBOX_NAME` (default `"Auth"`) into the company's
`auth.*` routes, so renaming a mailbox no longer changes where codes come from. A
company auth sender that exists but cannot send blocks the codes — it is a fault
to fix, shown in Mail Routing — rather than falling through to the platform.

## Verifying a mailbox actually works

Saving a mailbox now checks it: the host must be a public mail server on port 465
or 587, and the server must accept the TLS handshake and the login. A mailbox that
fails is saved with its error (*Connection failed* / *Needs attention*), so the
screen says what to fix. Then:

1. Press **Test** on the mailbox row. It sends one real message synchronously
   through that mailbox — to its own address or a user of your company — and
   reports the server's answer (`POST /api/v1/admin/mailboxes/{id}/test`).
2. Map the stages and check **Admin → Mail Routing**. It shows, per stage, the
   address mail goes out from, or **Blocked** with the reason.

`senders.usable_reason` (`src/email_api/senders.py`) is the single place that
decides whether a mailbox can send — enabled, not deleted, connection active with
a stored password — shared by the policy, the delivery worker and these screens.

Note "App Password" is a Gmail concept — a token generated because 2FA blocks
plain passwords. Mailboxes on `mail.innomaxskills.com` (or any cPanel host) take
the mailbox's own password.

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
