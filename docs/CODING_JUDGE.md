# Coding Judge — online code-assessment module for HireFlow

A HackerRank/LeetCode-style coding-assessment feature added **inside** the existing
HireFlow app (FastAPI + MySQL + React). It reuses HireFlow's JWT auth, multi-tenancy
(`company_id` auto-scoping), and React frontend, and adds the one thing the repo
lacked: a **secure code-execution judge**.

This is **Phase 1** — a correct, secure vertical slice. Later phases (contests,
real-time leaderboards, plagiarism/AI-code detection, K8s autoscaling, etc.) are
mapped at the end.

---

## What was built

| Layer | Files |
|---|---|
| Models | `src/models/judge_models.py` (`CodingProblem`, `ProblemTestCase`, `CodeSubmission`, `SubmissionTestResult`) — imported into `src/models/models.py` |
| Migration | `alembic/versions/l2b3c4d5e6f7_add_coding_judge_tables.py` (+ merge `k1a2b3c4d5e6`) |
| Engine | `src/judge/` — `languages.py`, `sandbox.py`, `verdict.py`, `runner.py`, `queue.py`, `worker.py` |
| Sandbox images | `judge/images/Dockerfile.{python,cpp,node,java}` |
| Infra | `scripts/build_judge_images.{ps1,sh}`, `deploy/cpanel/hireflow-judge-worker.service` |
| API | `routes/judge_routes.py` (prefix `/api/v1/judge`), registered in `main.py` |
| Frontend | `frontend/src/pages/coding/` (ProblemList, ProblemSolve, SubmissionHistory, AdminProblemEditor) + routes in `App.jsx` + Sidebar links |
| Tests | `test/test_judge_verdict.py` (unit), `test/test_judge_runner_integration.py` (Docker, auto-skips) |

Languages supported in Phase 1: **Python, C++ (g++), JavaScript (Node)**. Java/Go are
stubbed in `languages.py` for Phase 2.

---

## Execution flow (submission → verdict)

1. **Submit** — `POST /api/v1/judge/submissions` persists a `code_submissions` row
   (`status=queued`) and enqueues a judging job. Tenant `company_id` is auto-stamped.
2. **Dispatch** — `src/judge/queue.py` pushes the job to **Redis + RQ**. If Redis is
   down or `JUDGE_SYNC=1`, it runs **inline** (dev fallback).
3. **Worker** (`src/judge/worker.py`) loads the submission + problem + test cases,
   marks `running`, and publishes status to Redis channel `submission:{id}`.
4. **Runner** (`src/judge/runner.py`) writes the source to a throwaway work dir, then:
   * **compiles** once in a sandbox (C++), → **CE** on failure;
   * **runs each test case** in a fresh sandbox container with the problem's
     CPU/memory/time limits, capturing stdout/stderr/time/memory/exit code.
5. **Classify** (`src/judge/verdict.py`): timeout→**TLE**, OOM→**MLE**, non-zero
   exit→**RE**, output mismatch (whitespace-normalized)→**WA**, else **AC**. Overall
   verdict = worst case; score = sum of passed test-case weights.
6. **Persist** — `submission_results` rows + the `code_submissions` summary
   (`status=done`, verdict, score, time, memory). Hidden-case I/O is never exposed.
7. **Stream** — the browser opens `WS /api/v1/judge/ws/{id}?token=…` and sees
   `queued → running → done+verdict` live (DB-polled; Redis pub/sub also published for
   the multi-node scale path).

---

## Security model (the sandbox)

Every execution runs in a throwaway Docker container (`src/judge/sandbox.py`) with:

- `--network none` — no internet / localhost.
- `--read-only` root FS; only a size-capped `--tmpfs /tmp` is writable (workdir is RO at run time).
- `--memory` / `--memory-swap` equal (no swap) → OOM kill on excess → **MLE**.
- `--cpus`, `--pids-limit 64` (fork-bomb guard), `--ulimit nofile/fsize`.
- `--cap-drop ALL`, `--security-opt no-new-privileges`, `--user 65534:65534` (nobody, never root).
- Host-enforced **wall-clock timeout** → container force-killed → **TLE**.
- `--rm` cleanup + a kill-by-name reaper for timed-out containers.
- Output is read in chunks and anything past 256 KB is discarded as it arrives, so a
  program printing gigabytes cannot exhaust the worker's memory.
- Only the job's own temp directory is mounted. No app code, `.env` or other job's
  files are visible, and only two harmless environment variables are passed in.

**Production hardening (Linux):** set `JUDGE_RUNTIME=runsc` (gVisor) and
`JUDGE_SECCOMP=/path/profile.json` for kernel-level isolation of untrusted code.
Install steps: [CPANEL_DEPLOYMENT.md, Step 2.5](CPANEL_DEPLOYMENT.md). The worker
logs a warning at every start in production while `JUDGE_RUNTIME` is unset.

### Around the sandbox

The container is only one layer. The process that starts containers is in the
`docker` group, which is root-equivalent, so what reaches it is controlled too:

- **Only the API runs code on candidates' behalf, via the queue.** The API has no
  Docker access; the worker (`hfjudge`) does. The worker runs only the judge's own
  entry points (`src/judge/rq_safety.py`, `ALLOWED_JOBS`), and jobs and results cross
  Redis as JSON — stock RQ would run any function a job names and unpickle its
  arguments. Redis itself needs a password (CPANEL_DEPLOYMENT.md, Step 2.4).
- **The worker holds only the secrets it uses** (database, Redis, Gemini).
  `entrypoint.sh` unsets the rest; it cannot re-read `.env` (0600, API user).
- **The worker unit** (`hireflow-judge-worker.service`) mounts everything read-only
  except `/srv/judge_work` (itself 0700), and sets `NoNewPrivileges`, `PrivateDevices`
  and kernel protections. These do not apply to the containers, which dockerd starts.
- **SQL** runs in a fresh in-memory SQLite database per case (`sql_harness.py`). The
  graded query is read-only (`PRAGMA query_only` plus an authorizer): no writes,
  ATTACH, PRAGMA or extensions, with a 1 s limit per query and a 10,000-row limit.
  Test-case generation, which runs LLM-written seed SQL in the API process, uses the
  same harness.
- **Limits per user:** Run Code 20/minute, graded runs 10/minute and 3 per question
  per test; source up to 64 KB, custom input up to 1 MB.

### Hidden test cases and answer keys

Candidates never receive `correct_answer`, `sample_answer` (the reference solution) or
hidden test cases:

- The question-generator routes (`/interviews/*`) are staff-only and company-scoped.
- A candidate's paper (`/api/v1/mcq/interviews/{id}/questions`) is their own
  interview only, approved questions only, with `correct_answer` blank and sample test
  cases only.
- `/judge/grade-question` and `/judge/run` accept a question only from the
  candidate's own, unfinished test. Hidden-case output is never returned to them.
- `/mcq/questions*`, `/mcq/sample` and `/evaluation/*/submit` are staff-only.

`test/test_question_access.py` and `test/test_judge_access.py` pin these.

---

## Running it

### Prerequisites
- Build the language images (Docker running):
  ```powershell
  ./scripts/build_judge_images.ps1      # Windows
  ./scripts/build_judge_images.sh       # Linux/mac
  ```

### Local dev (simplest — no Redis/worker)
Start Docker Desktop, then run the API with inline judging:
```powershell
$env:JUDGE_SYNC = "1"
python -m uvicorn main:app --port 8000
# frontend: cd frontend; npm run dev   → http://localhost:3000
```
Submissions are judged inline in the API process (which has DB + Docker access).
Verdicts return on the same request and stream over the WebSocket.

> Memory reporting: set `JUDGE_GNU_TIME=1` (the built images include GNU `time`) to
> populate per-case peak memory. MLE detection works regardless (via the OOM cap).

### With the async queue (Linux / production)
```bash
docker compose -f docker-compose.judge.yml up -d --build          # redis + worker
docker compose -f docker-compose.judge.yml up -d --scale judge-worker=4   # scale out
```
Run the API **without** `JUDGE_SYNC` so it enqueues to Redis. See the header of
`docker-compose.judge.yml` for the docker-out-of-docker work-dir path requirement.

---

## API reference (prefix `/api/v1/judge`, all JWT-authed)

| Method | Path | Role | Purpose |
|---|---|---|---|
| GET | `/languages` | any | language picker catalog |
| POST | `/problems` | admin/hr | create a problem (+ test cases) |
| GET | `/problems` | any | list (candidates see published only) |
| GET | `/problems/{id}` | any | detail (+ sample cases; admin sees hidden) |
| PUT | `/problems/{id}` | admin/hr | update |
| POST | `/problems/{id}/testcases` | admin/hr | add test cases |
| GET | `/problems/{id}/testcases` | admin/hr | all cases incl. hidden |
| POST | `/submissions` | any | submit code → queued |
| GET | `/submissions/{id}` | owner/admin | status + per-case results |
| GET | `/submissions` | any | history (own; admin sees all) |
| WS | `/ws/{id}?token=…` | owner/admin | live status stream |

Frontend pages live under `/coding` (problem list), `/coding/problems/:id` (solve),
`/coding/submissions`, `/coding/admin` (create). Linked from the sidebar for all roles.

---

## Verification

**Unit (no Docker):** `pytest test/test_judge_verdict.py` — normalization, matching,
verdict aggregation, language registry. (14 tests.)

**Integration (Docker + images required):** `pytest test/test_judge_runner_integration.py`
— auto-skips without Docker. Covers:
- correct solution → **AC**; wrong → **WA**; infinite loop → **TLE**;
  huge allocation → **MLE**; `SystemExit(1)` → **RE**; bad C++ → **CE**;
- **security probe**: outbound network call is blocked (`--network none`).

**Manual end-to-end:** start Docker + images, run API (`JUDGE_SYNC=1`) + frontend, log
in, open **Coding Problems**, create a "sum two numbers" problem (sample + hidden
cases), then submit `a,b=map(int,input().split()); print(a+b)` → **ACCEPTED** live.

---

## Roadmap (mapping the rest of the original request)

- **Phase 2 — Contests & competition:** `contests`/`contest_problems`/`participants`,
  real-time leaderboard (Redis sorted sets), Java + Go images, multi-language
  templates, editorials, submission-history polish.
- **Phase 3 — Integrity:** wire HireFlow's existing **proctoring** (webcam/YOLO/
  tab-switch) into assessments; **plagiarism detection** (token-fingerprint/MOSS +
  structural similarity → `plagiarism_reports`); AI-generated-code detection; forum.
- **Phase 4 — Enterprise scale & ops:** K8s manifests + **worker autoscaling (HPA)**,
  WebSocket scaling via Redis pub/sub, **retry + dead-letter queue**, Nginx, Prometheus
  + Grafana, CI/CD, S3/MinIO for large test data, CDN, fault tolerance.
- **Phase 5 — Advanced:** SQL execution env, ML-notebook execution, adaptive
  difficulty, resume-based recommendation (reuse `enhanced_ats_service.py`), AI
  interview assistant, real-time code playback.
