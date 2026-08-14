# Migrating the GCP data into the cPanel deployment

Moving the Cloud SQL database and the `gs://hireflow-uploads` bucket into a
**running** cPanel deployment.

This is a different job from the migration sketch in
[CPANEL_DEPLOYMENT.md](CPANEL_DEPLOYMENT.md) Step 10.2, which assumes a fresh
install with an empty target. Here the site is live, so nothing is written to the
production database until the very last step — and that step is one line in
`.env`.

**Read [DEPLOYMENT_NOTES.md](DEPLOYMENT_NOTES.md) §4 first** if you have not:
`.env` overrides the systemd unit files, which is relevant to the cutover.

---

## The shape of it

| | |
|---|---|
| Approach | Import into a **separate** database, verify it fully, then flip `DATABASE_URL` |
| Rollback | Change that line back and restart. The live database is never written to |
| Downtime | One service restart |
| Files | Copied to matching relative paths. **No database rewriting** |

The file half is easy for one specific reason: `src/services/storage.py` uses
**identical keys across backends**. A resume is `resumes/<uuid>.pdf` whether that
resolves to a GCS object or a file under `HIREFLOW_DATA_DIR`, and the database
stores the key rather than a URL. Copy the objects to the same relative paths and
every existing row resolves unchanged.

### Values used in the commands below

Every command in this runbook is written with these filled in. Confirm the first
three against your own server before you start — the rest are fixed by the export.

| Placeholder | This deployment | Confirm with |
|---|---|---|
| Server | `root@66.116.207.71` | — |
| DB user | `innomaxskills_hf` | `grep ^DATABASE_URL /opt/hireflow/backend/.env` |
| Live database | `innomaxskills_hireflow` | `curl -s localhost:8000/health` |
| Staging database | `innomaxskills_hfmig` | you create it in step 4 |
| App root | `/opt/hireflow`, backend at `/opt/hireflow/backend` | — |
| Service user | `hireflow` | — |
| Site | `https://career.innomaxskills.com` | — |
| Export bundle | `C:\Users\ADMIN\Downloads\hireflow-migration.zip` | — |
| Dump inside it | `Cloud_SQL_Export_2026-08-13 (17_58_10).sql` | — |
| GCP project | `payyalas-project`, service `hire-flow-api`, region `us-central1` | — |
| GCP bucket | `gs://hireflow-uploads` | — |

The database password is never written into a command here — every `mysql` and
`mysqldump` call uses `-p` and prompts for it, so it stays out of your shell
history and out of `ps`.

---

## Before you start — inspect your own dump

Do not trust the numbers below; they are from the 2026-08-13 export. Re-run these
against yours, because **step 5 depends on the answers**.

```bash
F='Cloud_SQL_Export_2026-08-13 (17_58_10).sql'

# 1. statements a non-SUPER cPanel user cannot run
grep -cE 'DEFINER=|SQL_LOG_BIN|GTID_PURGED|CREATE (VIEW|TRIGGER|PROCEDURE|FUNCTION)' "$F"

# 2. which database it wants to create, and the revision it is stamped at
grep -nE '^(CREATE DATABASE|USE )' "$F"
grep -A1 'INSERT INTO `alembic_version`' "$F" | head -2

# 3. syntax newer than the target server (cPanel here is MySQL 8.0.46)
grep -ohE '/\*!(8[0-9]{4})' "$F" | sort -u
```

For the 2026-08-13 export: **0** problem statements, one `CREATE DATABASE
recruitment_db` + `USE`, stamped at `x4n5o6p7q8r9`, and the only `>8.0` gate
(`/*!80016`) sits on the `CREATE DATABASE` line that gets deleted anyway. So the
usual `sed` gymnastics for `DEFINER=` and `GTID_PURGED` are **not** needed — check
before copying them from a blog post.

### The trap this migration exists to document

That export was **stamped at head while its schema was not**.

GCP's database was bootstrapped by `create_all()` and stamped forward. `create_all`
creates missing *tables* but never `ALTER`s an existing one, so a migration that
adds a *column* to a table it already made is skipped — while `alembic_version`
claims it ran. The export was missing `company_mailboxes.hourly_limit`, added by
`s9i0j1k2l3m4`, an **ancestor** of the stamped revision.

Nothing repairs that automatically. `alembic upgrade head` skips ancestors;
`create_all` will not `ALTER`. And because SQLAlchemy emits every mapped column in
its `SELECT`s, one missing column takes out the entire model — the admin mailbox
screen and every outbound email that resolves a sender, all 500.

`scripts/schema_diff.py` exists to catch exactly this. Row counts never would.

---

## Step 0 — Recover the old secret

The dump's `company_mailboxes.smtp_pass` and `tenant_configs.smtp_pass` hold
Fernet ciphertext, encrypted under the **GCP** deployment's key.

```bash
gcloud secrets versions access latest --secret=SECRET_KEY --project=payyalas-project
gcloud run services describe hire-flow-api --region=us-central1 --project=payyalas-project \
  --format="yaml(spec.template.spec.containers[0].env)"
```

If `EMAIL_ENCRYPTION_KEY` appears in that environment it **wins** over
`SECRET_KEY` and is the value to carry. Exact bytes matter — a secret created with
`echo "value" | gcloud secrets create` carries a trailing newline and derives a
completely different key.

You do **not** need to carry `SECRET_KEY` itself. Password hashes are bcrypt with
embedded salts, so every migrated user logs in unchanged.

> ### What this actually turned up for the 2026-08-13 export
>
> Neither `EMAIL_ENCRYPTION_KEY` **nor `SECRET_KEY`** was present in the Cloud Run
> environment. A `SECRET_KEY` existed in Secret Manager but was never wired into
> the service, so `_derive_key()` (`src/security.py`) fell all the way through to
> its hardcoded development fallback:
>
> ```python
> secret = os.getenv("SECRET_KEY") or "hireflow-dev-key-change-me"
> ```
>
> All 11 ciphertexts decrypt with `SHA-256("hireflow-dev-key-change-me")`, and
> none with the Secret Manager value. **Test, do not assume** — the "obvious"
> answer was wrong here, and the failure is silent.
>
> The security consequence is separate from the migration and matters more: those
> SMTP passwords were encrypted under a constant that is checked into this
> repository. Anyone with the source and a copy of the database can read them.
> Treat every affected mailbox password as compromised — see [Afterwards](#afterwards).

## Step 1 — Deploy the current code first

If any commits are undeployed, deploy them **now**, not after. A dump stamped at an
older revision needs the newer migration to exist on the server before it can be
upgraded.

```bash
cd /opt/hireflow && git pull
cd backend && sudo -u hireflow /opt/hireflow/venv/bin/alembic upgrade head
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
curl -s localhost:8000/health
```

Rebuild the frontend too if anything under `frontend/` changed — see
[CPANEL_DEPLOYMENT.md](CPANEL_DEPLOYMENT.md) Step 13.

Confirm the site works normally before continuing. If you skip this, a later
failure has two possible causes instead of one.

## Step 2 — Upload the export

```bash
scp hireflow-migration.zip root@66.116.207.71:/root/
```

`[root]` — check free space first; a shared server has other accounts on this disk:

```bash
df -h /opt /root
mkdir -p /root/migration && unzip -q /root/hireflow-migration.zip -d /root/migration

# The zip has its own top-level folder, so the payload is one level deeper than
# where you unzipped it. Find it rather than assuming either layout:
cd "$(dirname "$(find /root/migration -name '*.sql' | head -1)")"
pwd
du -sh */ ; find . -type f | wc -l
```

Expect ~1,700 files. `cover_letters` will be absent — see [step 8](#step-8--move-the-files-in).

Everything from here on runs from that directory, and the dump is addressed
through `$F` rather than by name, because it contains spaces and parentheses:

```bash
F=$(ls *.sql | head -1)
echo "using: [$F]" && ls -lh "$F"
```

An unquoted or mistyped filename fails in a way that is easy to misread: `sed` and
`grep` say "No such file or directory", but the `mysql < "$F"` line fails as a
*bash redirect* error, so `mysql` never runs at all and no import happens. The next
command then reports `Table '….users' doesn't exist`, which looks like a failed
import rather than one that never started.

## Step 3 — Back up what is live

> ### Run this yourself rather than using `POST /api/v1/db/backup`
>
> The endpoint does produce a real `mysqldump` now (it did not before `0ad127f`),
> but it runs the dump in a **background task** — it returns `success: true` and a
> path before the work happens, and a failure after that exists only in the log.
>
> Your entire rollback rests on this file, so run the command and read its exit
> code. Any backup taken before 2026-08-14 will not restore; do not rely on one.

```bash
mysqldump -u innomaxskills_hf -p --single-transaction --no-tablespaces \
  innomaxskills_hireflow > /root/pre-migration-$(date +%F).sql
ls -lh /root/pre-migration-*.sql
```

## Step 4 — Create the staging database

cPanel → **MySQL Databases** → create `innomaxskills_hfmig`, then **Add User To Database**
granting your existing DB user ALL PRIVILEGES. Use the UI rather than raw SQL, so
cPanel's own tooling and backups know the database exists.

This becomes the permanent production database after the flip. The name is
cosmetic: every query uses MySQL's `DATABASE()` rather than a hardcoded name, and
`/health` reports whatever it is actually connected to.

## Step 5 — Import

```bash
cd "$(dirname "$(find /root/migration -name '*.sql' | head -1)")"
F=$(ls *.sql | head -1)                       # set in step 2; re-set if you reconnected

sed -i '/^CREATE DATABASE/d; /^USE `recruitment_db`/d' "$F"
grep -cE '^(CREATE DATABASE|USE )' "$F"      # must print 0

mysql -u innomaxskills_hf -p --max-allowed-packet=512M innomaxskills_hfmig < "$F"

mysql -u innomaxskills_hf -p innomaxskills_hfmig -e \
  "SELECT COUNT(*) AS users FROM users; SELECT COUNT(*) AS candidates FROM candidates;"
```

Compare those counts against the source. If the import failed part-way you will
see it here — and only the staging database is affected.

## Step 6 — Close the schema gaps

Two different fixes, in this order:

```bash
STAGE='mysql+pymysql://innomaxskills_hf:PASSWORD@localhost:3306/innomaxskills_hfmig'
cd /opt/hireflow/backend

# a) revisions NEWER than the dump's stamp — Alembic applies these normally
sudo -u hireflow env DATABASE_URL="$STAGE" /opt/hireflow/venv/bin/alembic upgrade head
sudo -u hireflow env DATABASE_URL="$STAGE" /opt/hireflow/venv/bin/alembic current

# b) migrations OLDER than the stamp that never actually ran (see the trap above).
#    Alembic believes these are applied and will never revisit them.
mysql -u innomaxskills_hf -p innomaxskills_hfmig \
  -e "ALTER TABLE company_mailboxes ADD COLUMN hourly_limit INT NULL;"
```

Do **not** try to reach (b) with `alembic downgrade` then `upgrade`. Replaying a
stamped chain over a populated schema is how this problem was created.

`env DATABASE_URL=...` works because `load_dotenv()` defaults to
`override=False` — an inline variable beats `.env`. `sudo` strips the environment,
so it has to be `sudo -u hireflow env VAR=…`, not `sudo -u hireflow VAR=… …`.

**Then prove nothing else is missing. This is the gate:**

```bash
sudo -u hireflow env DATABASE_URL="$STAGE" /opt/hireflow/venv/bin/python \
  scripts/schema_diff.py
```

Must end `OK: every table, column and index the models declare is present.`
Anything else — stop and resolve it. Do not proceed on row counts alone.

## Step 7 — Prove the encryption key

**For the 2026-08-13 export this is already answered** — all 11 ciphertexts were
tested against every candidate and exactly one works:

```
EMAIL_ENCRYPTION_KEY=9cByOR1y9qkcTo2VBUlwEbLDeQQVOYrWnYO49UHfX-g=
```

That is `SHA-256("hireflow-dev-key-change-me")`, base64 — derived from a constant
in `src/security.py`, so writing it here leaks nothing that the repository does not
already give away. It is **not** a secret, which is the whole problem with it; see
Step 0 and [Afterwards](#afterwards).

For any other export, work it out rather than guessing:

```bash
mysql -u innomaxskills_hf -p innomaxskills_hfmig -N -e \
  "SELECT smtp_pass FROM company_mailboxes WHERE smtp_pass LIKE 'gAAAA%' LIMIT 1;"

cd /opt/hireflow/backend
/opt/hireflow/venv/bin/python scripts/find_email_key.py \
  --ciphertext 'gAAAAAB...' --secret-key-stdin      # paste the OLD SECRET_KEY
```

It tests every plausible candidate against real ciphertext and prints the exact
`EMAIL_ENCRYPTION_KEY=` line to use. Apply it in the **same edit** as the
`DATABASE_URL` flip in step 9 — a mailbox saved between the two would be written
under the wrong key.

**If nothing matches**, migrate anyway and re-enter the SMTP app passwords in the
admin UI afterwards. Nothing crashes: `decrypt()` returns `""` and the caller falls
back to the raw stored value, so the app tries to authenticate using the literal
ciphertext. The symptom is email silently not sending — which is why this is
checked here rather than discovered next week.

## Step 8 — Move the files in

```bash
# Same nesting as step 2 — derive the source, do not assume it.
SRC="$(dirname "$(find /root/migration -name '*.sql' | head -1)")"
echo "copying from: $SRC" && ls "$SRC"

for p in resumes uploads logos cover_letters evidence_frames; do
  mkdir -p "/opt/hireflow/backend/$p"
  if [ -d "$SRC/$p" ]; then
    rsync -a "$SRC/$p/" "/opt/hireflow/backend/$p/"
    echo "  copied   $p"
  else
    echo "  ABSENT   $p  (expected only for cover_letters)"
  fi
done
chown -R hireflow:hireflow /opt/hireflow/backend/{resumes,uploads,logos,cover_letters,evidence_frames}
```

Read that output. A wrong `SRC` makes every prefix print `ABSENT`, the `mkdir` still
creates the directories, and nothing errors — so the copy silently does nothing and
the first sign is thousands of missing keys in the reconciliation below. Only
`cover_letters` should be absent.

Safe to do before the cutover: filenames are UUIDs so nothing collides, and
unreferenced files are inert.

Two prefixes are easy to get wrong:

- **`evidence_frames`** is written by proctoring (`proctoring/config.py:37`) but is
  deliberately **not** in `_FILE_PREFIXES` — it is served by an authenticated route
  rather than a static mount. It is easy to forget, and it fails somewhere nobody
  looks.
- **`cover_letters`** may not exist in the bucket at all. It *is* in
  `_FILE_PREFIXES`, and `main.py` calls `os.makedirs(..., exist_ok=True)`, so an
  empty directory is correct — hence the `mkdir -p` above.

Then reconcile the database against the disk:

```bash
sudo -u hireflow env DATABASE_URL="$STAGE" /opt/hireflow/venv/bin/python \
  scripts/verify_storage_keys.py
```

This walks every column holding a storage key and asks `storage.exists()` — the
same call the app makes, so leading slashes (the database legitimately holds
`/logos/x.png`) and `HIREFLOW_DATA_DIR` are handled identically. `ls | wc -l`
cannot tell you whether the files present are the ones the rows reference.

## Step 9 — Cutover

```bash
cp /opt/hireflow/backend/.env /root/.env.pre-migration
```

Edit `/opt/hireflow/backend/.env` — everything in one pass:

1. `DATABASE_URL` → `…/innomaxskills_hfmig`
2. `EMAIL_ENCRYPTION_KEY=` → the value from step 7
3. `PLATFORM_ADMIN_EMAIL` → see the warning below

> ### You may be locking yourself out of admin
>
> After a full replace, the admin accounts are the **source deployment's**, not the
> ones your `.env` bootstrap created. `bootstrap_platform_admin()`
> (`src/bootstrap.py`) **skips when the email already exists** — it does not reset
> the password. If `PLATFORM_ADMIN_EMAIL` matches an imported account whose
> password you do not know, restarting will not let you in.
>
> Check first:
> ```bash
> mysql -u innomaxskills_hf -p innomaxskills_hfmig -e \
>   "SELECT email, role FROM users WHERE role IN ('superadmin','admin');"
> ```
> If you cannot log in as any of them, point `PLATFORM_ADMIN_EMAIL` at an address
> **not** in that list. Bootstrap then creates a fresh admin on the next restart,
> under the existing platform company.
>
> **For the 2026-08-13 export this is resolved.** The Cloud Run environment carried
> `SUPERADMIN_EMAIL` / `SUPERADMIN_PASSWORD` and `PLATFORM_ADMIN_EMAIL` /
> `PLATFORM_ADMIN_PASSWORD`, and the platform admin address and password are the
> same pair already in the cPanel `.env` — so the credentials you use today keep
> working after the swap. No `PLATFORM_ADMIN_EMAIL` change is needed. Confirm with
> the query above rather than taking this on trust.

```bash
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
curl -s localhost:8000/health
```

`/health` names the database it is actually connected to, so it confirms the switch
rather than echoing a configured value.

### Rollback

```bash
cp /root/.env.pre-migration /opt/hireflow/backend/.env
systemctl restart hireflow-api hireflow-proctor hireflow-judge-worker hireflow-email-worker
```

Exact — the old database was never written to. Imported files stay on disk as
harmless orphans.

---

## Verification

| # | Check | Expected |
|---|---|---|
| 1 | `alembic current` against the new DB | the current head |
| 2 | `curl -s localhost:8000/health` | `healthy`, the new database name, `table_count > 0` |
| 3 | Log in as a migrated candidate | Succeeds — proves the bcrypt hashes imported |
| 4 | Log in as admin | Succeeds — the lockout check |
| 5 | Admin → Email routing diagnostics | Mailboxes listed, none erroring — `hourly_limit` **and** the Fernet key together |
| 6 | **Send one real email** | Arrives — the only proof the key is right |
| 7 | Open a migrated candidate's CV | PDF renders — keys, files and serving |
| 8 | `scripts/verify_storage_keys.py` | Zero missing |
| 9 | Open an old proctoring violation's evidence image | Loads — catches a missed `evidence_frames` |
| 10 | Re-run ATS scoring on a pool resume | Score appears — exercises `as_local_path()` |
| 11 | Generate questions on a migrated job | Works |
| 12 | Run Code in a test | Judge fine — unrelated to the DB, so a failure here means the restart |

Checks 5 and 6 are the pair that otherwise fails silently. Check 9 is the one
people skip.

## Afterwards

- [ ] **Rotate every SMTP app password, then move off the fallback key.** The
      migrated passwords were encrypted under a constant checked into this
      repository (Step 0), so they are readable by anyone with the source and a
      copy of the database. In order:
      1. Rotate the Gmail app passwords at the provider.
      2. Generate a real key — `python -c "from cryptography.fernet import Fernet;
         print(Fernet.generate_key().decode())"` — and put it in `EMAIL_ENCRYPTION_KEY`.
      3. Re-enter each mailbox password in the admin UI, which re-encrypts it under
         the new key.

      Do these in that order. Changing the key first makes the existing rows
      undecryptable and email stops — silently, because `decrypt()` returns `""`
      and the caller falls back to the raw stored value.
- [ ] **Delete the `.sql` export from the bucket.** Cloud SQL exports to GCS, and
      that is very likely the same bucket the app serves files from. It contains
      every password hash, the Fernet ciphertexts, any plaintext API keys in
      `companies`, and all candidate PII.
- [ ] Delete or encrypt the local export, for the same reason.
- [ ] Keep `/root/pre-migration-*.sql` **and the old database** until you have
      logged in as a migrated user, opened a migrated CV, and sent a real email.
      Dropping the old database is what makes the migration irreversible — a week
      of clean running is a reasonable wait, and it costs nothing to keep.
- [ ] Rotate the admin passwords from inside the app, and the database password.
      A migration puts credentials through terminals, notes and chat logs; assume
      everything that passed through one is known.
- [ ] **Lock down SSH.** Check the login banner — this server reported *13,435
      failed attempts* since the previous successful login, with root password
      auth on port 22. On shared hosting a successful guess is not only your
      problem. Do it *after* the migration, never during: WHM → *Security Center*
      → *cPHulk Brute Force Protection* (whitelist your own IP, or it will block
      you mid-transfer), then key-based auth with `PermitRootLogin
      prohibit-password`.
- [ ] Only then shut down the source deployment.
