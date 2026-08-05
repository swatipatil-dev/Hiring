# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

HireFlow is an AI-powered recruitment system built with FastAPI and MySQL. It implements a 12-phase hiring workflow with ATS (Applicant Tracking System) resume scoring, role-based access control (Admin, HR, Interviewer, Candidate), and AI-powered question generation via Ollama.

## Commands

```bash
# Run dev server
python -m uvicorn main:app --reload --port 8000

# Run dev server (alternative, uses port 8001)
python main.py

# Run tests
pytest
pytest --cov=.
pytest test/test_ats_scoring.py  # single test file

# Setup database
python setup_db.py

# Install dependencies
pip install -r requirements.txt
```

## Architecture

**Entry point**: `main.py` — FastAPI app that registers all routers and initializes the database on startup via lifespan handler.

**Database**: MySQL via SQLAlchemy ORM. Connection config is hardcoded in `src/config/database_config_mysql.py` (mysql+pymysql, root:1234@localhost:3306/recruitment_db). `DatabaseManager` class handles pooling, sessions, and health checks. Get a session via `get_db()` dependency.

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
- `generator_router.py` (`/interviews`) — AI question generation using Ollama
- `mcq_api.py` (`/api/v1/mcq`) — MCQ assessment endpoints
- `ats_manager.py` — ATS scoring logic (not a router; business logic)

### AI/ML Components

- **ATS Scoring** (`src/services/enhanced_ats_service.py`): 4-factor algorithm — skills match (35%), semantic/TF-IDF cosine similarity (35%), experience (20%), keywords (10%)
- **Question Generation** (`src/prompt_builder.py` + `src/services/ollama_service.py`): Uses Ollama LLM to generate role-specific interview questions
- **Resume Parsing**: PDF extraction via pdfplumber

### Key Patterns

- All IDs are UUID strings (`String(36)`), generated via `uuid.uuid4()`
- Pydantic models for request/response validation (defined alongside route files and in `src/models/`)
- Database sessions managed via FastAPI `Depends(get_db)` pattern
- Candidate stage progression is tracked in `candidate_stage_history` table (immutable audit trail)
- Resume files stored in `resumes/` directory

## Environment

Copy `.env.example` to `.env`. Key variables: `DATABASE_URL`, `SECRET_KEY`, `OPENAI_API_KEY`, `OLLAMA_BASE_URL`. The database config currently uses a hardcoded MySQL URL in `database_config_mysql.py` rather than reading from env.

## Documentation

Additional docs in `docs/` directory: API documentation, microservice architecture guide, MySQL setup, quickstart guide, and workflow docs.
