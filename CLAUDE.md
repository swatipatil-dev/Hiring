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

# Backend: setup database (from backend/)
python setup_db.py

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

- **ATS Scoring** (`src/services/enhanced_ats_service.py`): 4-factor algorithm — skills match (35%), semantic/TF-IDF cosine similarity (35%), experience (20%), keywords (10%)
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

**Deployment**: `docs/CPANEL_DEPLOYMENT.md` is the step-by-step runbook and `docs/DEPLOYMENT_NOTES.md` is the reference for *why* — non-obvious constraints, silent failure modes, and which config file wins. Read the latter before changing deployment behaviour. Service units and Apache config live in `deploy/cpanel/`.

Two constraints worth knowing before changing anything there:

- **Proctoring must run as a single worker.** `proctoring/routes.py` keeps sessions in a process-local dict of live MediaPipe handles, so a session's WebSocket must reach the process that created it. It runs as its own service for this reason.
- **Proctoring models are shared, not per-session.** `proctoring/model_registry.py` holds a bounded pool of model sets borrowed per call; analyzers hold only session state. Memory scales with CPU, not candidate count. Anything added to an analyzer's `__init__` should be state, not a model.
