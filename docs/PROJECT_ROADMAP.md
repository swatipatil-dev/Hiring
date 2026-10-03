# HireFlow — Project Roadmap & Completion Plan

**Product:** HireFlow AI-Powered Recruitment System
**Goal:** Production-ready SaaS platform with client onboarding
**Last Updated:** April 2026

---

## Table of Contents

1. [Current Problems](#1-current-problems)
2. [Improvements Needed](#2-improvements-needed)
3. [Complete Project Plan — Phase by Phase](#3-complete-project-plan)
4. [Client Onboarding Plan](#4-client-onboarding-plan)

---

## 1. Current Problems

### 1.1 Security Problems

| # | Problem | File / Location | Risk |
|---|---------|----------------|------|
| S1 | Gmail SMTP password hardcoded as default | `src/services/email_service.py` lines 19–20 | Credential leak |
| S2 | MySQL root password hardcoded (`1234`) in 6 files | `src/config/database_config_mysql.py`, `fix_db.py`, `setup_db.py`, `create_test_data.py`, `migrate_department_assignment.py`, `create_departments.py` | DB exposed |
| S3 | JWT secret key has weak public fallback | `src/auth.py` line 19 | All tokens forgeable |
| S4 | JWT tokens cannot be revoked — logout is client-side only | `src/auth.py` lines 70–72 | Compromised accounts stay active |
| S5 | Auth token stored in `localStorage` (XSS vulnerable) | `frontend/src/api/client.js`, `AuthContext.jsx` | Token theft via XSS |
| S6 | CORS allows all origins (`"*"`) | `main.py` line 127 | Any website can call the API |
| S7 | Error responses expose raw exception text to clients | `main.py` lines 134–140 | Leaks SQL errors, file paths |
| S8 | Resume files served with no access control | `routes/candidate_routes.py` | Anyone can download any resume |
| S9 | No file upload validation (type, size, MIME) | `routes/candidate_routes.py` lines 159–174 | Malware upload possible |

### 1.2 Backend Development Problems

| # | Problem | File / Location | Impact |
|---|---------|----------------|--------|
| B1 | No database migrations (Alembic installed but unused) | `requirements.txt` — no `alembic/` folder | Cannot update schema in production safely |
| B2 | Missing indexes on ALL foreign key columns | `src/models/models.py` — all FK columns | Slow queries as data grows |
| B3 | N+1 query loops in admin and HR routes | `routes/admin_routes.py` lines 294–309, `routes/hr_routes.py` | Database overload at scale |
| B4 | Inconsistent API response formats across 50+ endpoints | All routes | Frontend breaks unpredictably |
| B5 | No pagination on most list endpoints | `routes/admin_routes.py`, `routes/hr_routes.py` | Server crash when data grows large |
| B6 | Dramatiq installed but zero background workers exist | `requirements.txt` | Emails block HTTP responses |
| B7 | Ollama AI runs on localhost only — no cloud fallback | `src/services/ollama_service.py` line 20 | AI features break on any cloud server |
| B8 | ML models loaded per worker at startup (~2 GB each) | `main.py` lines 86–107 | Out-of-memory on cloud instances |
| B9 | Minimal test coverage (only ATS scoring tested) | `test/` folder | Bugs ship silently to production |
| B10 | No password reset / forgot password flow | `routes/auth_routes.py` | Users permanently locked out |
| B11 | No email verification on registration | `routes/auth_routes.py` | Fake accounts, spam abuse |

### 1.3 Frontend Development Problems

| # | Problem | File / Location | Impact |
|---|---------|----------------|--------|
| F1 | Hardcoded `localhost:8000` URLs in frontend | `HRDashboard.jsx` lines 456, 471, 1198; `InterviewerDashboard.jsx` line 1255 | Breaks in any non-local environment |
| F2 | No `.env` files — zero environment variable usage | All frontend source files | Cannot configure API URL without rebuilding |
| F3 | 31+ silent `.catch(() => {})` — errors swallowed | `AdminDashboard.jsx`, `HRDashboard.jsx`, `InterviewerDashboard.jsx`, `OnlineTest.jsx` | UI freezes with no user feedback |
| F4 | No React error boundaries | Entire frontend | One JS error crashes the whole app |
| F5 | `alert()` / `prompt()` / `confirm()` used as UI (20+ places) | All dashboard files | Poor UX, cannot style or customize |
| F6 | No toast / notification system | All dashboard files | Users don't know if actions succeeded |
| F7 | Missing loading states on 7+ API calls | `AdminDashboard.jsx`, `CandidateDashboard.jsx` | UI appears frozen while loading |
| F8 | No input validation on most forms | Login, Register, job creation, user creation forms | Bad data reaches the backend |
| F9 | Test proctoring is client-side only | `OnlineTest.jsx` | Candidates can cheat easily |

### 1.4 Hosting & Deployment Problems

| # | Problem | Impact |
|---|---------|--------|
| H1 | No `Dockerfile`, no `docker-compose.yml`, no cloud config files | Cannot deploy to any cloud platform |
| H2 | Database host / user / name all hardcoded to `localhost` | Fails on cloud databases (RDS, PlanetScale, etc.) |
| H3 | File storage is local disk — not shared across servers | Horizontal scaling impossible; files lost on redeploy |
| H4 | Port hardcoded to `8001` — cloud uses dynamic `$PORT` | Deployment fails on Heroku, Railway, Render, etc. |
| H5 | Single uvicorn worker — gunicorn never used | Low throughput; one request blocks others |
| H6 | Health check returns HTTP 200 even when unhealthy | Load balancers never remove broken instances |
| H7 | No structured JSON logging | Cannot use CloudWatch, Datadog, or ELK |
| H8 | No CI/CD pipeline, no deployment documentation | Every deployment is manual and risky |
| H9 | Loose `requirements.txt` version pinning | Fresh installs may break due to dependency updates |

---

## 2. Improvements Needed

### 2.1 Security Improvements

- Move all secrets to environment variables with no hardcoded defaults
- Implement token blacklist / revocation on logout and account suspension
- Migrate from `localStorage` to `httpOnly` cookies for auth tokens
- Restrict CORS to specific production domains
- Add file type and size validation on all upload endpoints
- Serve uploaded files through authenticated endpoints with signed URLs
- ~~Add MFA / Two-Factor Authentication support~~ — done: TOTP, required for admin/superadmin (docs/AUTH_SECURITY.md)

### 2.2 Backend Improvements

- Set up Alembic migrations for all schema changes going forward
- Add DB indexes on all FK columns and frequently filtered columns
- Standardize all API responses with a common wrapper (`data`, `message`, `pagination`)
- Add pagination to all list endpoints using a single utility
- Implement background workers (Dramatiq / Celery) for email sending
- Add Redis caching for ATS scores, job stats, and session data
- Add password reset with secure email token
- Add email verification flow on registration
- Replace Ollama localhost with configurable cloud LLM (OpenAI / Groq / Anthropic)
- Write tests for all route handlers, auth flows, and file uploads

### 2.3 Frontend Improvements

- Move all API base URLs to `.env` / `VITE_API_URL`
- Replace all `alert()` / `prompt()` with a toast notification component
- Add a global React error boundary
- Add loading spinners and skeleton screens on all data fetches
- Add proper error messages on all API call failures
- Add client-side form validation (email format, phone format, salary min < max)
- Add server-side proctoring validation for online tests

### 2.4 Infrastructure Improvements

- Write `Dockerfile` and `docker-compose.yml` for local + cloud use
- Replace local file storage with cloud object storage (AWS S3 / Cloudflare R2)
- Make all config (DB host, port, secret key, SMTP) configurable via ENV
- Add structured JSON logging with request ID and user context
- Configure gunicorn with multiple workers for production
- Add proper health check with HTTP 5xx when dependencies are down
- Set up CI/CD pipeline (GitHub Actions) for automated testing and deployment

---

## 3. Complete Project Plan — Phase by Phase

---

### Phase 1 — Security & Stability Foundation
**Duration:** 2 weeks
**Goal:** Fix all critical security issues so the app is safe to run publicly.

| Task | Details |
|------|---------|
| 1.1 | Move ALL credentials to `.env` — remove every hardcoded password, SMTP pass, secret key |
| 1.2 | Add JWT token blacklist using Redis — real logout and account suspension |
| 1.3 | Fix error handler — return generic message, log details server-side only |
| 1.4 | Add file upload validation — allowed types (PDF, DOCX), max size (5 MB), MIME check |
| 1.5 | Restrict CORS to your actual domain in production |
| 1.6 | Serve resume/upload files through an authenticated endpoint, not direct disk path |
| 1.7 | Fix JWT secret to require env var with no insecure default |

**Deliverable:** App passes basic security audit. No credentials in source code.

---

### Phase 2 — Database & API Quality
**Duration:** 2 weeks
**Goal:** Reliable, consistent, and scalable data layer.

| Task | Details |
|------|---------|
| 2.1 | Set up Alembic — write initial migration for current schema |
| 2.2 | Add indexes on all FK columns and `current_stage`, `status`, `email` |
| 2.3 | Fix N+1 queries — add `joinedload()` everywhere relationships are navigated |
| 2.4 | Standardize all API responses — single wrapper with `data`, `message`, `status_code`, `pagination` |
| 2.5 | Add pagination to all list endpoints — standard `page` + `page_size` params |
| 2.6 | Add search and filter to admin user list, job list, candidate list |
| 2.7 | Set `response_model=` on all endpoints |

**Deliverable:** All API endpoints are consistent, fast, and return predictable responses.

---

### Phase 3 — Core Missing Features
**Duration:** 2 weeks
**Goal:** Fill in features that users expect from any professional app.

| Task | Details |
|------|---------|
| 3.1 | Password reset — forgot password form, secure email token, reset page |
| 3.2 | Email verification — send verification email on register, block unverified login |
| 3.3 | Background workers — move all email sending to Dramatiq queue |
| 3.4 | Complete email notifications — test results, interview feedback, offer expiry reminders |
| 3.5 | Redis caching — ATS scores, job statistics, user sessions |
| 3.6 | Replace Ollama localhost with configurable LLM API (OpenAI / Groq) |

**Deliverable:** Full registration + login + notification flow works end-to-end.

---

### Phase 4 — Frontend Polish
**Duration:** 2 weeks
**Goal:** Frontend is production-quality — no broken states, no raw alerts.

| Task | Details |
|------|---------|
| 4.1 | Create `.env.production` and `.env.development` — all URLs via `VITE_API_URL` |
| 4.2 | Replace all `alert()` / `prompt()` with a Toast notification component |
| 4.3 | Add global React error boundary at app root |
| 4.4 | Add loading skeletons / spinners on all data fetches |
| 4.5 | Add proper error messages on all 31+ silent catch blocks |
| 4.6 | Add form validation — email format, phone format, salary min < max, URL format |
| 4.7 | Move auth token to `httpOnly` cookie (requires backend cookie endpoint) |
| 4.8 | Add empty state screens (no jobs, no candidates, no interviews yet) |

**Deliverable:** Frontend handles all error and loading states gracefully. No raw browser dialogs.

---

### Phase 5 — Infrastructure & Deployment
**Duration:** 1–2 weeks
**Goal:** App can be deployed to any cloud server reliably.

| Task | Details |
|------|---------|
| 5.1 | Write `Dockerfile` for backend (Python + gunicorn, multi-stage build) |
| 5.2 | Write `Dockerfile` for frontend (Node build + nginx static serve) |
| 5.3 | Write `docker-compose.yml` — backend + frontend + MySQL + Redis + Ollama (local dev) |
| 5.4 | Replace local file storage with AWS S3 or Cloudflare R2 — per-tenant folder prefix |
| 5.5 | Make port, DB host, DB name, all secrets configurable via ENV |
| 5.6 | Fix health check endpoint — return HTTP 503 when DB or Redis is down |
| 5.7 | Add structured JSON logging (request ID, user ID, timestamp) |
| 5.8 | Configure gunicorn with `workers = (2 × CPU) + 1` for production |
| 5.9 | Set up GitHub Actions CI — run tests on every push, build Docker image on merge |
| 5.10 | Write `DEPLOYMENT.md` — step-by-step guide for cloud deployment |

**Deliverable:** One command deploys the full stack. CI runs tests automatically.

---

### Phase 6 — Testing & QA
**Duration:** 1–2 weeks
**Goal:** Catch bugs before clients do.

| Task | Details |
|------|---------|
| 6.1 | Write unit tests for all route handlers (auth, HR, candidate, admin, interviewer) |
| 6.2 | Write integration tests — full hiring workflow end-to-end (apply → offer) |
| 6.3 | Write tests for ATS scoring edge cases |
| 6.4 | Write tests for file upload validation |
| 6.5 | Add load test (locust) — simulate 50 concurrent users |
| 6.6 | Manual QA pass — test every screen in each role (Admin, HR, Interviewer, Candidate) |
| 6.7 | Fix all bugs found in QA |

**Deliverable:** 80%+ test coverage. Zero critical bugs in all user flows.

---

### Phase 7 — Pre-Launch & Branding
**Duration:** 1 week
**Goal:** App is ready for real users.

| Task | Details |
|------|---------|
| 7.1 | Set up production domain + SSL (Let's Encrypt or cloud certificate) |
| 7.2 | Configure production MySQL (managed DB — AWS RDS / PlanetScale / Railway MySQL) |
| 7.3 | Configure production Redis (Upstash / Redis Cloud) |
| 7.4 | Configure production file storage (S3 bucket with proper IAM permissions) |
| 7.5 | Set up error monitoring (Sentry — free tier) |
| 7.6 | Set up uptime monitoring (UptimeRobot — free tier) |
| 7.7 | Final security review — check all ENV vars set, CORS correct, debug mode off |
| 7.8 | Write user guide / help documentation for each role |

**Deliverable:** App running on production domain with monitoring and SSL.

---

## 4. Client Onboarding Plan

### Step 1 — Client Registration
- Client signs up on the platform (company name, admin email, company size)
- Email verification sent immediately
- Admin account created and linked to their company

### Step 2 — Admin Setup (Day 1)
Admin completes setup in order:

| Step | Action |
|------|--------|
| 2.1 | Set company name, logo, and branding |
| 2.2 | Create departments (Engineering, HR, Sales, etc.) |
| 2.3 | Add HR users — enter email, system sends invite with temp password |
| 2.4 | Add Interviewer users — enter email, system sends invite |
| 2.5 | Configure email notification preferences |

### Step 3 — HR Setup (Day 1–2)
HR users complete setup:

| Step | Action |
|------|--------|
| 3.1 | HR logs in via invite link, sets permanent password |
| 3.2 | Creates first job posting (title, description, requirements, salary range) |
| 3.3 | Configures ATS scoring weights for the job (skills priority, experience weight) |
| 3.4 | Sets interview stages for the job (phone screen → technical → HR round → offer) |
| 3.5 | Assigns interviewers to specific stages |

### Step 4 — Candidate Flow (Ongoing)
Once jobs are posted:

| Step | Action |
|------|--------|
| 4.1 | Candidates browse public job listing (no login required) |
| 4.2 | Candidate registers and uploads resume |
| 4.3 | ATS auto-scores resume and ranks candidate |
| 4.4 | HR reviews ranked candidates and shortlists |
| 4.5 | HR schedules interview — candidate receives email with date/time/link |
| 4.6 | Interviewer conducts interview and submits feedback |
| 4.7 | HR reviews feedback and moves candidate to next stage or rejects |
| 4.8 | AI generates role-specific interview questions for each stage |
| 4.9 | Online MCQ test sent to candidate with proctoring enabled |
| 4.10 | Offer letter generated and sent to selected candidate |

### Step 5 — Onboarding Support (Week 1)
- Welcome email with getting-started video walkthrough
- In-app checklist showing completion of each setup step
- Live chat or email support during first week
- Dedicated onboarding call for enterprise clients (10+ users)

### Step 6 — Ongoing Success
- Weekly email summary: candidates applied, interviews scheduled, offers sent
- Monthly analytics report: time-to-hire, source quality, ATS score distribution
- Feature update notifications
- Admin can export all data at any time (CSV / PDF)

---

---

## 5. Hosting & Onboarding Challenges — Caused by Current Code & How to Solve

This section maps every current code/architecture problem directly to the real-world pain you will feel
when you try to host the app or onboard a client — and gives a concrete fix for each.

---

### 5.1 Challenges During HOSTING

---

#### Challenge 1 — App Will Not Start on Any Cloud Server
**Root cause:** `main.py` line 254 hardcodes `port=8001`. Cloud platforms (Railway, Render, Heroku, AWS)
assign a random port via the `$PORT` environment variable. If your app ignores `$PORT` the platform
marks the deployment as failed immediately.

**What happens:** You push to Railway or Render. Deployment log says "App failed health check" or
"No open port detected". App never becomes live.

**Fix:**
```python
# main.py line 254 — change to:
uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
```

---

#### Challenge 2 — Database Connection Fails Immediately After Deploy
**Root cause:** `src/config/database_config_mysql.py` lines 25–28 hardcodes
`mysql+pymysql://root:1234@localhost:3306/recruitment_db`. Cloud databases are NEVER on localhost.

**What happens:** Every single API call returns HTTP 500. App starts but is completely broken.
Logs show `Can't connect to MySQL server on 'localhost'`.

**Fix:**
```python
# src/config/database_config_mysql.py — replace hardcoded line with:
DATABASE_URL = os.environ["DATABASE_URL"]  # e.g. mysql+pymysql://user:pass@host:3306/db
```
Then set `DATABASE_URL` as an environment variable in your cloud dashboard.

---

#### Challenge 3 — All Uploaded Resumes Are Lost on Every Redeploy
**Root cause:** Resumes saved to `./resumes/` and `./uploads/` — local disk folders that are wiped
every time you redeploy (cloud containers are stateless).

**What happens:** You onboard Client A. Candidates upload resumes. You push a new feature. All
resumes are gone. Client A calls you angry. You have no backup.

**Fix:**
- Integrate AWS S3 or Cloudflare R2 using `boto3`
- Save files to `s3://your-bucket/resumes/<filename>` instead of local path
- Store the S3 URL in the database instead of the local path
- Cost: ~$0.023 per GB/month on S3

---

#### Challenge 4 — AI Question Generation Completely Broken on Cloud
**Root cause:** `src/services/ollama_service.py` line 20 hardcodes `http://localhost:11434`.
Ollama is a local desktop app. Cloud servers don't have it installed.

**What happens:** HR schedules an interview. Interviewer clicks "Generate Questions". Request hangs
for 30 seconds then returns 500. No questions generated. Interviewers cannot prepare.

**Fix:**
- Add env var: `LLM_PROVIDER=openai` or `LLM_PROVIDER=groq`
- Add OpenAI fallback: `from openai import OpenAI; client.chat.completions.create(...)`
- Keep Ollama for local dev, use cloud LLM in production
- Cost: ~$0.002 per question generation with GPT-4o-mini

---

#### Challenge 5 — Only One Request Processed at a Time
**Root cause:** `main.py` starts a single uvicorn worker with no gunicorn configuration.
One slow request (ATS scoring, PDF parsing, ML inference) blocks all other users.

**What happens:** HR uploads a resume for ATS scoring. While it processes (3–10 seconds), every
other user gets a slow or timeout response. With 10 clients onboarded, 5 concurrent users = app
appears dead.

**Fix:**
```bash
# Replace single uvicorn start with gunicorn:
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
```
4 workers = 4 requests processed simultaneously.

---

#### Challenge 6 — Out of Memory Crash on Standard Cloud Instances
**Root cause:** `main.py` lines 86–107 loads `sentence-transformers`, `spacy`, `mediapipe`, and
`torch` into memory at startup. Each model is 300MB–2GB. With 4 workers, startup RAM = 4 × 2GB = 8GB.

**What happens:** You deploy to a $10/month server (2GB RAM). App crashes immediately with
`Killed` or `MemoryError`. You can't afford an 8GB server just for this.

**Fix (short term):** Lazy-load models — only import them when the first ATS request comes in,
not at startup.

**Fix (long term):** Move ATS scoring and ML inference to a separate microservice on a GPU instance.
The main API server stays lightweight.

---

#### Challenge 7 — Load Balancer Never Removes a Broken Server
**Root cause:** `main.py` health check (`/health`) returns HTTP 200 even when the database is down.

**What happens:** Database goes down at 2am. Load balancer pings `/health`, gets 200 OK, keeps
sending traffic to the broken server. Every user gets 500 errors. You only find out when clients
complain in the morning.

**Fix:**
```python
@app.get("/health")
async def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy"}
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
```

---

#### Challenge 8 — Secrets Leak via Git History or Server Logs
**Root cause:** Gmail password hardcoded in `src/services/email_service.py` lines 19–20.
MySQL password (`1234`) hardcoded in 6 files. These are in git history forever even if you delete them.

**What happens:** Someone reads your GitHub repo. They have your Gmail password.
They send spam from your address. Google blocks your SMTP. All system emails stop.

**Fix:**
1. Immediately revoke the Gmail app password — Google Account → Security → App Passwords
2. Generate a new app password
3. Store in `.env` only — never in code
4. Run BFG Repo Cleaner to scrub the password from git history

---

#### Challenge 9 — Frontend Points to localhost After Deployment
**Root cause:** `HRDashboard.jsx` lines 456, 471, 1198 and `InterviewerDashboard.jsx` line 1255
have hardcoded `http://localhost:8000` URLs. No `.env` files exist in the frontend at all.

**What happens:** Frontend deploys to Vercel or Netlify. HR clicks "Download Resume". Browser
requests `http://localhost:8000/resumes/abc.pdf` from their own laptop — which has nothing.
404 error. HR cannot access any candidate resumes. Same for test links pointing to `localhost:3001`.

**Fix:**
1. Create `frontend/.env.production` → `VITE_API_URL=https://api.yourdomain.com`
2. Create `frontend/.env.development` → `VITE_API_URL=http://localhost:8000`
3. Replace every hardcoded URL:
   - Before: `` `http://localhost:8000/${path}` ``
   - After: `` `${import.meta.env.VITE_API_URL}/${path}` ``

---

#### Challenge 10 — No Way to Debug Issues in Production
**Root cause:** Logs are plain text with no structure, no request IDs, no user context.

**What happens:** Client reports "the interview scheduling button doesn't work sometimes".
You see a wall of unstructured log text with no user IDs or request trace. You cannot
reproduce or debug the issue.

**Fix:**
```python
import json, uuid
@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = str(uuid.uuid4())
    response = await call_next(request)
    print(json.dumps({
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code
    }))
    return response
```

---

### 5.2 Challenges During CLIENT ONBOARDING

---

#### Challenge 11 — Admin Cannot Invite HR or Interviewers (No Invite System)
**Root cause:** No user invite flow exists. The only way to add users is via direct registration.
There is no "invite by email" feature. Admin cannot create accounts for their team.

**What happens:** Client's admin logs in on Day 1. Tries to add their HR manager. Finds no invite
button. HR must self-register, manually tell admin their email, and admin assigns the role.
New users often register with wrong roles.

**Fix:**
- Add `POST /api/v1/admin/invite` endpoint — sends email with one-time signup link
- Link pre-fills the role and company
- User clicks link, sets password, account auto-assigned correct role
- Invite token expires in 48 hours

---

#### Challenge 12 — New Client Locked Out on Day 1 (No Email Verification or Password Reset)
**Root cause:** `routes/auth_routes.py` creates accounts instantly with no email verification.
No forgot password flow exists anywhere.

**What happens:** Client types email as `hr@compan.com` instead of `hr@company.com`.
Account created with wrong email. Cannot reset password. Completely locked out.
Must contact you to manually fix the database.

**Fix:**
- Add email verification token on registration (Phase 3, Task 3.2)
- Add password reset flow with 1-hour expiry token (Phase 3, Task 3.1)

---

#### Challenge 13 — All Clients Share One Database — Data Will Mix
**Root cause:** No `company_id` / `tenant_id` column on any table. All companies' data shares
the same rows with zero isolation.

**What happens (at scale):** You onboard 5 clients. A bug in a query filter returns candidates
from the wrong company. Client A sees Client B's confidential data. GDPR violation.
Client A terminates contract and threatens legal action.

**Fix (minimum viable):**
- Add `company_id` column to `users`, `job_postings`, `candidates`, `departments`, `interviews`
- Filter EVERY query by `company_id` taken from the JWT token
- Add `company_id` to JWT payload on login

---

#### Challenge 14 — Onboarding Emails Block or Fail Silently
**Root cause:** Email sending is synchronous — SMTP calls happen inside the HTTP request handler.
Gmail SMTP can take 1–5 seconds. Multiple simultaneous users cause request timeouts.

**What happens:** New HR registers. Welcome email takes 5 seconds. Browser shows endless spinner.
HR thinks registration failed. Registers again. Now they have two accounts.
Or SMTP is down and the entire `/register` endpoint returns 500.

**Fix:**
- Move all email to Dramatiq background queue (Phase 3, Task 3.3)
- HTTP response returns immediately: `{"message": "Check your email"}`
- Worker sends email in background and retries on failure

---

#### Challenge 15 — Users Cannot Reset Their Password (Feature Missing)
**Root cause:** No forgot password / reset password flow in `routes/auth_routes.py`.

**What happens:** Interviewer forgets password 3 months after onboarding.
Clicks "Forgot Password". Nothing happens. Emails you.
You manually update their password in the database.
This happens dozens of times per month as clients grow.

**Fix:** Standard forgot-password flow — email with secure token, expires in 1 hour, reset form.

---

#### Challenge 16 — Clients See Raw Database Error Messages
**Root cause:** `main.py` lines 134–140 returns `"error": str(exc)` — raw Python exceptions to clients.

**What happens:** HR creates a job but misses a required field. Instead of "Please fill in the job title" they see:
`"error": "NOT NULL constraint failed: job_postings.title (sqlalchemy.exc.IntegrityError)"`
Client thinks the app is broken and raises a support ticket.

**Fix:**
```python
# main.py global exception handler:
return JSONResponse(status_code=500, content={"detail": "Something went wrong. Please try again."})
logger.error(f"Unhandled error: {exc}", exc_info=True)  # real error logged server-side only
```

---

#### Challenge 17 — HR Cannot View Resumes on Day 1
**Root cause:** Resume files on local disk + hardcoded `http://localhost:8000/${resume_path}` in frontend.

**What happens:** HR reviews first batch of candidates. Clicks "View Resume".
Browser requests file from `localhost:8000` on their own laptop — nothing there.
404 error. HR cannot review any resumes. Core workflow broken on Day 1 of client use.

**Fix:** S3 storage (Challenge 3 fix) + env variable URLs (Challenge 9 fix) + generate
pre-signed S3 URLs for each resume download that expire after 1 hour.

---

#### Challenge 18 — AI Questions Time Out on First Interview
**Root cause:** Ollama must be on the same machine. Cloud servers don't have it.

**What happens:** Interviewer opens interview panel. Clicks "Generate AI Questions".
Spinner runs 30 seconds. Timeout. No questions generated.
Interviewer enters the interview unprepared. Client complains "the AI doesn't work".

**Fix:** Replace Ollama localhost with OpenAI/Groq API configured via environment variable.
Keep a curated fallback set of 10 default questions if LLM call fails.

---

### 5.3 Summary — All 18 Challenges at a Glance

| # | Challenge | When It Hits | Priority | Fix in One Line |
|---|-----------|-------------|----------|-----------------|
| 1 | App won't start — hardcoded port | First deploy | CRITICAL | Read `$PORT` from env |
| 2 | DB connection fails — hardcoded localhost | First deploy | CRITICAL | `DATABASE_URL` env var |
| 3 | Resumes lost on every redeploy | First deploy | CRITICAL | Move files to S3/R2 |
| 4 | AI questions broken on cloud | First client use | CRITICAL | Add OpenAI/Groq fallback |
| 8 | Gmail credentials in git history | Any time | CRITICAL | Revoke + move to `.env` |
| 9 | Frontend hits localhost after deploy | First frontend deploy | CRITICAL | `VITE_API_URL` env var |
| 13 | Client data mixes across companies | 2+ clients onboarded | CRITICAL | Add `company_id` to all tables |
| 17 | HR cannot view resumes | HR Day 1 review | CRITICAL | S3 + authenticated URLs |
| 5 | One request at a time — app freezes | 5+ concurrent users | HIGH | gunicorn multi-worker |
| 6 | Out of memory crash on small server | First deploy | HIGH | Lazy-load ML models |
| 7 | Health check always returns OK | First DB outage | HIGH | Return HTTP 503 on failure |
| 10 | Cannot debug production issues | First production bug | MEDIUM | Structured JSON logging |
| 11 | Admin cannot invite team | Admin Day 1 | HIGH | Build invite-by-email flow |
| 12 | Client locked out on Day 1 | Registration | HIGH | Email verify + password reset |
| 14 | Emails block/fail during onboarding | Registration | HIGH | Background email workers |
| 15 | Users cannot reset password | Week 2+ | HIGH | Forgot password flow |
| 16 | Raw error messages shown to clients | Daily use | MEDIUM | Generic error responses |
| 18 | AI questions time out on first interview | Interviewer Day 1 | HIGH | Cloud LLM fallback |

---

## Summary Timeline

| Phase | Work | Duration |
|-------|------|----------|
| Phase 1 | Security & Stability | 2 weeks |
| Phase 2 | Database & API Quality | 2 weeks |
| Phase 3 | Core Missing Features | 2 weeks |
| Phase 4 | Frontend Polish | 2 weeks |
| Phase 5 | Infrastructure & Deployment | 1–2 weeks |
| Phase 6 | Testing & QA | 1–2 weeks |
| Phase 7 | Pre-Launch & Branding | 1 week |
| **Total** | **Ready for Client Onboarding** | **~12–13 weeks** |
