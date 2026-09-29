# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HireFlow is an AI-powered recruitment system built with FastAPI and MySQL. It implements a 12-phase hiring workflow with ATS (Applicant Tracking System) resume scoring, role-based access control (Admin, HR, Interviewer, Candidate), and AI-powered question generation via the Google Gemini API.

## Repository Layout

The repo is split into two top-level apps:
- `backend/` — the FastAPI/Python application (all code, tests, migrations, scripts, assets, and configs below live here).
- `frontend/` — the Vite/React UI.

All backend commands below must be run from the `backend/` directory.

## Commands

```bash
# Backend: run dev server (from backend/)
cd backend
python -m uvicorn main:app --reload --port 8000

# Backend: run dev server (alternative, uses port 8001)
python main.py

# Backend: run tests (from backend/)
pytest
pytest --cov=.
pytest test/test_ats_scoring.py  # single test file

# Backend: setup database (from backend/) — creates missing tables, destroys nothing
python setup_db.py

# Backend: rebuild from scratch (DEV/TEST ONLY — drops interviews, feedback, candidates)
python setup_db.py --drop

# Backend: install dependencies (from backend/)
pip install -r requirements.txt

# Frontend: run dev server (from frontend/)
cd frontend
npm install
npm run dev
```

## Architecture

**Entry point**: `backend/main.py` — FastAPI app that registers all routers and initializes the database on startup via lifespan handler.

**Database**: MySQL via SQLAlchemy ORM. `src/config/database_config_mysql.py` reads `DATABASE_URL` from the environment, falling back to a local `root@localhost/recruitment_db` default for dev. `DatabaseManager` handles pooling, sessions, and health checks. Get a session via `get_db()` dependency. Schema names are never hardcoded — queries use MySQL's `DATABASE()` — because managed hosts impose their own naming (cPanel prefixes every database with the account name).

**ORM models**: All in `src/models/models.py` using `declarative_base()`. UUIDs stored as `String(36)`. Key models: User, Department, JobPosting, Candidate, ATSResult, Interview, InterviewFeedback, Offer, CandidateStageHistory.

**Tenancy** (full model in `docs/TENANT_ISOLATION.md`): every company's data carries `company_id`, and a person has a separate `User` per company (no shared cross-company profile). Routes filter by `current_user.company_id` explicitly; as a safety net, `get_current_user` binds the company and `src/tenancy/events.py` adds it to every ORM query on `TenantScoped` models. `get_current_user` must stay `async def` — a sync dependency's contextvar never reaches the endpoint. Raw SQL, background workers and superadmin requests are not auto-filtered. Never add an `OR company_id IS NULL` branch; backfill instead (`scripts/backfill_orphan_company_ids.py`).

**Authentication**: `src/auth.py` — JWT tokens via python-jose, bcrypt password hashing, `HTTPBearer` security scheme. Role-based access via dependency functions.

### Route Organization

Routes are in the `routes/` package, registered in `main.py` via `include_router`. Each file maps to a role or feature:

| File | Prefix | Purpose |
|------|--------|---------|
| `routes/auth_routes.py` | `/api/v1/auth` | Registration, login, logout |
| `routes/candidate_routes.py` | `/api/v1/candidate` | Job browsing, applications |
| `routes/hr_routes.py` | `/api/v1/hr` | Job management, interviews, offers |
| `routes/interviewer_routes.py` | `/api/v1/interviewer` | Interview management, feedback |
| `routes/enhanced_interviewer_routes.py` | `/api/v1/interviewer/enhanced` | Extended interviewer features |
| `routes/admin_routes.py` | `/api/v1/admin` | User/department management, analytics |
| `routes/ats_routes.py` | `/api/v1/ats` | ATS scoring endpoints |
| `routes/database_management_routes.py` | `/api/v1/db` | Database admin operations |
| `routes/evaluation_routes.py` | (evaluation) | Evaluation & workflow |
| `routes/advanced_features_routes.py` | (advanced) | Advanced features |

Three top-level routers are also registered directly in `main.py`:
- `generator_router.py` (`/interviews`) — AI question generation using Gemini
- `mcq_api.py` (`/api/v1/mcq`) — MCQ assessment endpoints
- `ats_manager.py` — ATS scoring logic (not a router; business logic)

### AI/ML Components

- **ATS Scoring** (`src/services/enhanced_ats_service.py` + `src/services/ats_evidence.py`, always run through `src/services/ats_scoring_pipeline.py`): 7-factor recruiter rubric — education 35%, skills 25%, experience 15%, certifications 10%, semantic, project and keyword 5% each. Skills are credited by evidence found in the full resume text (shown in an experience/project entry > listed only > related skill > not found) and every finding carries a resume excerpt; unknown education/experience parts are excluded and the rest re-weighted, never scored as 0. Each result records its rules version (`SCORING_RULES_VERSION` — bump it for any change that moves scores), job-description version (`job_description_versions`), model versions and scoring time. A resume that cannot be read reliably is `needs_review`: held out of every automatic shortlist and reject. HR overrides need a reason and are kept in `ats_score_events`. Resume-match scores are never combined with test or interview scores.
- **Assessment fairness**: proctoring ends a test only on a second face, a phone, or a sustained identity mismatch; everything else is recorded for human review (`proctoring_reviews`), never an automatic outcome. Candidates can explain a test, request adjustments, and HR can grant retests (`src/services/assessment_fairness.py`). The monitoring disclosure in `TestInstructions.jsx` must match what `proctoring/routes.py` actually does.
- **Coding judge** (`src/judge/`, `routes/judge_routes.py`; security model in `docs/CODING_JUDGE.md`): candidate code runs only in the judge worker, one throwaway Docker container per compile/test case. The worker runs only the jobs in `src/judge/rq_safety.py` (`ALLOWED_JOBS`), as JSON — add new job functions there. SQL is graded read-only in `src/judge/sql_harness.py`, the single implementation also used in-process. Candidates never receive `correct_answer`, `sample_answer` or hidden test cases: the `/interviews/*` generator routes are staff-only, and candidate-facing routes check ownership through `src/services/interview_access.py`.
- **Question Generation** (`src/prompt_builder.py` + `src/services/gemini_service.py`): Uses the Gemini API to generate role-specific interview questions
- **Resume Parsing**: PDF extraction via pdfplumber

### Key Patterns

- All IDs are UUID strings (`String(36)`), generated via `uuid.uuid4()`
- Pydantic models for request/response validation (defined alongside route files and in `src/models/`)
- Database sessions managed via FastAPI `Depends(get_db)` pattern
- Candidate stage progression is tracked in `candidate_stage_history` table (immutable audit trail)
- Resume files stored in `resumes/` directory

## Environment

Copy `.env.example` to `.env` for local development, or `.env.cpanel.example` when deploying. Key variables: `DATABASE_URL`, `SECRET_KEY`, `GEMINI_API_KEY`, `GEMINI_MODEL`.

`SECRET_KEY` is required — `src/auth.py` raises at import if it is unset or still a placeholder, rather than signing JWTs with a publicly known key.

`GEMINI_API_KEY` selects the Gemini Developer API. Setting `GOOGLE_CLOUD_PROJECT` instead selects Vertex AI via Application Default Credentials; the API key wins when both are present.

Uploaded files resolve against `HIREFLOW_DATA_DIR` (default: the `backend/` directory), **not** the process working directory — systemd, cron and an interactive shell all start with different CWDs.

## Documentation

Additional docs in `docs/`: API documentation, microservice architecture guide, MySQL setup, quickstart guide, and workflow docs.

**Deployment**: `docs/END_TO_END_RUNBOOK.md` is the spine — local setup through to a production deploy, with every command labelled by which machine it runs on; start there and follow its links for depth. `docs/CPANEL_DEPLOYMENT.md` is the first-install runbook, `docs/DEPLOY_UPDATE.md` is what to run when pushing an update to a live deployment (read it before deploying — `git pull` leaves the schema, systemd units, Apache config and built frontend stale, and each has caused an outage), `docs/GCP_DATA_MIGRATION.md` covers moving data between deployments, `docs/BACKUP_AND_RESTORE.md` covers the nightly encrypted backup (`scripts/backup.py`) and the monthly restore drill (`scripts/restore_drill.py`), and `docs/DEPLOYMENT_NOTES.md` is the reference for *why* — non-obvious constraints, silent failure modes, and which config file wins. Read the latter before changing deployment behaviour. Service units and Apache config live in `deploy/cpanel/`.

Two constraints worth knowing before changing anything there:

- **Proctoring must run as a single worker.** `proctoring/routes.py` keeps sessions in a process-local dict of live MediaPipe handles, so a session's WebSocket must reach the process that created it. It runs as its own service for this reason.
- **Proctoring models are shared, not per-session.** `proctoring/model_registry.py` holds a bounded pool of model sets borrowed per call; analyzers hold only session state. Memory scales with CPU, not candidate count. Anything added to an analyzer's `__init__` should be state, not a model.
