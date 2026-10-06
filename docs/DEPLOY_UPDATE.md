# Updating a running deployment

For pushing code that is already live. First-time install is
[CPANEL_DEPLOYMENT.md](CPANEL_DEPLOYMENT.md); moving data between deployments is
[GCP_DATA_MIGRATION.md](GCP_DATA_MIGRATION.md).

## The one rule

**Code and schema move together, and you verify that they did.**

`git pull` updates the code. It does not touch the database, `/etc/systemd/system/`,
`/etc/apache2/`, or the built frontend — all four are *copies*, and every one of
them has silently gone stale on this deployment at least once.

The check that catches it costs two seconds:

```bash
sudo -u hireflow /opt/hireflow/venv/bin/python scripts/schema_diff.py
```

Run it after every deploy, not only when something looks wrong.

> ### What this prevents
>
> A release added a `mail_templates` table and a `departments.logo_url` column.
> The code was deployed; `alembic upgrade head` was not run. SQLAlchemy emits every
> mapped column in its `SELECT`s, so one missing table took out the entire HR
> dashboard — while interviewers, who touch neither, worked perfectly.
>
> It looked like a data problem. It cost hours of hunting through tenancy filters
> and `hr_id` joins before anyone checked the schema. `schema_diff.py` names the
> missing table immediately, and `alembic current` alone would **not** have caught
> it — see [DEPLOYMENT_NOTES §3](DEPLOYMENT_NOTES.md#a-database-can-be-stamped-at-head-while-its-schema-is-not).

---

## 1. See what you are deploying

> **The pipeline does this differently.** `.github/workflows/deploy.yml` deploys a *release
> tag*, not a branch: it runs `git fetch --tags` then `git checkout --detach vX.Y.Z`, which
> leaves the server in detached HEAD. The steps below are the by-hand equivalent and still
> describe what has to happen; only the way the code arrives differs. If you deploy by hand
> on a server the pipeline has touched, `git pull` will not work until you check out a
> branch again.

`[root]`

```bash
cd /opt/hireflow
BEFORE=$(git rev-parse HEAD)
git pull
git diff --name-only $BEFORE HEAD | sed 's|^|  |'
```

Keep that list. It decides which of steps 3–6 you need. When in doubt, run them
all — every one is safe to repeat.

## 2. Back up, if any migration changed

Skip only if `git diff` showed nothing under `backend/alembic/versions/`.

```bash
DB=$(curl -s localhost:8000/health | grep -o '"database":"[^"]*"' | cut -d'"' -f4)
echo "backing up: $DB"
mysqldump -u innomaxskills_hf -p --single-transaction --no-tablespaces \
  "$DB" > /root/predeploy-$(date +%F-%H%M).sql
grep -c '^CREATE TABLE' /root/predeploy-*.sql | tail -1
```

A count of `0` means the dump is empty — stop and fix that before running DDL.

Once the nightly backup is installed ([BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md)),
`systemctl start hireflow-backup.service` takes the same dump plus every uploaded file,
encrypted and off-site; `journalctl -u hireflow-backup -n 5` must end with `copied
off-site to …`.

## 3. Python dependencies — if `requirements-cpanel.txt` changed

```bash
sudo -u hireflow /opt/hireflow/venv/bin/pip install -r /opt/hireflow/backend/requirements-cpanel.txt
```

`requirements-ci.txt` is CI-only; ignore it.

## 4. Database — always, then verify

```bash
cd /opt/hireflow/backend
sudo -u hireflow /opt/hireflow/venv/bin/alembic upgrade head
sudo -u hireflow /opt/hireflow/venv/bin/alembic current
sudo -u hireflow /opt/hireflow/venv/bin/python scripts/schema_diff.py
```

**`schema_diff.py` must end `OK: every table, column and index the models declare
is present.`** Anything else, stop — do not restart the services. A partially
migrated database serving traffic is worse than an old one.

> **Release with `j6z7a8b9c0d1_ats_explainability`:** it widens
> `resume_pool.match_results` to MEDIUMTEXT, which rebuilds that table. Run it
> off-hours on a large Talent Pool. Existing resume scores are **not** rescored by the
> deploy: they keep their score and show "Scoring rules updated" until HR presses
> *Rescore* on a job. To preview what a rescore would do to a job's ranking first
> (saves nothing):
>
> ```bash
> sudo -u hireflow /opt/hireflow/venv/bin/python scripts/ats_rules_dry_run.py --job <job_id>
> ```

> **Release with the code-execution security changes** (judge jobs as JSON, the
> worker's allow-list, a hardened worker unit). Four one-time steps, in this order:
>
> 1. **Redis password**, if `curl -s localhost:8000/health` shows
>    `"judge_queue_auth":false`: follow Step 2.4 of
>    [CPANEL_DEPLOYMENT.md](CPANEL_DEPLOYMENT.md) (it checks first that no other
>    account uses Redis), then put the printed `REDIS_URL=` into `backend/.env`.
> 2. `chmod 700 /srv/judge_work`.
> 3. **Step 5 below is required** — the worker unit changed (`ProtectSystem=strict`,
>    `NoNewPrivileges=true`), and so did how the worker is started (`entrypoint.sh`).
> 4. Restart **all four services together** (Step 8). The API and the worker must
>    agree on the job format: a job queued by the old API is refused by the new
>    worker, once, and shows the candidate "temporarily unavailable" — it is not
>    lost, the answer is regraded later.
>
> Then press **Run Code** with `print("hello")` (Step 9). If it fails,
> `journalctl -u hireflow-judge-worker -n 50` names the path or permission the new
> unit took away. Optional but recommended: gVisor (Step 2.5 of CPANEL_DEPLOYMENT.md).

> **Release with tenant-isolation enforcement** (the tenant filter now actually runs on
> every request; background: [TENANT_ISOLATION.md](TENANT_ISOLATION.md)). Rows with a NULL
> `company_id` vanish from every company's view the moment the new code serves traffic,
> so fill them **before Step 8**, after `alembic upgrade head` above:
>
> ```bash
> sudo -u hireflow /opt/hireflow/venv/bin/python scripts/backfill_orphan_company_ids.py          # dry run
> sudo -u hireflow /opt/hireflow/venv/bin/python scripts/backfill_orphan_company_ids.py --apply
> sudo -u hireflow /opt/hireflow/venv/bin/python scripts/verify_tenant_isolation.py
> ```
>
> `verify_tenant_isolation.py` must end `PASS`. Rows still counted in `total_null` have no
> parent to inherit a company from; they will be invisible after the restart. Look at
> them before deploying, don't discover them from a user.

> **Release that moves the email queue to JSON** (`src/mailq/safety.py`). The worker no
> longer reads pickled jobs, and the queue moves to its own Redis database
> (`EMAIL_REDIS_DB`, default `1`) unless `EMAIL_REDIS_URL` is set. A job queued by the old
> code is never sent by the new worker. Before Step 8, check the old queue is empty:
>
> ```bash
> cd /opt/hireflow/backend
> # The URL the OLD code used: EMAIL_REDIS_URL, else JUDGE_REDIS_URL, else REDIS_URL.
> OLD_URL=$(sudo -u hireflow /opt/hireflow/venv/bin/python -c 'from dotenv import dotenv_values as d; e = d(".env"); print(e.get("EMAIL_REDIS_URL") or e.get("JUDGE_REDIS_URL") or e.get("REDIS_URL") or "redis://localhost:6379/0")')
> sudo -u hireflow /opt/hireflow/venv/bin/rq info email --url "$OLD_URL"
> ```
>
> `email` must show `0` jobs, and nothing scheduled (rate-limit and retry deferrals wait up
> to an hour). If some remain and you can't wait, those messages won't go out; the Email
> Log shows them as `queued`/`deferred`, so resend them by hand after the deploy.

Two failures worth recognising:

- **`(3780, "Referencing column … are incompatible")`** — a charset mismatch. The
  database default is `utf8mb3` while its tables are `utf8mb4`, so a newly created
  table cannot form a foreign key. Fixed once, permanently:

  ```bash
  mysql -u innomaxskills_hf -p -e \
    "ALTER DATABASE $DB CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
  ```

  No existing table is rewritten — this only changes the default for new ones.

- **`schema_diff` reports a missing column while `alembic current` says `(head)`**
  — a migration older than the stamped revision never ran, and Alembic will never
  revisit it. Apply the DDL by hand; do **not** `downgrade` and re-`upgrade`.

## 5. Service units — if `deploy/cpanel/*.service` or `*.timer` changed

```bash
\cp -f /opt/hireflow/deploy/cpanel/*.service /etc/systemd/system/
[ -n "$(ls /opt/hireflow/deploy/cpanel/*.timer 2>/dev/null)" ] && \
  \cp -f /opt/hireflow/deploy/cpanel/*.timer /etc/systemd/system/
systemctl daemon-reload
```

The `\` in `\cp` bypasses root's `cp -i` alias, which otherwise prompts per file.
**Without `daemon-reload` systemd keeps running the old unit** — this is how
proctoring once spent two deploys ignoring its own `ROLE=proctor`.

## 6. Apache — if `deploy/cpanel/hireflow.conf` changed

```bash
for m in std ssl; do
  \cp -f /opt/hireflow/deploy/cpanel/hireflow.conf \
     /etc/apache2/conf.d/userdata/$m/2_4/innomaxskills/career.innomaxskills.com/
done
/scripts/ensure_vhost_includes --user=innomaxskills
/scripts/restartsrv_httpd
```

Both `std` and `ssl` — cPanel rebuilds its configuration periodically and would
otherwise discard one of them.

## 7. Frontend — if anything under `frontend/` changed

```bash
cd /opt/hireflow/frontend && npm install && npm run build
rsync -a --delete dist/ /home/innomaxskills/public_html/career/
\cp -f /opt/hireflow/deploy/cpanel/htaccess-spa /home/innomaxskills/public_html/career/.htaccess
chown -R innomaxskills:innomaxskills /home/innomaxskills/public_html/career
```

**`npm install`, not `npm ci`.** The lockfile in this repo drifts from
`package.json` and `npm ci` fails with `EUSAGE`.

Forgetting this step leaves the browser on the old bundle, which looks exactly
like a backend fault.

## 8. Restart and verify

```bash
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
sleep 25
systemctl is-active hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
ss -ltnp | grep -E ':800[01]'
curl -s localhost:8000/health
```

All four must read `active`. `activating` on a second check is a restart loop —
`Restart=always` makes that look like a slow start.

**Both 8000 and 8001 must be listening**, with 8001 on a single worker. If only
8000 appears, one service took the other's port and is crash-looping on
`Connection in use` — while the site keeps working, because both run the same app.
That is what a silent proctoring outage looks like.

## 9. Smoke test the paths that fail quietly

| Check | Why it is on this list |
|---|---|
| Log in as an **HR** user with jobs assigned | The role most exposed to schema drift |
| Send one real email | Encryption-key and mailbox failures produce no error |
| Start a test, `F12` → Network → **WS** shows `101` | Proctoring failures show as "Checking…" forever |
| Run Code with `print("hello")` | Proves the judge worker and its queue |
| Open a candidate CV | Proves storage keys still resolve |
| `systemctl list-timers hireflow-backup.timer` | Backups are scheduled; see [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md) |

---

## Email API: one-time steps

The release that introduces the email API ([EMAIL_API.md](EMAIL_API.md)) moves every
email onto company senders, an outbox and a new dispatcher service, and stops
reading `SMTP_*` at send time. Do these once, in this order, inside the normal
procedure above.

**Before pulling (Steps 1–2)**

1. Generate the email key and add it to `backend/.env` (keep `EMAIL_ENCRYPTION_KEY`
   and `SECRET_KEY` as they are — they are still needed to read the old passwords
   once):

   ```bash
   sudo -u hireflow /opt/hireflow/venv/bin/python -c \
     "from cryptography.fernet import Fernet; print('EMAIL_CREDENTIAL_KEYS=k1:' + Fernet.generate_key().decode())"
   ```

   Back it up with the other secrets. **Losing it makes every stored mailbox
   password unreadable.**
2. Install the updated sudoers file (it allows the deploy user to restart the new
   dispatcher):

   ```bash
   \cp -f /opt/hireflow/deploy/cpanel/deploy-sudoers /etc/sudoers.d/hireflow-deploy && visudo -c
   ```

**After `alembic upgrade head` (Step 4)** — two revisions run: `t1e2m3a4i5l6`
(new tables) and `t2e3m4a5i6l7` (copies `tenant_configs`, `company_mailboxes` and
pipeline step settings into connections, senders and routes; the old rows are left
as they are).

3. Re-encrypt the copied passwords under the new key:

   ```bash
   cd /opt/hireflow/backend
   sudo -u hireflow /opt/hireflow/venv/bin/python scripts/reencrypt_email_credentials.py --dry-run
   sudo -u hireflow /opt/hireflow/venv/bin/python scripts/reencrypt_email_credentials.py
   ```

   Any `unreadable` connection is set to *Needs attention*; its mail is blocked
   until an admin re-enters that password. Note which companies they belong to.
4. Check what the platform's own mailboxes will become (the API also does this at
   startup):

   ```bash
   sudo -u hireflow /opt/hireflow/venv/bin/python scripts/seed_platform_email.py --dry-run
   ```

   `SMTP_*` becomes "Platform System" — it also sends verification and reset codes
   for companies with no mailbox of their own — `INNOMAX_HR_*` the platform's
   default sender, `INNOMAX_OFFICIAL_*` the new-company welcome email.

**Step 5** is required: install the new `hireflow-email-dispatcher.service`, then

```bash
systemctl enable --now hireflow-email-dispatcher
```

**Step 8:** restart the dispatcher with the others:

```bash
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker hireflow-email-dispatcher
systemctl is-active hireflow-email-dispatcher
```

A job queued by the previous release is not lost: the worker re-submits it through
the new sender rules and ignores the password it carried.

**Step 9, extended**

- Admin → Email Settings → **Mail Routing** shows no `email_migration_pending`, and
  the delivery card reads *Delivering normally* (Redis up, dispatcher reporting).
- Every company that has blocked events there is a company whose mail was going out
  from the platform's account before. Tell its admin: they need to connect a
  mailbox. Verification and reset codes keep working meanwhile.
- Send one real email, and request one password reset.

**What admins will notice**

- Saving a mailbox now connects and logs in; ports other than 465 and 587 are
  refused.
- A mailbox that cannot send no longer falls back to another address: mail routed
  to it is blocked, and Mail Routing says why.
- A step switched off in Pipeline Notifications really sends nothing.
- Test emails go only to the mailbox's own address or a user of the company.

`hireflow-email-dispatcher` is in `SERVICES` in `.github/workflows/deploy.yml`, so
automated deploys restart it — which needs the updated sudoers file (step 2) on the
server first, or the restart step is refused.

## Rollback

Code and frontend:

```bash
cd /opt/hireflow && git reset --hard $BEFORE
cd frontend && npm install && npm run build && rsync -a --delete dist/ /home/innomaxskills/public_html/career/
\cp -f /opt/hireflow/deploy/cpanel/htaccess-spa /home/innomaxskills/public_html/career/.htaccess
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
```

Rolling back past the email API release: **first**, while the new code is still
installed, copy the email settings changed since back to the old tables, then stop
the dispatcher —

```bash
sudo -u hireflow /opt/hireflow/venv/bin/python scripts/email_connections_to_legacy.py --dry-run
sudo -u hireflow /opt/hireflow/venv/bin/python scripts/email_connections_to_legacy.py
systemctl disable --now hireflow-email-dispatcher
```

— otherwise the old code sends with the settings as they were before the upgrade.
Mail still in the outbox is not sent by the old code.

`--delete` removes `.htaccess` along with the old bundle — it lives in the document root
but not in `dist/`. Step 7 re-copies it; a rollback has to as well, or the SPA loses its
history fallback (every deep link 404s) and its `Referrer-Policy` header.

Schema is the part that does not roll back cleanly. Most migrations here are
additive and guarded, so an older codebase tolerates the newer schema — prefer
rolling the code back and leaving the database alone. Restore from
`/root/predeploy-*.sql` or the nightly bundle
([BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md#restoring-for-real)) only if a migration
actually destroyed something.

## The four copies that `git pull` does not update

Keep this list in mind; every entry has caused a real incident.

| Copy | Updated by | Symptom when stale |
|---|---|---|
| Database schema | `alembic upgrade head` | One feature silently empty or 500 |
| `/etc/systemd/system/*.service` | `\cp` + `daemon-reload` | Config changes have no effect |
| `/etc/apache2/conf.d/userdata/…` | `\cp` + `restartsrv_httpd` | WebSockets fail; routes 404 |
| `public_html/career/` | `npm run build` + `rsync` | Old UI against a new API |
