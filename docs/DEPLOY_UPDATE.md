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

---

## Rollback

Code and frontend:

```bash
cd /opt/hireflow && git reset --hard $BEFORE
cd frontend && npm install && npm run build && rsync -a --delete dist/ /home/innomaxskills/public_html/career/
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
```

Schema is the part that does not roll back cleanly. Most migrations here are
additive and guarded, so an older codebase tolerates the newer schema — prefer
rolling the code back and leaving the database alone. Restore from
`/root/predeploy-*.sql` only if a migration actually destroyed something.

## The four copies that `git pull` does not update

Keep this list in mind; every entry has caused a real incident.

| Copy | Updated by | Symptom when stale |
|---|---|---|
| Database schema | `alembic upgrade head` | One feature silently empty or 500 |
| `/etc/systemd/system/*.service` | `\cp` + `daemon-reload` | Config changes have no effect |
| `/etc/apache2/conf.d/userdata/…` | `\cp` + `restartsrv_httpd` | WebSockets fail; routes 404 |
| `public_html/career/` | `npm run build` + `rsync` | Old UI against a new API |
