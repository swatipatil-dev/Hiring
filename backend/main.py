"""
🚀 HIREFLOW RECRUITMENT SYSTEM - MAIN APPLICATION
Microservice-based FastAPI application with modular architecture
Updated: email fallback in candidate endpoints + SCHEDULED invites in test results + pending badge

Features:
✅ AI/ML ATS Resume Scoring (7-factor algorithm)
✅ Real-time Candidate Ranking
✅ Complete 12-Phase Hiring Workflow
✅ Role-Based Access Control (Admin, HR, Interviewer, Candidate)
✅ Resume PDF Parsing & Skill Extraction
✅ Semantic Matching with Embeddings
✅ 50+ API Endpoints
✅ Production-Ready with Security

Architecture:
- Modular design with separate services
- Authentication & authorization service
- AI/ML ATS scoring service
- Route modules by user role
- Clean separation of concerns

Author: AI Assistant
Version: 2.0.0 (Microservice Architecture)
"""

import json
import os
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

# Load environment variables from .env (if present) before importing modules
# that read os.getenv() at import time (e.g., src/services/email_service.py).
from src.env_boot import load_env
load_env()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from src.rate_limit import limiter
from src.middleware import TenantDomainMiddleware

# Core Services
from routes import (
    auth_router,
    candidate_router,
    candidate_profile_router,
    hr_router,
    interviewer_router,
    enhanced_interviewer_router,
    admin_router,
    ats_router,
    database_management_router,
    advanced_router,
    ats_explain_router,
)

# Enhanced Services
from generator_router import router as generator_router
from mcq_api import router as mcq_router
from routes.evaluation_routes import router as evaluation_router
from routes.superadmin_routes import router as superadmin_router
from routes.public_routes import router as public_router
from routes.support_routes import router as support_router
from routes.judge_routes import router as judge_router
from routes.recruiter_workflow_routes import router as recruiter_workflow_router
from routes.scorecard_routes import router as scorecard_router
try:
    from proctoring.routes import router as proctor_router
except ImportError:
    proctor_router = None

# Database
from src.config.database_config_mysql import init_db

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Application lifecycle management
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown"""
    # Startup
    logger.info("🚀 Starting HireFlow Recruitment System...")
    
    # Initialize database
    if init_db():
        logger.info("✅ Database initialized successfully!")
    else:
        logger.error("❌ Database initialization failed!")

    # Optionally seed a superadmin and/or the platform org's admin from env vars.
    try:
        from src.bootstrap import bootstrap_superadmin, bootstrap_platform_admin
        bootstrap_superadmin()
        bootstrap_platform_admin()
    except Exception as _e:
        logger.error(f"Bootstrap error: {_e}")

    # NLP model availability check
    try:
        from src.services.enhanced_ats_service import (
            semantic_model_available,
            RAPIDFUZZ_AVAILABLE,
        )
        # Loads the model here, per worker, rather than on the first resume scored.
        if semantic_model_available():
            logger.info("NLP: sentence-transformers loaded (semantic scoring: all-MiniLM-L6-v2)")
        else:
            logger.warning("NLP: sentence-transformers NOT available — semantic scoring uses TF-IDF fallback")

        if RAPIDFUZZ_AVAILABLE:
            logger.info("NLP: rapidfuzz loaded (fuzzy keyword matching active)")
        else:
            logger.warning("NLP: rapidfuzz NOT available — keyword matching uses exact overlap only")

        try:
            import spacy  # noqa: F401
            logger.info("NLP: spaCy package present — en_core_web_trf will load on first NER call")
        except ImportError:
            logger.warning("NLP: spaCy NOT installed — NER-based skill extraction disabled")
    except Exception as _e:
        logger.error(f"NLP startup check failed: {_e}")

    # Proctoring warm-up. Building a pipeline costs ~107s on a cold worker (mediapipe and
    # ultralytics imports dominate) and used to happen inline in POST /proctor/sessions/start,
    # on the event loop — so the first candidate to start a test froze the server and their
    # face-match readout only appeared after a page reload.
    #
    # Deliberately NOT awaited: app boot and the Cloud Run readiness probe must not wait on
    # it. The first few seconds of uptime simply run with a cold pool.
    _proctor_tasks = []
    if os.getenv("PROCTOR_PREWARM", "1").lower() in ("1", "true", "yes"):
        try:
            from proctoring.warmup import warm_imports, ensure_pool, reap_idle_sessions

            def _warm():
                warm_imports()
                ensure_pool()

            _proctor_tasks.append(asyncio.create_task(asyncio.to_thread(_warm)))
            _proctor_tasks.append(asyncio.create_task(reap_idle_sessions()))
            logger.info("Proctoring pre-warm started in background")
        except Exception as _e:
            logger.error(f"Proctoring pre-warm could not start: {_e}")

    yield

    # Shutdown
    for _t in _proctor_tasks:
        _t.cancel()
    try:
        from proctoring.warmup import shutdown_pool
        shutdown_pool()
    except Exception:
        pass
    try:
        from proctoring.runtime import shutdown_runtime
        shutdown_runtime()
    except Exception:
        pass
    # One line naming the full picture at every boot. This is the cheapest of the
    # three tracking mechanisms and the only one that survives the manifest being
    # lost: `journalctl -u hireflow-api | grep "| release "` is a dated history of
    # everything this box has ever run.
    _rel = _release_summary()
    logger.info(
        "HireFlow %s | release %s | commit %s | frontend %s | deployed %s by %s",
        _rel["app_version"],
        _rel["release"] or "not recorded",
        (_rel["commit"] or "unknown")[:12],
        _rel["frontend_version"] or "not recorded",
        _rel["deployed_at"] or "unknown",
        _rel["deployed_by"] or "unknown",
    )
    if _rel["frontend_matches"] is False:
        logger.warning(
            "Frontend bundle is %s but the backend is %s — the browser is being served "
            "a different release from the API.",
            _rel["frontend_version"], _rel["app_version"],
        )
    logger.info("🎉 HireFlow Recruitment System started successfully!")

# Rate limiter is defined in src.rate_limit (proxy-aware client-IP key) and
# imported above, so this module and the auth routes share one instance.


# The VERSION file at the repo root is the single source of truth for the release
# number. It is written by .github/workflows/release.yml and committed alongside the
# tag, so a checkout of v2.1.0 carries "2.1.0" here — which is what lets /health
# answer "exactly which version is running in production?".
#
# Resolved from __file__, never the CWD: systemd, cron and an interactive shell all
# start somewhere different, and a bare relative path would read nothing under two of
# the three.
#
# Never raises. A missing or unreadable VERSION means we do not know the version, not
# that the service should refuse to boot.
def _read_version() -> str:
    """Release number from the repo-root VERSION file, or 'unknown'."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "VERSION")
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or "unknown"
    except OSError:
        return "unknown"


APP_VERSION = _read_version()


# What the pipeline actually deployed, as opposed to what this checkout claims to be.
#
# VERSION answers "what does this code say it is". It cannot answer "when did this get
# here, who put it here, and is the frontend being served the same release" — the
# deploy publishes dist/ only when frontend/ changed, and a rollback restores an older
# bundle, so the two genuinely can diverge.
#
# .release.json is written by .github/workflows/deploy.yml at the end of a successful
# deploy and rewritten by its rollback, so it never advertises a release that was
# undone. Untracked, and absent on any developer machine — which is why "not recorded"
# is the ordinary case here, not an error.
#
# Same rules as _read_version above: resolved from __file__ rather than the CWD, and
# never raises. A corrupt manifest is treated exactly like a missing one; a malformed
# JSON file is not a reason to refuse to serve traffic.
def _read_release() -> dict:
    """The deploy manifest, or {} when nothing has been recorded."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".release.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    # A JSON file whose top level is a list or a string parses fine and would then
    # break every caller that does .get(). Only an object is a manifest.
    return data if isinstance(data, dict) else {}


def _release_summary() -> dict:
    """Version facts for /health and /api/v1/version, including frontend skew.

    `frontend_version` is the bundle the deploy published, which after a rollback can
    sit behind the backend. Reporting the mismatch here is the only automatic check in
    the system for the stale-copy failure docs/DEPLOY_UPDATE.md is built around — it
    otherwise shows up as a user seeing an old screen against a new API.
    """
    release = _read_release()
    frontend = release.get("frontend_version")
    return {
        "app_version": APP_VERSION,
        "release": release.get("version"),
        "commit": release.get("commit"),
        "deployed_at": release.get("deployed_at"),
        "deployed_by": release.get("deployed_by"),
        "state": release.get("state"),
        "frontend_version": frontend,
        # None rather than False when there is no manifest: "we did not check" and
        # "we checked and they match" are different answers.
        "frontend_matches": None if not frontend else frontend == APP_VERSION,
        "recorded": bool(release),
    }


# Create FastAPI application
app = FastAPI(
    title="HireFlow Recruitment System",
    description="Complete AI-powered recruitment system with ATS resume scoring - Microservice Architecture",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Attach rate limiter state and 429 handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Tenant resolution — resolves Host header to Company before route handlers run
app.add_middleware(TenantDomainMiddleware)

# CORS Middleware
# Public routes (/api/v1/public/*) must be reachable from any origin for iframe embedding.
# We use allow_origins=["*"] with allow_credentials=False — Bearer tokens (Authorization header)
# are unaffected by credentials mode; only cookies require credentials=True.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global exception handler — never expose raw exception details to clients
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Something went wrong"}
    )

# ==================== ROOT ENDPOINTS ====================

@app.get("/")
async def root():
    """Root endpoint - API Information"""
    return {
        "message": "HireFlow Recruitment System API",
        "version": APP_VERSION,
        "architecture": "Microservice-based",
        "docs": "/docs",
        "health": "/health",
        "status": "running"
    }

def _judge_queue_auth() -> bool:
    try:
        from src.judge.queue import redis_url_has_password
        return redis_url_has_password()
    except Exception:
        return False


@app.get("/health")
async def health_check():
    """Health check — returns 503 when the database is unreachable."""
    try:
        from src.config.database_config_mysql import db_manager
        db_status = db_manager.check_connection()
        is_healthy = db_status.get("status") == "healthy"
        return JSONResponse(
            status_code=200 if is_healthy else 503,
            content={
                "status": db_status.get("status", "unhealthy"),
                "database": db_status.get("database", "recruitment_db"),
                "user": db_status.get("user", ""),
                # "version" here is MySQL's, and has been since this endpoint was
                # written — renaming it would break whatever reads it. The release
                # number gets its own key rather than quietly taking that one over.
                "version": db_status.get("version", "MySQL"),
                "app_version": APP_VERSION,
                # What the pipeline recorded as deployed. Additive: the deploy's own
                # health check greps for "status":"healthy", so extra keys are safe.
                "release": _release_summary(),
                # False means the judge's Redis has no password: on a shared server
                # any local account could then queue work for the judge worker.
                "judge_queue_auth": _judge_queue_auth(),
                "timestamp": db_status.get("timestamp", datetime.now().isoformat()),
            }
        )
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                # Reported even when the database is down: knowing which release is
                # failing is most of the value of asking.
                "app_version": APP_VERSION,
                "release": _release_summary(),
                "timestamp": datetime.now().isoformat(),
            }
        )

@app.get("/api/v1/version")
async def version_info():
    """Everything known about what is running here.

    One call for the support question "which version are you on?". Deliberately
    unauthenticated and free of anything sensitive — the release number, its commit
    and when it shipped are all public facts about a public site, and requiring a
    login to read them would defeat the point when the complaint is that login is
    broken.

    `release` is null on any machine the pipeline has not deployed to, which includes
    every developer laptop. That is a normal answer, not an error.
    """
    return {
        **_release_summary(),
        # The raw manifest as written, so a future field added by the pipeline is
        # readable without another backend change.
        "manifest": _read_release() or None,
    }


@app.get("/api/v1/info")
async def api_info():
    """API information and available endpoints"""
    return {
        "name": "HireFlow Recruitment API",
        "version": APP_VERSION,
        "architecture": "Microservice-based",
        "modules": {
            "authentication": "JWT-based auth with role management",
            "database": "MySQL with 17 tables",
            "ats_scoring": "AI/ML 4-factor resume scoring",
            "routes": "Modular route handlers by role"
        },
        "endpoints": {
            "auth": "/api/v1/auth/*",
            "candidates": "/api/v1/candidate/*",
            "hr": "/api/v1/hr/*",
            "interviewers": "/api/v1/interviewer/*",
            "enhanced_interviewer": "/api/v1/interviewer/enhanced/*",
            "admin": "/api/v1/admin/*",
            "ats": "/api/v1/ats/*",
            "mcq": "/api/v1/mcq/*",
            "question_generator": "/api/v1/interviews/*",
            "database_management": "/api/v1/db/*"
        },
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc"
        }
    }

# ==================== ROUTE REGISTRATION ====================

# Register all route modules
app.include_router(auth_router, tags=["Authentication"])
app.include_router(candidate_profile_router, tags=["Candidate Profile"])
app.include_router(candidate_router, tags=["Candidates"])
app.include_router(hr_router, tags=["HR"])
app.include_router(interviewer_router, tags=["Interviewers"])
app.include_router(enhanced_interviewer_router, tags=["Enhanced Interviewer"])
app.include_router(admin_router, tags=["Admin"])
app.include_router(ats_router, tags=["ATS"])
app.include_router(generator_router, tags=["Question Generator"])
app.include_router(mcq_router, prefix="/api/v1", tags=["MCQ Questions"])
app.include_router(database_management_router, tags=["Database Management"])
app.include_router(evaluation_router, tags=["Evaluation & Workflow"])
app.include_router(advanced_router, tags=["Advanced Features"])
app.include_router(ats_explain_router, tags=["ATS Explainability"])
if proctor_router:
    app.include_router(proctor_router, tags=["AI Proctoring"])
app.include_router(superadmin_router, tags=["Super Admin"])
app.include_router(public_router, tags=["Public"])
app.include_router(support_router, tags=["Support"])
app.include_router(judge_router, tags=["Coding Judge"])
app.include_router(recruiter_workflow_router, tags=["Recruiter Workflow"])
app.include_router(scorecard_router, tags=["Scorecards"])

# Uploaded files.
#
# Company logos are public (they appear on public career pages) and are served at
# /logos. Everything else a person uploads — resumes, cover letters, photos,
# certificates, voice answers — is private and reachable ONLY through the
# authenticated, row-keyed routes in routes/file_routes.py. Never mount /uploads,
# /resumes or /cover_letters here: they used to be static mounts, and anyone holding
# a path could download a candidate's resume.
#   local storage  → mount the logos directory directly (dev default)
#   gcs storage    → stream logo objects from the bucket (container FS is ephemeral)
from src.services.storage import storage as _file_storage, DATA_DIR as _DATA_DIR
from routes.file_routes import router as file_router

app.include_router(file_router, tags=["Files"])

_PUBLIC_FILE_PREFIXES = ["logos"]

if getattr(_file_storage, "backend", "local") == "gcs":
    import mimetypes
    from fastapi.responses import Response as _BytesResponse

    def _register_file_route(prefix: str):
        @app.get(f"/{prefix}/{{path:path}}", name=f"serve-{prefix}")
        async def _serve_file(path: str, _prefix: str = prefix):
            key = f"{_prefix}/{path}"
            try:
                data = _file_storage.read_bytes(key)
            except Exception:
                raise HTTPException(status_code=404, detail="File not found")
            ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
            return _BytesResponse(content=data, media_type=ctype,
                                  headers={"X-Content-Type-Options": "nosniff"})

    for _p in _PUBLIC_FILE_PREFIXES:
        _register_file_route(_p)
else:
    for _p in _PUBLIC_FILE_PREFIXES:
        _dir = os.path.join(_DATA_DIR, _p)
        os.makedirs(_dir, exist_ok=True)
        app.mount(f"/{_p}", StaticFiles(directory=_dir), name=_p)

# The standalone proctoring demo page (proctoring/static/proctor.html) is no longer
# served: it started anonymous sessions, which proctoring now refuses, and the real
# UI is the candidate test page in the frontend.

# ==================== APPLICATION READY ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8001)))
