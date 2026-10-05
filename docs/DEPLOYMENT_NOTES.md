# Deployment notes — what we learned doing it

Companion to [CPANEL_DEPLOYMENT.md](CPANEL_DEPLOYMENT.md). That document says
*what to run*; this one says *why it is that way* and *what breaks quietly*.

Read the relevant section before changing anything about the deployment. Most
entries here exist because something failed, or nearly did, during the first
real deployment — several of them were errors in the deployment guide itself.

---

## 1. Hard constraints — do not break these

### Proctoring must run as exactly one worker

`proctoring/routes.py` keeps active sessions in a process-local dict whose values
are **live MediaPipe graph handles**. Those cannot be serialised, so the state
cannot move to Redis or be shared between workers.

`POST /proctor/sessions/start` creates the entry in one process. The WebSocket
arrives as a *separate connection*, which gunicorn hands to an arbitrary worker.
With N workers it reaches the right one 1/N of the time and closes **4004**
otherwise.

- The worker count is **hardcoded** as `-w 1` in `entrypoint.sh` under
  `ROLE=proctor`, and that role ignores `WEB_CONCURRENCY` entirely. It was once
  pinned in the unit file instead; [§4](#4-configuration-which-file-wins) is the
  story of why that wasn't enough.
- Capacity comes from `PROCTOR_INFERENCE_SLOTS`, never from worker count.
- This is why proctoring is a separate service from the API at all.

> The GCP deployment ran `WEB_CONCURRENCY=2` on a single combined service, so
> this bug was almost certainly live there — showing up as intermittent
> proctoring failures that the frontend's auto-reconnect masked by retrying
> until it happened to hit the right worker.

### Proctoring models are shared, not per-session

`proctoring/model_registry.py` holds a bounded pool of `ModelSet` objects,
borrowed per call. Analyzers hold **session state only** — counters, deques, one
face embedding.

Measured: 50 concurrent sessions cost ~604 MB total, about 45 KB per additional
session. Under the old per-session design that would have been ~5.7 GB and 200
threads.

**When editing an analyzer:** anything added to `__init__` should be state, not a
model. Adding a model there silently reverts the scaling property.

**Locking:** a borrowed slot is held exclusively, so a `ModelSet` needs no
internal lock — the pool *is* the mutex. Callers holding a per-session lock must
take that lock **first**, then borrow. The reverse ordering deadlocks.

### Nothing may depend on the current working directory

systemd, cron, and an interactive shell all start with different CWDs. Three
separate bugs came from this:

| Where | Symptom |
|---|---|
| `storage.py` | Uploads written to a second, unserved directory |
| `main.py` | Static mounts pointing at the wrong tree |
| `model_registry.py` | `Permission denied: 'yolov8n.pt'` — object detection silently disabled |

Resolve paths against `__file__`, never a bare relative name. The YOLO case is
instructive: ultralytics treats an unfound weights *name* as something to
**download into the CWD**, so the failure was a permissions error rather than a
"file not found".

### The judge worker's home directory is unreachable

`hireflow-judge-worker.service` sets `ProtectHome=true`, so `/home/hfjudge` does
not exist as far as that process is concerned. The `docker` CLI looks for
`$HOME/.docker/config.json`, gets `permission denied` rather than "not found", and
warns — on its own stderr.

That matters more than it sounds, because **`docker run` streams the container's
stderr onto its own**. The two are one pipe and cannot be separated at the stream
level, so a host-level warning is delivered to the candidate as their program's
error output. Candidates sitting an exam saw:

```
WARNING: Error loading config file: open /home/hfjudge/.docker/config.json: permission denied
```

Fixed on both sides, and both are needed:

- `Environment=HOME=/srv/judge_work` and `DOCKER_CONFIG=/srv/judge_work/.docker`
  in the unit. The directory need not exist — the CLI is silent on `ENOENT` and
  only complains about a path it can see but not read.
- `_split_docker_noise()` in `src/judge/sandbox.py` separates CLI diagnostics from
  program stderr and **logs** them. Image pulls, platform mismatches and daemon
  errors all arrive by the same route, so removing one cause does not close the
  hole. It runs *before* the OOM heuristic, which greps stderr for `Killed` — a
  host message could otherwise fabricate an MLE verdict.

The patterns are specific rather than a blanket `^WARNING:`, because a candidate's
program may legitimately print that.

### RQ must never be given a decoding Redis client

Both queue modules keep **two** clients, and the distinction is load-bearing:

| Client | `decode_responses` | Used for |
|---|---|---|
| `get_redis()` | `True` | our own keys — pub/sub JSON, rate-limit counters, `llen` probes |
| `get_redis_raw()` | `False` | every `rq.Queue` and `rq.job.Job` |

RQ stores job data as binary — pickle by default, and zlib-compressed even with the
JSON serializer both queues now use (`src/judge/rq_safety.py`, `src/mailq/safety.py`).
Hand it the decoding client and every read tries to UTF-8 decode binary data:

```
'utf-8' codec can't decode byte 0x9c in position 1: invalid start byte
```

`0x9c` is pickle framing. The trap is that **enqueueing still works** — writing
bytes is fine — so the fault only appears when a result is read back, i.e. on the
candidate's first "Run Code", reported as `Judge error`. `test/test_queue_redis_serialization.py`
asserts the connection every RQ object is constructed with, because behavioural
tests pass either way.

The CLI workers (`rq worker-pool --url …`) build their own connection and default
to no decoding, so only the in-process call sites matter.

### Both RQ workers run only their own jobs, from JSON

Stock RQ runs any importable function a job names, with pickled arguments; unpickling
alone executes code. So anyone who can write to Redis could run code as a worker. The
judge worker is root-equivalent (docker group), and the email worker holds the key that
decrypts every company's mailbox password. Both are started with `--job-class` (an
allow-list: `ALLOWED_JOBS` in `src/judge/rq_safety.py` / `src/mailq/safety.py`) and
`--serializer rq.serializers.JSONSerializer`, and the API enqueues with the same
serializer. **API and workers must be deployed together**: each side refuses the other's
old format.

The email queue also has its own Redis database (`EMAIL_REDIS_DB`, default 1), resolved
by `src/mailq/queue.resolve_redis_url()`. `entrypoint.sh` calls that same function
rather than rebuilding the URL in shell, so the API and the worker can't disagree about
which database the queue is in.

### Email: the database is the queue of record, and three processes share it

Since the email API ([EMAIL_API.md](EMAIL_API.md)) a message is written to
`email_messages` + `email_outbox` in the request's transaction; Redis only carries
`{message_id, tenant_id}` jobs. Three processes are involved and all three must run:

| Process | Role | Holds |
|---|---|---|
| `hireflow-api` | accepts mail, publishes after commit | `EMAIL_CREDENTIAL_KEYS` (to save mailboxes) |
| `hireflow-email-worker` | delivers | `EMAIL_CREDENTIAL_KEYS` (to decrypt passwords) |
| `hireflow-email-dispatcher` | republishes due/lost outbox rows | **no secrets** — `entrypoint.sh` strips them |

Without the dispatcher nothing is lost, but mail committed while Redis was down,
every retry and every rate-limit deferral waits forever. Mail Routing shows its
heartbeat. Without Redis, only verification/reset codes are sent (in-process);
everything else waits in the outbox — there is no inline fallback any more.

The outbox claim uses `FOR UPDATE SKIP LOCKED` on MySQL 8 and MariaDB 10.6+, and a
claim-token `UPDATE … LIMIT` on MariaDB 10.5 (detected from `SELECT VERSION()`), so
running more than one dispatcher is safe but unnecessary.

### `.env` is not readable by every service user

`backend/.env` is mode 0600 owned by `hireflow` — it holds `SECRET_KEY`, the
database password and the Gemini key. The judge worker runs as **`hfjudge`**, which
holds the Docker access the API deliberately lacks, and therefore cannot read it.
That is the intended state: systemd reads the file as root and injects it via
`EnvironmentFile=` before dropping privileges, so every variable is already in
`os.environ` by the time Python starts.

A bare `load_dotenv()` raises `PermissionError` there and kills the worker on
import. Application code must call `src.env_boot.load_env()`, which treats an
unreadable file as normal and logs at INFO. Genuinely missing configuration still
fails loudly and specifically afterwards — `src/auth.py` refuses to start without
`SECRET_KEY`. The two standalone scripts that still call `load_dotenv()` directly
(`alembic/env.py`, `create_departments.py`) are always run by the file's owner;
`test/test_env_boot.py` enforces the rule for everything else.

---

## 2. Failures that produce no error

These all leave the deployment looking healthy. Check them explicitly.

| Thing | What you see | What is actually wrong |
|---|---|---|
| `EMAIL_CREDENTIAL_KEYS` wrong or missing | That company's mail is **blocked**, visibly | Decryption raises (`src/email_api/crypto.py`); the connection shows `credential_unreadable` in Mail Routing. Missing entirely: mailboxes cannot be saved and the platform's mailboxes are not seeded (CRITICAL at startup) |
| `EMAIL_ENCRYPTION_KEY` wrong (before re-encryption) | `reencrypt_email_credentials.py` reports unreadable rows | It is now only the *legacy* key; anything still under it fails loudly instead of sending the ciphertext as a password |
| Email dispatcher not running | New mail still sends | Retries, deferrals and anything committed during a Redis outage never go out; Mail Routing says "dispatcher not reporting" |
| Admin bootstrap vars unset | Everything green, candidates can register | No account exists that can administer anything |
| YOLO weights unresolvable | Proctoring runs normally | No object detection — phones, books, second people unflagged |
| `google-genai` missing | Health checks pass | Every AI feature fails at first use, three retries deep |
| `PORT` / `WEB_CONCURRENCY` set in `.env` | Site loads, `/health` green, one service `active` | `.env` overrode the unit; proctoring took the API's port and the API crash-looped ([§4](#4-configuration-which-file-wins)) |
| A service losing a port race | `systemctl status` cycles `activating`, nothing else | `Restart=always` hides it; the survivor serves the traffic |
| Docker CLI warnings | Candidates see them as *their* program's stderr | `docker run` merges container stderr into its own ([above](#the-judge-workers-home-directory-is-unreachable)) |
| `create_all` without `import main` | Plausible table count | Judge and question tables silently absent |
| Wrong schema name | `/health` reports 0 tables | App works; only the reporting queries were hardcoded |
| Stamped at head, schema is not | `alembic current` says `(head)`, upgrade says "nothing to do" | A migration older than the stamp never ran ([§3](#a-database-can-be-stamped-at-head-while-its-schema-is-not)) |
| Deploy without `alembic upgrade head` | One feature empty or 500; everything else fine | The code selects a table or column the database lacks — run `scripts/schema_diff.py` |
| cPanel database left on `utf8mb3` | Imports fine, works for months | The next migration that adds a foreign key fails with MySQL 3780 ([§3](#cpanel-databases-are-utf8mb3-imported-tables-are-utf8mb4)) |
| `POST /api/v1/db/backup` | `success: true` and a path | The dump runs in a background task *after* the response; a failure is log-only ([§3](#backups-the-apis-backup-is-real-now-but-it-is-asynchronous)) |
| `en_core_web_trf` missing | ATS scores fine | NER skill extraction returns `[]` |
| `whisper` / `resemblyzer` absent | Audio proctoring runs | Transcription and neural diarization are off — neither is in any requirements file |
| `JUDGE_REGRADE_TOKEN` unset, or `hireflow-regrade.timer` not enabled | Tests submit fine during a judge outage; scores look plausible | Coding answers deferred during the outage are never re-graded. The marks stay out of the denominator, so the candidate is scored out of a silently reduced total. `systemctl list-timers hireflow-regrade.timer` is the check |

---

## 3. Database: fresh install and migration are opposite

**Alembic cannot build a fresh database.** Revision `2a077fcbf6c4` has
`down_revision = None` but its first statement is `CREATE INDEX ... ON
interviews` — it *alters* tables it expects to exist. There is no `create_table`
for the base schema anywhere in the chain, because Alembic was adopted after the
schema already existed. On an empty database it fails immediately.

| Situation | Correct sequence |
|---|---|
| **Fresh install** | `init_db()` → `create_all()`, then `alembic stamp head` |
| **Importing a dump** | `mysql < dump.sql` (carries its own `alembic_version`), then `alembic upgrade head` |

Never `create_all` over an imported dump; never `upgrade head` on an empty one.

Two details that bite:

- **`import main` before `create_all`.** It only creates tables for models that
  have been imported, and the judge (`judge_models.py`) and question
  (`question_models.py`) models live in separate modules. Importing only
  `src.models.models` silently omits their tables.
- **`stamp` creates nothing.** It writes a version marker. Running it alone
  yields `(head)` and an empty database.

Do not use `setup_db.py` — it hardcodes `root`/`recruitment_db` credentials and
**drops tables**.

### A database can be stamped at head while its schema is not

`alembic current` is **not** evidence that a schema is correct, and this is a
permanent property of any database built the "fresh install" way above.

`create_all()` creates missing *tables* but never `ALTER`s an existing one.
`stamp head` then records that every migration ran. So the next release that adds
a **column** to a table `create_all` already built is skipped by `alembic upgrade
head` — Alembic considers ancestors of the stamped revision applied — while
nothing reports a problem.

Seen for real in the GCP export: stamped at `x4n5o6p7q8r9`, missing
`company_mailboxes.hourly_limit`, which `s9i0j1k2l3m4` adds. No automatic path
repairs it. Because SQLAlchemy emits every mapped column in its `SELECT`s, one
missing column takes out the whole model — the admin mailbox screen and every
outbound email that resolves a sender, all 500.

```bash
sudo -u hireflow /opt/hireflow/venv/bin/python scripts/schema_diff.py
```

Run that after an upgrade instead of trusting `alembic current`. It exits non-zero
on any missing table, column or index, so it works as a deployment gate. When it
reports a missing column on a database already at head, apply the DDL directly —
do **not** `downgrade` and re-`upgrade`, which replays a stamped chain over a
populated schema and is how the problem is created in the first place.

`repair_schema.py` answers a narrower question (stamp-or-upgrade, against a
hardcoded list of talent-pool columns) and would not have caught this.

### cPanel databases are utf8mb3; imported tables are utf8mb4

cPanel creates a database with a default of `utf8mb3`. Tables restored from a
`mysqldump` carry `utf8mb4` on each `CREATE TABLE`, so after an import the tables
and the database they live in disagree — and nothing complains, because every
imported table is internally consistent.

The mismatch only bites when a *later* migration creates a table with a foreign
key. That table inherits the `utf8mb3` default and MySQL rejects the constraint:

```
(3780, "Referencing column 'company_id' and referenced column 'id' ... are incompatible")
```

One line, safe to run at any time, and it rewrites nothing:

```bash
mysql -u <user> -p -e "ALTER DATABASE <db> CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
```

That repairs a database already in this state. Migrations defend themselves against
it independently, and must: **a `create_table` with a foreign key takes its collation
from `referenced_collation()` (`backend/migration_helpers.py`), never from the
database default and never from a hardcoded name.** No two environments agree on
what that default is — cPanel gives `utf8mb3`, CI's `mysql:8.0` service gives
`utf8mb4_0900_ai_ci`, and `init_db()` gives `utf8mb4_unicode_ci` — so a named
collation works in exactly one of them and is rejected with 3780 in the rest. That
is not hypothetical: `x4n5o6p7q8r9` already carried a workaround for it, and
`d0t1u2v3w4x5` still took CI red by naming `utf8mb4_unicode_ci` anyway.
Pass the result as `mysql_collate` alone; MySQL derives the character set
from it, and the charset behind `utf8mb3_general_ci` is spelled `utf8` on some
servers and `utf8mb3` on others.

`utf8mb3` also cannot store 4-byte characters, so a table left on it silently
mangles emoji and several scripts in candidate names and résumé text. After any
import, check that nothing was left behind:

```bash
mysql -u <user> -p <db> -e "SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_COLLATION NOT LIKE 'utf8mb4%';"
```

### Backups: the API's backup is real now, but it is asynchronous

`db_manager.backup_database()` shells out to `mysqldump` with the flags a managed
host needs (`--single-transaction --no-tablespaces --set-gtid-purged=OFF
--protocol=TCP`), raises on failure, and deletes any partial file. It produces a
restorable dump.

> Until `0ad127f` (2026-08-14) it did not. It built SQL by interpolating Python's
> `tuple` repr — no `CREATE TABLE`, `None` where `NULL` belongs,
> `datetime.datetime(…)` as a literal, no column list. If you have any backup file
> from before that date, it will not restore. Take a fresh one.

The remaining caveat is the **endpoint**, not the function. `POST /api/v1/db/backup`
runs the dump in a FastAPI background task, so it returns `success: true` and a
path *before* the work happens. A failure after that point exists only in the log:

```bash
journalctl -u hireflow-api | grep 'Database backup'
ls -lh /opt/hireflow/backups/
```

So for a backup something depends on — before a migration, before a schema change —
run `mysqldump` yourself and read the exit code, rather than trusting a 200.

The routine backup is neither of these: `hireflow-backup.timer` runs
`scripts/backup.py` nightly (database **and** uploaded files, encrypted, off-site,
checksummed, with a row-count manifest), and `scripts/restore_drill.py` proves a bundle
restores. See [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md). It passes the credentials
through a 0600 option file with `protocol=tcp`, for the same reason the API's backup uses
`--protocol=TCP`: the app connects over TCP, so that is the grant known to work.

---

## 4. Configuration: which file wins

**`.env` wins.** systemd applies `EnvironmentFile=` **after** `Environment=`, and
it does so regardless of the order those directives appear in the unit — a
`Environment=PORT=8001` written twenty lines *below* the `EnvironmentFile=` line
still loses. This is the opposite of what reading a unit file top-to-bottom
suggests, and an earlier version of this document had it backwards.

Never trust the unit file to tell you what took effect. Ask the process:

```bash
tr '\0' '\n' < /proc/$(systemctl show hireflow-proctor -p MainPID --value)/environ \
  | grep -E '^(PORT|WEB_CONCURRENCY|ROLE)='
```

### Pinning a constraint in a unit does not make it safe

Four services share one `.env`. `PORT=8000` and `WEB_CONCURRENCY=2` were in it,
left over from Cloud Run where there was a single service. They overrode the
`PORT=8001` and `WEB_CONCURRENCY=1` pinned in `hireflow-proctor.service`, so:

1. `hireflow-proctor` bound **8000** with **2** workers.
2. It won the startup race. `hireflow-api` crash-looped on `Connection in use`,
   restarting every 5s under `Restart=always`.
3. The site kept working — the proctor service is the same app, so it served the
   API's traffic quite happily.
4. Nothing listened on 8001. Apache returned 503 for the proctoring WebSocket,
   every candidate's face badge sat on "Checking…", zero frames were analysed, and
   every proctoring report came back **clean**.

Note how little of that looks like a fault: one `systemctl is-active` says
`active`, the site loads, `/health` is green. The tell was `journalctl -u
hireflow-proctor` showing the *same PIDs* that `ss -ltnp` showed on port 8000.

The fix was not a better comment. Proctoring now runs `ROLE=proctor`, and
`entrypoint.sh` hardcodes `-w 1` and port 8001 for that role — no variable to
override. **A correctness constraint belongs in the code path, not in a config
layer that something else can win.**

| Genuinely un-overridable (in `entrypoint.sh`) | Unit-provided, `.env` can override | Tuning, set in `.env` |
|---|---|---|
| proctor worker count (`-w 1`) | `ROLE`, `BIND_HOST` | `PROCTOR_INFERENCE_SLOTS` |
| proctor port (8001) | `PORT` (API only) | `PROCTOR_MAX_SESSIONS` |
| judge queue name default | `WEB_CONCURRENCY` (API only) | `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` |
| | `PROCTOR_PREWARM`, `HF_HOME`, `PATH` | everything else |

Do not reintroduce `PORT` or `WEB_CONCURRENCY` into `.env`.

After editing `.env`: `systemctl restart <svc>`.
After editing a `.service`: `systemctl daemon-reload && systemctl restart <svc>`.

### `EMAIL_ENCRYPTION_KEY` resolution order

`src/security.py` resolves it as:

1. `EMAIL_ENCRYPTION_KEY`, used as-is
2. **`SECRET_KEY`**, SHA-256 → urlsafe base64
3. the literal `hireflow-dev-key-change-me`

A deployment that never set the first still encrypted its data — with a key
derived from whatever `SECRET_KEY` was. Generating a fresh `SECRET_KEY` on a new
server therefore breaks decryption of imported rows.

Use `scripts/find_email_key.py` to determine the right value empirically rather
than guessing. It matters that this is tested, not deduced: a secret created with
`echo "v" | gcloud secrets create` stores `v\n`, and that trailing newline
produces a completely different key from the same visible value.

### `DATABASE_URL` special characters

Tested against SQLAlchemy's parser:

- **Safe unencoded:** `&` `:` `/` `#` `?` `!` `;` `,` `-` `.`
- **Breaks silently:** `@` — it splits userinfo from host, so the hostname
  becomes wrong rather than the password. Percent-encode as `%40`.

### Domain-dependent values

Only two: `FRONTEND_URL` and `PLATFORM_BASE_URL`. Both build links in outbound
email, so a stale value sends candidates to a dead URL while the app looks fine.

`HIREFLOW_BASE_DOMAIN` defaults to `hireflow.io`. Left unset, per-tenant slug
subdomains never resolve and every request is treated as a single tenant —
correct for a single-company deployment, silently wrong if you later expect
`acme.career.innomaxskills.com` to work.

**No application code contains the hostname.** The backend resolves the tenant
from the `Host` header at request time; the frontend derives its API base and
WebSocket URLs from `window.location`. Renaming the domain touches only config
and docs.

---

## 5. Shared-server rules

This box hosts five cPanel accounts on one Apache, one database engine and one
CPU. `innomaxskills` is only one of them.

### Isolated — cannot affect the others

Subdomain, database (cPanel prefixes scope it), the Apache vhost include (its
path is read only for this account's vhost), the web root, `/opt/hireflow`, and
the `hireflow` / `hfjudge` users.

### Server-wide — affects everyone

Apache modules, Python 3.11, EPEL and shared libraries, Redis, Docker. All are
**additive**; nothing in the deployment removes or downgrades software the other
accounts use.

### Never upgrade the database engine

An earlier draft of the guide recommended switching to MySQL 8.0 to match the old
Cloud SQL setup. On a shared host that is the wrong call: the engine is
server-wide, cPanel's MariaDB → MySQL upgrade is **irreversible**, and it could
break the other four live sites.

It is also unnecessary. The ORM has 7 JSON columns, all storing whole documents
serialised in Python — no SQL-level JSON functions — and no MySQL-8-only syntax
anywhere. The app runs correctly on MariaDB 10.5+.

*(This particular server turned out to already run MySQL 8.0.46, making the
question moot — but the reasoning stands for any future host.)*

### Resource limits protect the neighbours

| Service | `MemoryMax` | `CPUWeight` |
|---|---|---|
| `hireflow-proctor` | 10 GB | 40 |
| `hireflow-api` | 8 GB | 50 |
| `hireflow-judge-worker` | 2 GB | 30 |
| `hireflow-email-worker` | 1 GB | 30 |
| `hireflow-email-dispatcher` | 512 MB | 20 |

21.5 GB of 32 GB, leaving 10.5 GB for MySQL, Apache, cPanel and the other accounts.

- **`MemoryMax` is a hard ceiling** so systemd kills *our* service rather than
  letting the kernel OOM killer pick a victim — possibly MySQL or another
  account's PHP workers.
- **`CPUWeight`, not `CPUQuota`.** A quota throttles even on an idle machine.
  Weight applies only under contention: at 40–50 against a default of 100, we
  yield when the box is busy and use everything spare when it is not.
- The judge worker's limits bound **only the queue runner**. The sandboxes it
  launches are separate containers, capped independently in
  `src/judge/sandbox.py`.

### The `docker` group is root-equivalent

A member can bind-mount the host filesystem into a container. It is confined to
`hfjudge`, a nologin user running one process — meaningfully safer than granting
it to the account serving web traffic, but still a real privilege on a shared
box. The alternative is running Judge0 on a separate machine with
`JUDGE_BACKEND=judge0`.

---

## 6. Dependencies

**Always install `requirements-cpanel.txt`, never `requirements.txt`.** The plain
file pulls the CUDA build of torch (~2 GB of unusable `nvidia-*` wheels) and
`psycopg2-binary`, which fails to build without `pg_config` and aborts the whole
install.

**`--extra-index-url` is additive, not exclusive.** pip may still prefer PyPI's
CUDA wheel since `torch==2.1.1` exists on both indexes. To force the CPU build,
use `--index-url`, which *replaces* the index list.

**Do not judge the torch build by grepping for `nvidia` packages.** `ultralytics`
depends on `nvidia-ml-py`, a ~50 KB pure-Python GPU-stats wrapper containing no
CUDA libraries. Check `torch.version.cuda` — `None` means the CPU build. A CPU
venv is roughly 2–3 GB; a CUDA one 6–8 GB.

**Dropped from the cPanel set:** `deepface` (zero import sites; pulled TensorFlow
transitively — face recognition is entirely OpenCV SFace/YuNet),
`psycopg2-binary`, `boto3`, `minio`, `gensim`, `nltk`, `dramatiq`,
`fastapi-users`, `watchdog`, `black`, `flake8`, `google-cloud-storage`.
**Added:** `dnspython`, which the code imports but `requirements.txt` never
declared.

### Frontend

`npm ci` requires the lockfile to match `package.json` exactly and will refuse
otherwise. `npm install` reconciles it — but rewrites `package-lock.json`, a
tracked file, so `git status` shows a modification afterwards. Commit it or
`git checkout --` it before the next pull, or you will hit a merge conflict
mid-deploy.

`npm audit --omit=dev` is the number that matters — it filters out the build
chain, which never reaches a browser. As of this deployment it reported 7 issues
in shipped code: `react-router` (open redirect via protocol-relative `//` URLs —
genuinely browser-exploitable) and `axios`. Most axios advisories concern its
**Node HTTP adapter** (proxy handling, SSRF, metadata exfiltration), which
browsers never use; the browser-relevant subset is the prototype-pollution
gadgets, XSRF token leakage and a few ReDoS issues.

Patch **after** a verified working baseline, with plain `npm audit fix` (never
`--force`), then rebuild and re-test.

---

## 7. Verification: check the thing, not a proxy

Every wrong call during this deployment came from checking a stand-in instead of
the fact itself.

| Bad check | Why it lied | Correct check |
|---|---|---|
| `pip list \| grep -c nvidia` | `nvidia-ml-py` is harmless | `torch.version.cuda is None` |
| Table name guessed from memory | The model's table is `interview_questions`, not `questions` | Diff `Base.metadata.tables` against the live schema |
| "`EMAIL_ENCRYPTION_KEY` is absent, so there is nothing to carry over" | The key was derived from `SECRET_KEY` | Decrypt a real ciphertext with each candidate |
| Assuming an API key format is wrong from its prefix | `AQ.` keys are valid | Call the API and see |
| `[ok] ModelSet build — engines={...}` | A disabled engine hid inside the dict | Report each engine on its own line |

The pattern: **assert on the property you care about, against real data.**

---

## 8. This server, as found

Confirmed during deployment — re-check on any new host.

| Fact | Value | Consequence |
|---|---|---|
| OS | AlmaLinux 9.8, **not CloudLinux** | No per-account LVE memory caps |
| CPU / RAM | 8 vCPU (AMD EPYC, KVM), 31 GB | KVM means Docker works properly |
| Database | MySQL **8.0.46** already installed | No engine change needed |
| SELinux | **Disabled** | `httpd_can_network_connect` not required |
| Firewall | `csf` and `firewalld` both inactive | Docker's iptables rules can't conflict — but **never bind services to `0.0.0.0`** |
| Redis | Already running, **`db0` in use** | Use a different database number (`/5`) |
| `mod_proxy_wstunnel` | Already installed | Step 2.1 was a no-op |
| EPEL | Already enabled | — |
| cgroups | v2 (RHEL 9 default) | Vindicates the Docker judge over Judge0, which needs v1 |

---

## 9. Open items

- **`en_core_web_trf` is a transformer used for tokenisation.**
  `extract_skills_from_tokens()` matches tokens against a fixed
  `CANONICAL_SKILLS_SET` and never uses the model's NER output. A 12 MB
  `en_core_web_sm` would do the same job — the model name is hardcoded, so this
  needs a small code change.
- **`whisper` and `resemblyzer` are in no requirements file**, so transcription
  and neural speaker diarization are permanently off. Either install them
  deliberately or remove the dead code paths.
- **Capacity numbers are estimates.** `PROCTOR_INFERENCE_SLOTS=6` /
  `PROCTOR_MAX_SESSIONS=9` and "8–10 concurrent candidates" are reasoned from
  per-frame cost, not measured on this hardware. `GET /api/v1/proctor/warmup`
  returns `peak_in_use` and `waits`; a climbing `waits` means frames are queueing
  and the box is at its inference ceiling.
- **MediaPipe was never exercised on the development machine** (not installed on
  Windows), so the shared-model tests covered the SFace path only. The MediaPipe
  sharing path and the lock added to `PoseAnalyzer` are first proven on the
  server.
- **Rotate the old Gmail app password.** It remains in the *previous*
  repository's history.
