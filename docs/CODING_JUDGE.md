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
| Sandbox images | `judge/images/Dockerfile.{python,cpp,node}`, `judge/worker.Dockerfile` |
| Infra | `docker-compose.judge.yml`, `scripts/build_judge_images.{ps1,sh}` |
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
- `--rm` cleanup + a kill-by-name reaper for timed-out containers; output size-capped.

**Production hardening (Linux):** set `JUDGE_RUNTIME=runsc` (gVisor) and
`JUDGE_SECCOMP=/path/profile.json` for kernel-level isolation of untrusted code.

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
