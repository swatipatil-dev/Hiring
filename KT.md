# HireFlow — Knowledge Transfer

Start here if you have just joined. This is the orientation document: what the product is,
how to run it, where things live, and — most importantly — the things that have already
caused outages.

It does not duplicate the other docs. It tells you which one to open, and warns you about
the several that are out of date.

> **Read §7 (Landmines) before you run anything.** One command recommended in `CLAUDE.md`
> will drop your local database tables.

---

## 1. What HireFlow is

A **multi-tenant, AI-assisted recruitment platform (ATS)**. One deployment serves many
client companies; the platform's own organisation is *Innomax*. Live at
`career.innomaxskills.com`.

The business loop:

```
superadmin onboards a client company
   → that company's admin creates HR + interviewer users
      → HR posts a job
         → candidates apply via the public career page
            → resumes auto-scored (ATS)
               → shortlisted candidates sit an AI-generated test, AI-proctored
                  → interviewers give feedback
                     → HR sends an offer → hired
```

### The five roles

| Role | What they do | Enforced in |
|---|---|---|
| `superadmin` | Onboards client companies, seeds their first admin, custom domains, DB admin | `routes/superadmin_routes.py` |
| `admin` | Company admin: creates HR/interviewers, departments, mailboxes, analytics | `routes/admin_routes.py` |
| `hr` | Jobs, ATS, talent pool, interviews, offers, mail | `routes/hr_routes.py` |
| `interviewer` | Authors tests, AI question generation, feedback | `routes/interviewer_routes.py`, `enhanced_interviewer_routes.py` |
| `candidate` | Browse/apply, profile, sit tests, respond to offers | `routes/candidate_routes.py`, `candidate_profile_routes.py` |

Roles are a plain string on `users.role` (`src/models/models.py:239`). The gate is
`require_role()` at `src/auth.py:248`, with `AuthMiddleware.get_current_*` wrappers at
`:419-460`.

Two things to know:
- **`get_current_hr` also admits `admin`** (`src/auth.py:432`). Not a bug — admins are
  expected to do HR work.
- **Registration cannot create a superadmin** (`src/auth.py:285`). It is DB-seeded only, by
  `src/bootstrap.py` at startup.

### The hiring pipeline — and a trap

You will hear "12-phase workflow". That is **documentation vocabulary, not a code
constant**. In code there is a `current_stage` string on `candidates` plus an immutable
audit trail in `candidate_stage_history`.

Stages actually written: `registered → ats_screening → hr_review → shortlisted →
test_submitted/test_completed → interview → interview_completed → offer_sent → hired`,
plus `rejected` / `offer_rejected`.

**`applied` is only ever a history entry.** On apply, `current_stage` goes straight to
`ats_screening`. The frontend normalises it (`HRDashboard.jsx:66`). If you filter for
`applied` you will get nothing.

⚠️ There are **three competing stage vocabularies** in the tree. `routes/advanced_features_routes.py`
uses `screening` / `assessment` / `offer` — strings **nothing else writes**. That router is
largely orphaned analytics and *has no role guards at all*. Don't copy its patterns.

---

## 2. Getting it running

### Install first

| Need | Why | Without it |
|---|---|---|
| **Python 3.11** | pinned by CI and the wheels | — |
| **MySQL 8** | the database. Not SQLite, not Postgres | nothing runs |
| **Node 20** | frontend | no UI |
| Gemini API key | all AI features | AI silently does nothing |
| tesseract binary | OCR of scanned resumes | image-only PDFs extract as empty |
| Docker Desktop | the coding judge | judge tests fail |
| Redis | queues | email/judge run inline instead — fine locally |

### Commands

```bash
# ── Backend — ALL backend commands run from backend/ ──
cd backend
pip install -r requirements-cpanel.txt      # NOT requirements.txt — see §7
python -m uvicorn main:app --reload --port 8000

# ── Tests ──
pytest                       # needs MySQL running
pytest -m "not slow"         # what CI runs
pytest test/test_ats_scoring.py

# ── Frontend ──
cd frontend
npm install                  # NOT npm ci — see §7
npm run dev                  # :3000, proxies /api → :8000
```

### Setting up your database

**Do not run `setup_db.py`.** See §7.

```bash
cd backend
python -c "
import sys; sys.path.insert(0, '.')
import main
from src.config.database_config_mysql import init_db
print('created:', init_db())
"
alembic stamp head
```

`import main` is **required, not decorative** — `create_all()` only creates tables for
models that have been *imported*, and the judge and question tables live in separate
modules. Importing `src.models.models` alone silently skips them. Expect ~44 tables.

### First failures you will hit

- **`SECRET_KEY` unset → the app refuses to import** (`src/auth.py:40`). Generate one:
  `python -c "import secrets; print(secrets.token_urlsafe(48))"` and put it in `backend/.env`.
- **`backend/.env.example` is stale** — it has a *PostgreSQL* DSN and no `GEMINI_API_KEY`
  line at all. Copy `backend/.env.cpanel.example` instead and adapt it.
- **Tests need a real MySQL** at `127.0.0.1:3306`, database `recruitment_test_db`, root
  password from `DB_PASSWORD` (default `1234`) — `conftest.py:18`.

---

## 3. Where things are

```
backend/
├── main.py                  entry point — routers, lifespan, static mounts, /health
├── entrypoint.sh            production launcher; dispatches on ROLE
├── routes/                  18 modules, one per role/feature
├── src/
│   ├── auth.py              JWT, bcrypt, require_role, create_user
│   ├── models/models.py     34 tables (~44 total incl. judge + question modules)
│   ├── services/            ATS, Gemini, email, talent pool, storage, notifications
│   ├── tenancy/             the multi-tenant filter — read this early
│   ├── middleware/          tenant_domain.py — Host header → Company
│   ├── judge/               code sandbox, queue, languages, SQL harness
│   └── mailq/               email queue + rate limiting
├── proctoring/              face/pose/object/audio analysis, model pool
├── alembic/versions/        34 migrations
├── scripts/                 ~35 ops scripts — schema_diff.py is the important one
└── test/                    54 test files

frontend/src/
├── api/client.js            axios, baseURL '/api/v1', JWT interceptor
├── context/AuthContext.jsx  auth state
└── pages/{admin,candidate,hr,interviewer,public,shared,superadmin}/
```

**Scale:** 244 Python files, 71 frontend files, 18 route modules, 34 migrations, 52 test
files, 17 docs.

### The files you will actually touch

| Lines | File |
|---|---|
| 5983 | `backend/routes/hr_routes.py` — the biggest file in the repo |
| 5094 | `frontend/src/pages/hr/HRDashboard.jsx` |
| 4791 | `frontend/src/pages/interviewer/InterviewerDashboard.jsx` |
| 2734 | `frontend/src/pages/candidate/OnlineTest.jsx` |
| 2285 | `backend/routes/candidate_profile_routes.py` |
| 2197 | `backend/src/services/enhanced_ats_service.py` |
| 1115 | `backend/src/models/models.py` |

### Dead code — do not spend time here

- `backend/ats_manager.py` — imports names that **do not exist** in `src/constants.py`.
  Nothing registers it, despite `CLAUDE.md` describing it as live business logic.
- `backend/ats_routes.py` (repo root) — duplicate of `routes/ats_routes.py`; only the
  latter is registered.
- `routes/enhanced_candidate_routes.py`, `routes/enhanced_hr_routes.py` — never imported.

---

## 4. Tech stack

**Backend** — FastAPI 0.115, SQLAlchemy 2.0, Alembic, PyMySQL, MySQL 8 / MariaDB 10.5+,
Pydantic 2.9, python-jose + bcrypt (HS256 JWT, 24h), Redis + RQ, gunicorn + uvicorn workers.

**Frontend** — React 18.3, Vite 5.4, react-router 6, axios, framer-motion, lucide-react,
recharts, Monaco editor. Tests: vitest + testing-library.

**AI/ML** — each piece does one job:

| Purpose | What |
|---|---|
| Question/JD generation, cover letters, resume gap-fill | **Google Gemini** (`gemini-flash-latest`) |
| Semantic ATS scoring | `sentence-transformers` all-MiniLM-L6-v2 → falls back to TF-IDF |
| Skill NER | spaCy `en_core_web_trf` — **not installed anywhere**, so this silently returns `[]` |
| Fuzzy skill matching | rapidfuzz |
| Proctoring: faces | OpenCV DNN — YuNet (detect) + SFace (recognise) |
| Proctoring: pose/landmarks | MediaPipe `.task` models |
| Proctoring: objects | YOLOv8n — phone, book, second person |
| Proctoring: audio | webrtcvad. Whisper/resemblyzer paths exist but are in **no** requirements file |

**ATS score** = skills 35% + semantic 35% + experience 20% + keywords 10%.
(`main.py:12` says "7-factor" — stale.)

---

## 5. Data model

Read `src/tenancy/` before anything else. Every tenant table carries `company_id` and
subclasses `TenantScoped`, and two SQLAlchemy hooks in `src/tenancy/events.py` do the work:

- **read** — auto-injects `WHERE company_id = <current>`
- **write** — auto-stamps `company_id` on new rows

The company is bound to a contextvar during auth (`src/auth.py:234`). Superadmin binds to
`None`, so the filter is a no-op for them.

> **`NULL = X` is never true in SQL.** A row inserted with a NULL `company_id` becomes
> permanently invisible. This is documented as the number-one cause of *"the data is in the
> database but the dashboard is blank"*. See `docs/DASHBOARD_DATA_PITFALLS.md`.

### Tables that matter

| Table | Notes |
|---|---|
| `companies` | The tenant. `slug`, `api_key` (drives `/app?key=`), `custom_domain`, branding |
| `users` | Unique on **`(company_id, email)`** — the same person can exist at many tenants |
| `job_postings` | `status: draft/pending_approval/active/closed` |
| `candidates` | One row **per person per opening**. `job_id IS NULL` = signup placeholder |
| `ats_results` | Unique `(candidate_id, job_id)`. Four sub-scores + breakdown JSON |
| `resume_pool` | Talent Pool — not tied to a job or user. `profile_source` records regex-vs-AI provenance |
| `candidate_stage_history` | Immutable audit trail |
| `interviews` | ⚠️ **`id` is an autoincrement Integer, not a UUID** (`models.py:583`) — the only one. Doubles as the *test* record |
| `candidate_profile_ext` | Rich profile + 5 child tables (work, education, skills, certs, projects) |

Everything else uses UUID strings (`String(36)`).

---

## 6. Deployment

```
Browser → https://career.innomaxskills.com
   ├─ /                    → /home/innomaxskills/public_html/career/   (built React)
   ├─ /api/                → 127.0.0.1:8000   hireflow-api      (3 workers)
   └─ /proctor/ + sockets  → 127.0.0.1:8001   hireflow-proctor  (1 worker)
```

Code lives at **`/opt/hireflow`**. Config at **`/opt/hireflow/backend/.env`** — mode 0600,
owned by `hireflow`, **not in git**, holds the only copy of `SECRET_KEY` and
`EMAIL_ENCRYPTION_KEY`.

### The five services

| Unit | User | Purpose |
|---|---|---|
| `hireflow-api` | `hireflow` | Everything except proctoring |
| `hireflow-proctor` | `hireflow` | Proctoring only — **must be 1 worker**, see §7 |
| `hireflow-judge-worker` | **`hfjudge`** | Runs candidate code in Docker. Separate user because docker-group = root-equivalent |
| `hireflow-email-worker` | `hireflow` | Queued outbound mail |
| `hireflow-regrade.timer` | `hireflow` | Every 15 min, recovers coding marks deferred during a judge outage |

Apache is a **reverse proxy only** — it serves no application code. Its config must be
copied to *both* `std` and `ssl` paths, because cPanel rebuilds and would discard one.

### Deploying

**`docs/DEPLOY_UPDATE.md` is the authority.** Its one rule: *code and schema move together,
and you verify that they did.*

`git pull` does **not** update four things, each of which has caused an outage:

| Stale copy | Fixed by | Symptom |
|---|---|---|
| Database schema | `alembic upgrade head` | A feature silently empty, or 500s |
| systemd units | `cp` + `daemon-reload` | Config changes have no effect |
| Apache config | `cp` + `restartsrv_httpd` | WebSockets fail, routes 404 |
| Built frontend | `npm run build` + `rsync` | Old UI against a new API |

### CI/CD

`feature/* → develop → testing → main → production`. **`testing` does not deploy** — there
is only one server.

- **`ci.yml`** runs on every branch: `lint`, `backend`, `migrations`, `frontend`
- **`deploy.yml`** — merge to `main` prepares a release; a human approves it. Refuses to run
  from any branch but `main`
- **`prod-deps.yml`** — weekly check that `requirements-cpanel.txt` actually installs

Setup instructions: `docs/CICD_SETUP.md`.

---

## 7. Landmines

Every one of these has already caused a real incident. This is the section that earns the
document.

### 🔴 `setup_db.py` will drop your tables

`CLAUDE.md:33` tells you to run `python setup_db.py`. **Don't.** It hardcodes
`root:root@localhost/recruitment_db` and does `DROP TABLE IF EXISTS` on 15 tables
(`setup_db.py:23`). `docs/DEPLOYMENT_NOTES.md:193` forbids it. Use the `init_db()` snippet in
§2 instead.

### 🔴 `.env` silently overrides the systemd unit files

systemd applies `EnvironmentFile=` **after** `Environment=`, regardless of the order in the
file. So a value in `.env` beats the one pinned in the unit.

The incident: `PORT=8000` left in `.env` beat `PORT=8001` in `hireflow-proctor.service`. The
proctor bound 8000, won the race, and the API crash-looped. **The site kept working** — same
app — but nothing listened on 8001, so every candidate's face check sat on "Checking…", zero
frames were analysed, and **every proctoring report came back clean**.

Never put `PORT` or `WEB_CONCURRENCY` in `.env`. Never trust the unit file — ask the process:

```bash
tr '\0' '\n' < /proc/$(systemctl show hireflow-proctor -p MainPID --value)/environ | grep -E '^(PORT|WEB_CONCURRENCY|ROLE)='
```

### 🔴 Proctoring must run as exactly one worker

`proctoring/routes.py` keeps sessions in a **process-local dict of live MediaPipe handles** —
unserialisable, so it cannot go to Redis. `POST /sessions/start` creates the session in one
process; the WebSocket is a separate connection that gunicorn hands to an arbitrary worker.
With N workers it lands correctly 1/N of the time and closes 4004 otherwise.

Capacity comes from `PROCTOR_INFERENCE_SLOTS`, **never** from worker count.

### 🔴 Rotating `SECRET_KEY` silently breaks email

The email encryption key resolves as: `EMAIL_ENCRYPTION_KEY` → else **`SECRET_KEY` hashed** →
else a hardcoded dev string. So a deployment that only ever set `SECRET_KEY` encrypted its
mailbox passwords with a key derived from it.

And nothing errors: `decrypt()` returns `""` rather than raising, so callers fall back to
using the ciphertext as the SMTP password. Check whether `EMAIL_ENCRYPTION_KEY` is set before
you touch `SECRET_KEY`.

### 🔴 Alembic cannot build a fresh database

The first revision has no parent but its first statement is `CREATE INDEX ... ON interviews`
— it *alters* tables it expects to exist. Alembic was adopted **after** the schema existed.

- Fresh database → `init_db()` (create_all) → `alembic stamp head`
- Imported dump → `mysql < dump.sql` → `alembic upgrade head`
- **Never** `create_all` over an imported dump; **never** `upgrade head` on an empty one

The lasting consequence: because production was built with `create_all` + `stamp`, the
version table claims every migration ran when none did. A later migration that adds a
**column** to an existing table is skipped silently, while `alembic current` cheerfully
reports `(head)`.

**Run `python scripts/schema_diff.py` after every upgrade. Do not trust `alembic current`.**
This exact failure shipped a missing column and took out the Talent Pool.

### 🟠 `npm install`, not `npm ci` — and never on the server

The lockfile drifts from `package.json`, so `npm ci` fails with `EUSAGE`. But `npm install`
**rewrites the tracked lockfile**, dirtying the tree so the *next* `git pull` conflicts
mid-deploy. That is why CI builds `dist/` on a disposable runner and rsyncs it.

⚠️ `docs/CPANEL_DEPLOYMENT.md:975` still says `npm ci`. That line is wrong.

### 🟠 Nothing may depend on the current working directory

systemd, cron and your shell all start with different CWDs. Three separate bugs came from
this — including ultralytics silently *downloading* weights into the CWD and disabling object
detection. Resolve paths against `__file__`. Uploads resolve against `HIREFLOW_DATA_DIR`.

### 🟠 RQ must never get a decoding Redis client

`get_redis()` decodes; `get_redis_raw()` does not. RQ stores arguments and results as
**pickle**. The trap: enqueueing works fine, so the fault only appears when a result is read
back — on a candidate's first "Run Code", as `'utf-8' codec can't decode byte 0x9c`.

### 🟠 `POST /api/v1/db/backup` returns success before the dump runs

It runs in a background task; failures exist only in the log. For anything that matters, run
`mysqldump` yourself and check the exit code.

### 🟡 Other silent failures

- Admin bootstrap vars unset → everything looks green, but no account can administer anything
- `google-genai` missing → health checks pass, AI fails at first use
- `en_core_web_trf` missing → ATS scores fine, NER skill extraction returns `[]` (this is the
  current state, in production)
- `JUDGE_REGRADE_TOKEN` unset → every regrade sweep 401s and candidates keep a permanently
  short denominator
- `DATABASE_URL` containing `@` in the password → breaks *silently*, because it splits
  userinfo from host. Percent-encode as `%40`

---















## 8. Testing

```bash
cd backend && pytest -m "not slow"     # what CI runs
cd frontend && npm test
```

Tests need a real MySQL. The suite builds its schema with `create_all`, so **it never runs a
migration** — that gap is covered by the separate `migrations` CI job.

**What is not covered:** proctoring and the coding judge have **no CI coverage at all** —
`requirements-ci.txt` omits `opencv-python`, `mediapipe` and `ultralytics`, so those tests
*skip rather than run*. That is why the deploy has a required human reviewer.

Watch for **conditional skips that hide failures**: several tests call `pytest.skip()` when a
precondition is missing ("Candidate application failed — skipping offer test"). They report
green while covering nothing.

At the time of writing, 17 tests fail locally for environmental reasons — 16 from an old
`pytest-asyncio` on Python 3.14, and 1 because `EMAIL_ENABLED=false` locally. Neither is a
code defect.

---

## 9. Which docs to trust

**Trust in this order:** the code → the four deployment docs → `CLAUDE.md` → everything else.

> **Rule of thumb:** if a doc mentions **Ollama, PostgreSQL, OpenAI, or a root-level
> `main.py`**, it predates the current architecture. Stop reading it.

### Current and authoritative

| Doc | For |
|---|---|
| `docs/DEPLOY_UPDATE.md` | **Updating a live deployment.** The one to follow |
| `docs/DEPLOYMENT_NOTES.md` | *Why* deployment is the way it is. Read before changing anything there |
| `docs/CPANEL_DEPLOYMENT.md` | First install, 14 steps (one stale line: `npm ci` at :975) |
| `docs/CICD_SETUP.md` | The CI/CD pipeline and its one-time setup |
| `docs/DASHBOARD_DATA_PITFALLS.md` | The `company_id` NULL trap. High value |
| `docs/CODING_JUDGE.md` | The code-assessment module |
| `docs/TENANT_CUSTOM_DOMAINS.md` | White-label domains (has a ✅Built/🔜Planned legend) |
| `CLAUDE.md` | Best single starting point — but see the two defects below |

### Stale — do not trust

| Doc | Why |
|---|---|
| `README.md` | Says **PostgreSQL**, "14 Tables", Python 3.8+, root-level layout. All wrong |
| `AGENTS.md` | Says questions are generated **via Ollama**. There is no Ollama in this codebase |
| `WORKFLOW_AND_STRUCTURE.md` | Root-level tree, Ollama, "17 tables". Its Phase 0-11 prose is still the clearest description of *intent* — use it for that only |
| `backend/.env.example` | PostgreSQL DSN, no `GEMINI_API_KEY` line. Use `.env.cpanel.example` |
| `docs/QUICKSTART.md` | Predates the backend/frontend split |
| `docs/MYSQL_SETUP_COMPLETE.md` | Stale, and publishes `root`/`1234` |
| `backend/test/README.md` | Accurate on fixtures; stale where it says "requires Ollama" |

**Two defects in `CLAUDE.md`**, which is otherwise the best starting point: line 33
recommends the destructive `setup_db.py`, and it describes `ats_manager.py` as live business
logic when it is broken and unreachable.

---

## 10. Your first week

1. Get it running locally (§2). Expect a day.
2. Read `src/tenancy/` and `docs/DASHBOARD_DATA_PITFALLS.md`. Multi-tenancy touches every
   query you will write.
3. Read `docs/DEPLOYMENT_NOTES.md` end to end. It is long, but every section is a bug
   somebody already paid for.
4. Pick a small task in `routes/hr_routes.py` — it is the busiest file, so it is where you
   learn the conventions.
5. Before your first PR, read §7 again.

**When something behaves oddly, suspect in this order:** a NULL `company_id` making rows
invisible → a stale copy that `git pull` didn't update → an `.env` value overriding a unit
file → a missing column on a database stamped at head.
