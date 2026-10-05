# The email API

Every email HireFlow sends — stage notifications, verification codes, HR's
ad-hoc mail, and mail a customer's own application sends through the API — goes
through one pipeline in `backend/src/email_api/`. This document is the map. For
*which stage sends what*, see [EMAIL_STAGES.md](EMAIL_STAGES.md).

**The rule the whole design serves:** every accepted email has a company, an
authorised sender, a fixed delivery connection, an initiating identity and a
durable record. No company's mail takes its sender from the environment.

## Vocabulary

| Thing | Table | What it is |
|---|---|---|
| Connection | `email_connections` | *How* mail leaves: one SMTP account (host, port, TLS mode, login, encrypted password). `type` already allows `google_oauth`, `microsoft_oauth`, `verified_domain` for later phases. |
| Sender | `email_senders` | An authorised From identity on a connection: the login address (`primary`) or another address on the same domain (`approved_alias`). Migrated rows are `legacy_unverified`. In the admin UI a sender is a "mailbox"; migrated senders keep their old mailbox id. |
| Route | `email_routes` | Per company and event: which sender, and whether the event is on at all. `event_type='__default__'` is the company default sender. |
| Message | `email_messages` | One accepted email: sender and connection frozen at acceptance, content encrypted, status. |
| Recipient / attempt | `email_recipients`, `email_attempts` | Per-addressee outcome; each try at handing the message over. |
| Outbox | `email_outbox` | Committed work still to publish to the queue. |
| Suppression | `email_suppressions` | Addresses a company must not mail again (hard bounce, complaint, manual). |
| API key | `api_keys` | Hashed `hf_live_…` / `hf_test_…` credential for a company's own software. |

Ownership is enforced by the database: every child table references
`(company_id, parent_id)`, so a route, message or recipient cannot point at
another company's row even if a service check is missed.

## Who sends as whom — the sender policy

`policy.resolve_sender(db, company_id, event_type, requested_sender_id=…)`, in order:

1. The event must exist; `platform.*` and `support.*` events belong to the platform company only.
2. A route for the event that is **switched off** → *disabled*: nothing is created or sent.
3. An explicitly chosen sender (HR's "Send from", an API `sender_id`) must be the company's, enabled, on an active connection — otherwise the email is **blocked**.
4. The event's routed sender (blocked if it cannot send).
5. The company default (blocked if it cannot send).
6. For `auth.verify_email` / `auth.password_reset` only: the **platform company's** route for that event — so users of a company with no mailbox can still register and reset passwords.
7. Otherwise blocked: `no_sender_configured`.

**There is no fallback.** A sender that exists but cannot send blocks the
email; it is never swapped for another address. Blocked messages are stored
with a reason (`status_reason`) and shown in Admin → Mail Routing and HR's email
log. `scripts/retry_blocked_messages.py --reason …` re-sends them after a fix.

The platform's own mailboxes (`SMTP_*`, `INNOMAX_HR_*`, `INNOMAX_OFFICIAL_*`) are
seeded at startup as the platform company's connections and routes
(`bootstrap.py`, also `scripts/seed_platform_email.py`). After that the
environment is not read at send time.

## The path of one email

```
caller ──► resolve_sender ──► messages.submit ──(commit)──► outbox ──► RQ ──► worker.deliver_message ──► adapter
             (policy)          message+recipients            publish       {message_id,     revalidate, rate      SMTP / Null
                               +outbox row, one txn                          tenant_id}       limit, decrypt, send
```

- **`submit`** validates (no CR/LF in headers, ≤50 recipients, ≤1 MB body,
  template XOR raw content), applies idempotency, renders, encrypts the content and
  writes message + recipients + outbox row in one transaction. It never talks to a
  mail server.
- **Publishing happens after the outermost transaction commits** (a rolled-back
  request never leaves an email behind). Not on `after_commit`: SQLAlchemy fires
  that for every SAVEPOINT release.
- **The job payload is `{message_id, tenant_id}`** — no credential, no content.
- **The worker** locks the message, re-checks the frozen sender and connection
  (anything changed or broken → blocked), applies the hourly limit, commits a send
  lease, decrypts the password immediately before the call, and records the
  outcome with a guarded update.
- **The dispatcher** (`ROLE=email-dispatcher`, `python -m src.email_api.outbox --loop`)
  republishes rows that are due: committed while Redis was down, retries and
  deferrals that came due, and jobs lost to a Redis flush or a dead worker.

### When Redis is down

Nothing is sent inline, except verification and password-reset codes
(`priority='auth'`), which go through the same worker function in-process, rate
limit and attempt record included. Everything else waits in the outbox until Redis
is back and the dispatcher publishes it. `EMAIL_SYNC=1` (development, tests) sends
everything in-process after commit.

### States

`queued` → `sending` → `accepted` (the mail server took it). Plain SMTP cannot see
further, so SMTP mail is never reported `delivered`. Others: `deferred` (rate limit
or retry pending), `failed` (refused, or retries exhausted), `blocked`
(configuration or policy — nothing will send until fixed), `unknown`, `cancelled`.

**`unknown` is never retried automatically.** It means the connection dropped
after the message was sent: the server may already have accepted it. A worker
that dies mid-send produces the same state when its lease expires. Retrying needs
`confirm_duplicate_risk: true`.

### Failure handling

| Failure | Result |
|---|---|
| Login rejected, certificate invalid, STARTTLS missing, destination not public | blocked; connection marked failed |
| MAIL FROM refused (sender not allowed on that account) | blocked |
| 421 / 4xx with rate-limit wording | deferred ≥ 15 min (respects the server) |
| Network error or 4xx before the message was sent | retried with exponential backoff and jitter (5 retries) |
| 5xx for a recipient | that recipient failed; "user unknown" adds a suppression |
| Disconnect/timeout after the message was sent | unknown |

## Security properties

- **Credentials** are encrypted with `EMAIL_CREDENTIAL_KEYS` (`k2:<fernet>,k1:<fernet>`;
  first is primary) via HKDF subkeys, separately for credentials and content —
  never with `SECRET_KEY`. Decryption failures raise; they never become a blank
  password. Rotate: prepend a key, restart, `scripts/reencrypt_email_credentials.py`,
  drop the old key.
- **SSRF:** a connection's host must be a public DNS name on port 465 (implicit
  TLS) or 587 (STARTTLS); port 25 is refused. Every resolved address must be
  globally routable, and the socket connects to the address that passed the check
  while TLS verifies the hostname — re-checked on every send, so DNS rebinding
  does not help. Certificate verification cannot be turned off (`SMTP_TLS_VERIFY`
  is ignored).
- **Tenancy:** the company always comes from the credential. Request bodies forbid
  unknown fields, so a `company_id` in a body is a 422. Another company's ids are
  404s.
- **Headers:** CR/LF is rejected in subject, names and addresses, and stripped in
  the MIME builder as a backstop. Bcc never appears in a header.
- **Test sends** (`connection.test`) go only to the sender's own address or an
  active user of the company, ten per connection per hour.
- **OTP content** is purged from the message once it is final.

## External API

`/api/v1/email` (the repo's `/api/v1` prefix for the spec's `/v1`), authenticated by
an API key (`Authorization: Bearer hf_live_…`) or a signed-in admin/HR session.
Admins create keys under Email Settings → Email API keys (`/api/v1/admin/api-keys`);
a key is shown once, stored as a hash, and can be scoped, IP-restricted, given an
expiry and an hourly quota, rotated with overlap, and revoked.

| Endpoint | Scope |
|---|---|
| `POST /messages` (`Idempotency-Key` header) | `email:send` |
| `GET /messages`, `GET /messages/{id}` | `email:read` |
| `POST /messages/{id}/retry`, `/cancel` | `email:send` |
| `GET /connections`; `POST /connections/smtp`, `/{id}/test`, `/{id}/disconnect` | `connections:read` / `connections:write` |
| `GET /senders`; `POST /senders`; `PATCH /senders/{id}` | `senders:read` / `senders:write` |
| `GET /suppressions`; `DELETE /suppressions/{email}` | `email:read` / `senders:write` |

```http
POST /api/v1/email/messages
Authorization: Bearer hf_live_…
Idempotency-Key: interview-app-982-round-2-v1

{"to": [{"email": "candidate@example.net", "name": "Candidate"}],
 "subject": "Interview invitation", "html": "<p>…</p>",
 "external_reference": "application_982"}
```

`202 {"message_id", "status": "queued", "sender": {...}}`. The same key and body
again → `200` with `Idempotent-Replayed: true`; the same key with a different body
→ `409`. Quota → `429` with `Retry-After`. Errors are `{"detail": {"code", "message"}}`.

`hf_test_` keys see only test messages and deliver nothing (the NullAdapter
simulates outcomes from the recipient: `fail+…@`, `bounce+…@`, `defer+…@`,
`throttle+…@`, `unknown+…@`).

## Operations

| Task | How |
|---|---|
| Deploy this for the first time | [DEPLOY_UPDATE.md → Email API](DEPLOY_UPDATE.md#email-api-one-time-steps) |
| See why mail is not going out | Admin → Email Settings → Mail Routing: blocked events with reasons, outbox backlog, dispatcher heartbeat |
| Re-send mail blocked before a fix | `scripts/retry_blocked_messages.py --reason <code> [--company <id>] --dry-run` |
| Push changed env mailbox passwords | `scripts/seed_platform_email.py --update` |
| Rotate encryption keys | prepend to `EMAIL_CREDENTIAL_KEYS`, restart, `scripts/reencrypt_email_credentials.py` |
| Roll the release back | `scripts/email_connections_to_legacy.py` **before** downgrading code and schema |
| Local development without mail | `EMAIL_DELIVERY_MODE=null` |

## Code map

| Module | Responsibility |
|---|---|
| `constants.py` | States, reason codes, scopes, `EVENT_CATALOG` |
| `policy.py` | `resolve_sender`, `describe_routing` |
| `senders.py`, `connections.py` | Sender rules; connection create/verify/update/disconnect, `send_test` |
| `messages.py` | `submit`, idempotency, templates, `cancel`, `retry` |
| `outbox.py`, `worker.py`, `limits.py` | Publishing, dispatcher, delivery, rate limits and quotas |
| `adapters/` | `SMTPAdapter`, `NullAdapter`, the adapter contract and capability flags |
| `crypto.py`, `ssrf.py`, `mime.py` | Keys, destination checks, message building |
| `api_keys.py`, `principal.py`, `schemas.py` | External API authentication and bodies |
| `compat.py`, `facade.py` | `SenderRef` for `email_service`; the admin/HR screens' endpoints |
| `bootstrap.py` | Platform mailbox seeding, migration check |
