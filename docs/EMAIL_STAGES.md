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
> mailbox is inert and everything pointed at it falls back to the company default.
> This is the single most common reason mail goes out from the wrong address. It
> shows up as `no password set` in Admin → Mail Routing and in the logs.

## Mail that is not stage-configurable

| Mail | Recipient | Sender |
|---|---|---|
| Verification OTP | Candidate | **`Auth` mailbox, by name** — `resolve_auth_sender` (`src/auth.py`, `auth_routes.py`) |
| Resend OTP | Candidate | **`Auth` mailbox, by name** — `resolve_auth_sender` (`auth_routes.py`) |
| Password reset OTP | Any user | **`Auth` mailbox, by name** — `resolve_auth_sender` (`auth_routes.py`) |
| Admin welcome | New tenant admin | Platform `official@` (`superadmin_routes.py:340`) |
| Support — new ticket | Superadmins | **env `SMTP_*` only** (`support_routes.py:186`) |
| Support — admin replied | Superadmins | **env `SMTP_*` only** (`support_routes.py:214`) |
| Support — superadmin replied | Client admin | Company default (`support_routes.py:234`) |

**`Auth` is matched by name, not by a stage mapping.** `AUTH_MAILBOX_NAME`
(`src/services/email_service.py`, default `"Auth"`) looks up a mailbox called
exactly that. Create it with that name and map it to nothing; OTP finds it. Rename
it and auth mail drops to the company default — a real incident on this deployment,
where it went out from a personal address for that reason.

All three auth call sites share one chain, `resolve_auth_sender`: **named `Auth`
mailbox → the company's SMTP override → env `SMTP_*`**. They used to inline it and
had drifted, so registration and resend could send one user two codes from two
different addresses.

The drop to the default is no longer silent. A mailbox that exists but cannot send
logs `Mailbox 'Auth' unusable for company <id>: <reason>` and **Admin → Mail
Routing** names the same reason on the auth layer. A company that has no `Auth`
mailbox at all logs nothing — that is a configuration choice, not a fault.

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

A mailbox is used only when it is active, has an SMTP user, **and** has a password
— `mailbox_unusable_reason` (`src/services/pipeline_notifications.py`) is the single
place that decides, shared by the send path, the auth path and this panel so they
cannot disagree. Fail any one and the company default is used instead, with the
reason logged and shown.

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
