"""
HireFlow 12-Phase Workflow Data Mapping Test
============================================
Run: python test_workflow_data_mapping.py

Tests every workflow phase API endpoint and verifies that database data is
mapped correctly to the response — catching JSON strings returned as plain
strings instead of parsed arrays, hardcoded placeholder values, missing
relationship fields, and N+1 timing issues.

Known bugs targeted:
  BUG-1  GET /hr/feedback           — strengths/weaknesses returned as JSON string not list
  BUG-2  GET /hr/interviewers       — expertise returned as JSON string not list
  BUG-3  GET /candidate/applications— company hardcoded as "Company Name"
  BUG-4  GET /hr/jobs/{id}/candidates — matched/missing_skills JSON string not list
  BUG-5  GET /hr/jobs/{id}/candidates — photo_url may have double slash
  BUG-6  GET /hr/interviews         — N+1 on candidate lazy load
  BUG-7  GET /candidate/interviews  — N+1 on job lazy load

Env vars (all optional):
  SUPERADMIN_EMAIL      default: superadmin@hireflow.com
  SUPERADMIN_PASSWORD   default: Admin@1234
  DB_PASSWORD           default: 1234
  BASE_URL              default: http://localhost:8000
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta

import requests

# ── Configuration ──────────────────────────────────────────────────────────────
BASE = os.environ.get("BASE_URL", "http://localhost:8000")
SA_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "superadmin@hireflow.com")
SA_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "Admin@1234")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "1234")

RUN_ID = uuid.uuid4().hex[:8]
SLUG = f"testrun-{RUN_ID}"
ADMIN_EMAIL = f"testrun_{RUN_ID}_admin@test.hireflow.com"
ADMIN_PASS = "TestAdmin@1234"
HR_EMAIL = f"testrun_{RUN_ID}_hr@test.hireflow.com"
HR_PASS = "TestHr@1234"
IV_EMAIL = f"testrun_{RUN_ID}_iv@test.hireflow.com"
IV_PASS = "TestIv@1234"
CAND_EMAIL = f"testrun_{RUN_ID}_cand@test.hireflow.com"
CAND_PASS = "TestCand@1234"

FUTURE_DATE = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S")
OFFER_START = (datetime.utcnow() + timedelta(days=400)).strftime("%Y-%m-%d")
OFFER_EXPIRY = (datetime.utcnow() + timedelta(days=380)).strftime("%Y-%m-%d")

# Minimal valid PDF bytes for resume upload
_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n"
    b"0000000058 00000 n\n0000000115 00000 n\n"
    b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF"
)


# ── Test result record ─────────────────────────────────────────────────────────
class Row:
    def __init__(self, phase, phase_name, endpoint, status, issue="", detail=""):
        self.phase = phase
        self.phase_name = phase_name
        self.endpoint = endpoint
        self.status = status   # PASS | FAIL | WARN | SKIP | ERROR
        self.issue = issue
        self.detail = str(detail)[:250]


# ── Main runner ────────────────────────────────────────────────────────────────
class HireFlowTestRunner:
    def __init__(self):
        self.results: list[Row] = []
        self.tokens: dict[str, str] = {}
        self.data: dict[str, str] = {}

    # ── Utilities ──────────────────────────────────────────────────────────────

    def ah(self, role: str) -> dict:
        """Return Authorization header dict for the given role token."""
        tok = self.tokens.get(role, "")
        return {"Authorization": f"Bearer {tok}"}

    def record(self, phase, phase_name, endpoint, status, issue="", detail=""):
        self.results.append(Row(phase, phase_name, endpoint, status, issue, str(detail)[:250]))

    def ok(self, phase, phase_name, endpoint, msg=""):
        self.record(phase, phase_name, endpoint, "PASS", msg)

    def fail(self, phase, phase_name, endpoint, issue, detail=""):
        self.record(phase, phase_name, endpoint, "FAIL", issue, detail)

    def warn(self, phase, phase_name, endpoint, issue, detail=""):
        self.record(phase, phase_name, endpoint, "WARN", issue, detail)

    def skip(self, phase, phase_name, endpoint, reason=""):
        self.record(phase, phase_name, endpoint, "SKIP", reason)

    def login(self, email, password) -> str | None:
        try:
            r = requests.post(f"{BASE}/api/v1/auth/login",
                              json={"email": email, "password": password}, timeout=10)
            if r.status_code == 200:
                return r.json().get("access_token")
        except Exception:
            pass
        return None

    def verify_email_via_db(self, email: str) -> bool:
        """Directly set email_verified=True via pymysql when OTP email is unavailable."""
        try:
            import pymysql
            conn = pymysql.connect(host="localhost", user="root",
                                   password=DB_PASSWORD, database="recruitment_db",
                                   charset="utf8mb4")
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET email_verified=1, otp_code=NULL, otp_expires_at=NULL WHERE email=%s",
                    (email,)
                )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            self.warn(0, "Setup", "DB email verify", f"Cannot verify email via DB: {e}",
                      "Install pymysql or set email_verified manually")
            return False

    def assert_list(self, obj, field, phase, phase_name, endpoint) -> bool:
        val = obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)
        if val is None:
            self.warn(phase, phase_name, endpoint, f"'{field}' is null/missing")
            return False
        if isinstance(val, str):
            self.fail(phase, phase_name, endpoint,
                      f"'{field}' is a JSON string — expected array",
                      f"value={val[:80]!r}")
            return False
        if not isinstance(val, list):
            self.fail(phase, phase_name, endpoint,
                      f"'{field}' type={type(val).__name__} — expected list")
            return False
        self.ok(phase, phase_name, endpoint, f"'{field}' is list ({len(val)} items)")
        return True

    def assert_not_hardcoded(self, obj, field, bad, phase, phase_name, endpoint) -> bool:
        val = obj.get(field) if isinstance(obj, dict) else None
        if val == bad:
            self.fail(phase, phase_name, endpoint,
                      f"'{field}' hardcoded as {bad!r} — not real DB value")
            return False
        if not val:
            self.warn(phase, phase_name, endpoint, f"'{field}' is empty/null")
            return True
        self.ok(phase, phase_name, endpoint, f"'{field}'={val!r} (not hardcoded)")
        return True

    def assert_populated(self, obj, field, phase, phase_name, endpoint) -> bool:
        val = obj.get(field) if isinstance(obj, dict) else None
        if val is None or val == "" or val == "—":
            self.fail(phase, phase_name, endpoint, f"Required field '{field}' is empty/null/sentinel")
            return False
        self.ok(phase, phase_name, endpoint, f"'{field}' populated")
        return True

    def assert_numeric(self, obj, field, phase, phase_name, endpoint) -> bool:
        val = obj.get(field) if isinstance(obj, dict) else None
        if val is None:
            self.warn(phase, phase_name, endpoint, f"'{field}' is null")
            return False
        if not isinstance(val, (int, float)):
            self.fail(phase, phase_name, endpoint,
                      f"'{field}' type={type(val).__name__} — expected number",
                      f"value={val!r}")
            return False
        self.ok(phase, phase_name, endpoint, f"'{field}'={val} (numeric)")
        return True

    def check_no_double_slash(self, obj, field, phase, phase_name, endpoint):
        val = obj.get(field) if isinstance(obj, dict) else None
        if not val:
            return
        stripped = val.replace("://", "XX")
        if "//" in stripped:
            self.fail(phase, phase_name, endpoint,
                      f"'{field}' contains double slash", f"value={val!r}")
        else:
            self.ok(phase, phase_name, endpoint, f"'{field}' no double-slash")

    def get_resume_bytes(self):
        path = os.path.join(os.path.dirname(__file__), "test_resume.pdf")
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        return _MINIMAL_PDF

    # ── Setup ──────────────────────────────────────────────────────────────────

    def setup(self) -> bool:
        print(f"\n[SETUP] Run ID: {RUN_ID}")

        # S1: Login superadmin
        tok = self.login(SA_EMAIL, SA_PASSWORD)
        if not tok:
            print(f"  ERROR: Cannot login as superadmin ({SA_EMAIL}).")
            print("  Set SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD env vars.")
            print("  Trying fallback admin@hireflow.com …")
            tok = self.login("admin@hireflow.com", "Admin1234!")
            if tok:
                self.tokens["admin"] = tok
                print("  Fallback admin login OK — skipping company creation.")
                return self._setup_with_existing_admin()
            return False
        self.tokens["superadmin"] = tok
        print("  S1: Superadmin login OK")

        # S2: Create company + admin
        r = requests.post(f"{BASE}/api/v1/superadmin/companies",
                          json={
                              "company_name": f"TestCo {RUN_ID}",
                              "slug": SLUG,
                              "admin_email": ADMIN_EMAIL,
                              "admin_password": ADMIN_PASS,
                              "admin_first_name": "TestAdmin",
                              "admin_last_name": "User",
                          },
                          headers=self.ah("superadmin"), timeout=10)
        if r.status_code not in (200, 201):
            print(f"  ERROR: Create company failed: {r.status_code} {r.text[:200]}")
            return False
        self.data["company_id"] = r.json().get("id", "")
        print(f"  S2: Company created id={self.data['company_id']}")

        # S3: Login admin
        tok = self.login(ADMIN_EMAIL, ADMIN_PASS)
        if not tok:
            print("  ERROR: Admin login failed")
            return False
        self.tokens["admin"] = tok
        print("  S3: Admin login OK")

        return self._setup_with_existing_admin()

    def _setup_with_existing_admin(self) -> bool:
        # S4: Create department
        r = requests.post(f"{BASE}/api/v1/admin/departments",
                          json={"name": f"TestDept-{RUN_ID}", "description": "Test dept"},
                          headers=self.ah("admin"), timeout=10)
        if r.status_code not in (200, 201):
            print(f"  ERROR: Create department failed: {r.status_code} {r.text[:200]}")
            return False
        self.data["dept_id"] = r.json().get("id", "")
        print(f"  S4: Department created id={self.data['dept_id']}")

        # S5: Create HR user
        r = requests.post(f"{BASE}/api/v1/admin/users/hr",
                          json={
                              "email": HR_EMAIL,
                              "password": HR_PASS,
                              "first_name": "TestHR",
                              "last_name": "User",
                              "department_id": self.data["dept_id"],
                              "employee_id": f"EMP-HR-{RUN_ID}",
                          },
                          headers=self.ah("admin"), timeout=10)
        if r.status_code not in (200, 201):
            print(f"  ERROR: Create HR failed: {r.status_code} {r.text[:200]}")
            return False
        print("  S5: HR user created")

        tok = self.login(HR_EMAIL, HR_PASS)
        if not tok:
            print("  ERROR: HR login failed")
            return False
        self.tokens["hr"] = tok
        print("  S5: HR login OK")

        # S6: Create Interviewer user (with expertise_areas as JSON list)
        r = requests.post(f"{BASE}/api/v1/admin/users/interviewer",
                          json={
                              "email": IV_EMAIL,
                              "password": IV_PASS,
                              "first_name": "TestIV",
                              "last_name": "User",
                              "department_id": self.data["dept_id"],
                              "employee_id": f"EMP-IV-{RUN_ID}",
                              "expertise_areas": ["Python", "FastAPI", "System Design"],
                              "years_experience": 5,
                          },
                          headers=self.ah("admin"), timeout=10)
        if r.status_code not in (200, 201):
            print(f"  ERROR: Create Interviewer failed: {r.status_code} {r.text[:200]}")
            return False
        self.data["interviewer_user_id"] = r.json().get("id", "")
        print(f"  S6: Interviewer created id={self.data['interviewer_user_id']}")

        # Get interviewer profile id
        r2 = requests.get(f"{BASE}/api/v1/hr/interviewers", headers=self.ah("hr"), timeout=10)
        if r2.status_code == 200:
            for iv in r2.json():
                if iv.get("email") == IV_EMAIL:
                    self.data["interviewer_profile_id"] = iv.get("id", "")
                    break
        if not self.data.get("interviewer_profile_id"):
            print("  WARN: Could not find interviewer profile id — some tests may skip")

        tok = self.login(IV_EMAIL, IV_PASS)
        if not tok:
            print("  ERROR: Interviewer login failed")
            return False
        self.tokens["interviewer"] = tok
        print("  S6: Interviewer login OK")

        # S7: Register candidate + verify email via DB
        r = requests.post(f"{BASE}/api/v1/auth/register",
                          json={
                              "email": CAND_EMAIL,
                              "password": CAND_PASS,
                              "role": "candidate",
                              "first_name": "TestCand",
                              "last_name": "User",
                          }, timeout=10)
        if r.status_code not in (200, 201):
            print(f"  ERROR: Candidate register failed: {r.status_code} {r.text[:200]}")
            return False

        verified = self.verify_email_via_db(CAND_EMAIL)
        if not verified:
            print("  WARN: Could not verify candidate email via DB — candidate tests may fail")

        tok = self.login(CAND_EMAIL, CAND_PASS)
        if not tok:
            print("  ERROR: Candidate login failed (email may not be verified)")
            return False
        self.tokens["candidate"] = tok
        print("  S7: Candidate registered and logged in OK")
        print("[SETUP] Complete\n")
        return True

    # ── Phase 1: Job Creation ──────────────────────────────────────────────────

    def phase1_job_creation(self):
        ep = "POST /api/v1/hr/jobs"
        ph, pn = 1, "Job Creation"
        r = requests.post(f"{BASE}/api/v1/hr/jobs",
                          json={
                              "title": f"TEST_SeniorDev_{RUN_ID}",
                              "description": "Backend development using Python, FastAPI, SQLAlchemy, Docker, REST APIs. Strong SQL skills required.",
                              "requirements": "5+ years Python. FastAPI experience. Strong SQL. Docker deployment experience.",
                              "department_id": self.data.get("dept_id", ""),
                              "location": "Remote",
                              "employment_type": "Full-time",
                              "salary_min": 80000,
                              "salary_max": 120000,
                              "experience_years": 3,
                              "required_skills": ["Python", "FastAPI", "SQLAlchemy", "Docker"],
                          },
                          headers=self.ah("hr"), timeout=10)
        if r.status_code not in (200, 201):
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return
        data = r.json()
        self.data["job_id"] = data.get("id", "")
        self.ok(ph, pn, ep, f"Job created id={self.data['job_id']}")

        # required_skills must be list
        self.assert_list(data, "required_skills", ph, pn, ep)
        self.assert_populated(data, "department", ph, pn, ep)

        # Verify with GET
        ep2 = f"GET /api/v1/hr/jobs/{self.data['job_id']}"
        r2 = requests.get(f"{BASE}/api/v1/hr/jobs/{self.data['job_id']}",
                          headers=self.ah("hr"), timeout=10)
        if r2.status_code == 200:
            self.assert_list(r2.json(), "required_skills", ph, pn, ep2)
        else:
            self.warn(ph, pn, ep2, f"GET job returned {r2.status_code}")

    # ── Phase 2: Career Page & Application ────────────────────────────────────

    def phase2_application(self):
        ph, pn = 2, "Career Page & Application"

        # 2a: Public job list
        ep = "GET /api/v1/candidate/jobs"
        r = requests.get(f"{BASE}/api/v1/candidate/jobs",
                         headers=self.ah("candidate"), timeout=10)
        if r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
        else:
            jobs = r.json()
            job_list = jobs.get("jobs", jobs) if isinstance(jobs, dict) else jobs
            self.ok(ph, pn, ep, f"{len(job_list)} jobs returned")
            for j in job_list[:3]:
                skills = j.get("required_skills")
                if isinstance(skills, str):
                    self.fail(ph, pn, ep, "required_skills is JSON string not list", f"job={j.get('title')!r}")
                    break
                elif isinstance(skills, list):
                    self.ok(ph, pn, ep, "required_skills is list in public job listing")
                    break

        # 2b: Apply to the job
        ep2 = f"POST /api/v1/candidate/jobs/{self.data.get('job_id')}/apply"
        job_id = self.data.get("job_id")
        if not job_id:
            self.skip(ph, pn, ep2, "No job_id from Phase 1")
            return

        resume_bytes = self.get_resume_bytes()
        application_data = json.dumps({
            "first_name": "TestCand",
            "last_name": "User",
            "email": CAND_EMAIL,
            "phone": "9876543210",
            "linkedin_url": "",
            "github_url": "",
            "portfolio_url": "",
            "notice_period": "30 days",
        })
        files = {"resume": ("test_resume.pdf", resume_bytes, "application/pdf")}
        data_fields = {"application_data": application_data}
        r2 = requests.post(f"{BASE}/api/v1/candidate/jobs/{job_id}/apply",
                           files=files,
                           data=data_fields,
                           headers=self.ah("candidate"), timeout=30)
        if r2.status_code not in (200, 201):
            self.fail(ph, pn, ep2, f"HTTP {r2.status_code}", r2.text[:300])
            return
        resp = r2.json()
        self.data["candidate_id"] = resp.get("candidate_id", "")
        self.ok(ph, pn, ep2, f"Applied, candidate_id={self.data['candidate_id']}")
        self.assert_numeric(resp, "ats_score", ph, pn, ep2)

    # ── Phase 3: ATS Scoring ──────────────────────────────────────────────────

    def phase3_ats_scoring(self):
        ph, pn = 3, "ATS Scoring"
        ep = f"GET /api/v1/hr/jobs/{{id}}/candidates"
        job_id = self.data.get("job_id")
        if not job_id:
            self.skip(ph, pn, ep, "No job_id")
            return
        r = requests.get(f"{BASE}/api/v1/hr/jobs/{job_id}/candidates",
                         headers=self.ah("hr"), timeout=10)
        if r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return

        resp = r.json()
        candidates = resp.get("candidates", [])
        self.ok(ph, pn, ep, f"total_candidates={resp.get('total_candidates', '?')}")

        if not candidates:
            self.warn(ph, pn, ep, "No candidates in response — cannot test field types")
            return

        for c in candidates:
            # BUG-4: matched_skills must be list
            self.assert_list(c, "matched_skills", ph, pn, ep)
            # BUG-4: missing_skills must be list
            self.assert_list(c, "missing_skills", ph, pn, ep)
            # extracted_skills must be list
            self.assert_list(c, "extracted_skills", ph, pn, ep)
            # BUG-5: photo_url double-slash
            self.check_no_double_slash(c, "photo_url", ph, pn, ep)
            # ats_score must be numeric
            self.assert_numeric(c, "ats_score", ph, pn, ep)
            # name must be populated
            self.assert_populated(c, "name", ph, pn, ep)
            break  # first candidate is enough for type checks

    # ── Phase 4: Shortlisting ─────────────────────────────────────────────────

    def phase4_shortlisting(self):
        ph, pn = 4, "Shortlisting"
        ep = "POST /api/v1/hr/jobs/{id}/select-candidates"
        job_id = self.data.get("job_id")
        candidate_id = self.data.get("candidate_id")
        if not job_id or not candidate_id:
            self.skip(ph, pn, ep, "No job_id or candidate_id from earlier phases")
            return

        r = requests.post(f"{BASE}/api/v1/hr/jobs/{job_id}/select-candidates",
                          json={
                              "candidate_ids": [candidate_id],
                              "selection_type": "manual",
                              "reject_others": False,
                              "next_stage": "shortlisted",
                          },
                          headers=self.ah("hr"), timeout=10)
        if r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return

        resp = r.json()
        selected = resp.get("selected_count", resp.get("count", 0))
        if selected and selected >= 1:
            self.ok(ph, pn, ep, f"selected_count={selected}")
        else:
            self.warn(ph, pn, ep, f"selected_count={selected} — expected >= 1")

        # Confirm stage changed
        ep2 = f"GET /api/v1/hr/jobs/{{id}}/candidates (stage check)"
        r2 = requests.get(f"{BASE}/api/v1/hr/jobs/{job_id}/candidates",
                          headers=self.ah("hr"), timeout=10)
        if r2.status_code == 200:
            cands = r2.json().get("candidates", [])
            for c in cands:
                if c.get("id") == candidate_id or str(c.get("id")) == str(candidate_id):
                    stage = c.get("current_stage", "")
                    if stage == "shortlisted":
                        self.ok(ph, pn, ep2, "Stage correctly updated to 'shortlisted'")
                    else:
                        self.warn(ph, pn, ep2, f"Stage={stage!r} expected 'shortlisted'")
                    break

    # ── Phase 5: Test/Interview Creation ─────────────────────────────────────

    def phase5_test_creation(self):
        ph, pn = 5, "Test Creation"
        ep = "POST /api/v1/hr/interviews"
        candidate_id = self.data.get("candidate_id")
        iv_profile_id = self.data.get("interviewer_profile_id")
        if not candidate_id or not iv_profile_id:
            self.skip(ph, pn, ep, "Missing candidate_id or interviewer_profile_id")
            return

        r = requests.post(f"{BASE}/api/v1/hr/interviews",
                          json={
                              "candidate_id": candidate_id,
                              "interviewer_id": iv_profile_id,
                              "interview_type": "technical",
                              "scheduled_datetime": FUTURE_DATE,
                              "duration_minutes": 60,
                              "meeting_link": "https://meet.example.com/test",
                          },
                          headers=self.ah("hr"), timeout=10)
        if r.status_code == 409:
            # Interview already exists for this job — get its id
            detail = r.json().get("detail", "")
            self.warn(ph, pn, ep, "Interview already exists (409)", detail)
            # Try to extract existing interview id from detail
            import re
            match = re.search(r"Interview ID: (\d+)", detail)
            if match:
                self.data["interview_id"] = match.group(1)
            return
        if r.status_code not in (200, 201):
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return

        resp = r.json()
        self.data["interview_id"] = str(resp.get("interview_id", ""))
        self.ok(ph, pn, ep, f"Interview created id={self.data['interview_id']}")
        self.assert_populated(resp, "interview_id", ph, pn, ep)

    # ── Phase 6: Interviewer List (BUG-2) ─────────────────────────────────────

    def phase6_interviewer_list(self):
        ph, pn = 6, "Interviewer List"
        ep = "GET /api/v1/hr/interviewers"
        r = requests.get(f"{BASE}/api/v1/hr/interviewers",
                         headers=self.ah("hr"), timeout=10)
        if r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return

        interviewers = r.json()
        self.ok(ph, pn, ep, f"{len(interviewers)} interviewers returned")
        found_test_iv = False
        for iv in interviewers:
            if iv.get("email") == IV_EMAIL:
                found_test_iv = True
                # BUG-2: expertise must be a list
                self.assert_list(iv, "expertise", ph, pn, ep)
                self.assert_populated(iv, "name", ph, pn, ep)
                self.assert_populated(iv, "email", ph, pn, ep)
                break

        if not found_test_iv:
            self.warn(ph, pn, ep, f"Test interviewer {IV_EMAIL!r} not found in list")
            # Still check the first interviewer's expertise type
            if interviewers:
                self.assert_list(interviewers[0], "expertise", ph, pn, ep)

    # ── Phase 7: Candidate Interviews (BUG-7) ────────────────────────────────

    def phase7_candidate_interviews(self):
        ph, pn = 7, "Candidate Interviews"
        ep = "GET /api/v1/candidate/interviews"
        start = time.time()
        r = requests.get(f"{BASE}/api/v1/candidate/interviews",
                         headers=self.ah("candidate"), timeout=10)
        elapsed = time.time() - start

        if r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return

        interviews = r.json()
        n = len(interviews)
        self.ok(ph, pn, ep, f"{n} interview(s) returned")

        for iv in interviews:
            # job_title should be populated (tests N+1 lazy load)
            jt = iv.get("job_title")
            if not jt or jt == "N/A":
                self.warn(ph, pn, ep, "job_title is null/N/A — possible N+1 lazy load failure")
            else:
                self.ok(ph, pn, ep, f"job_title={jt!r}")

            status_val = iv.get("status")
            known = ("SCHEDULED", "READY", "IN_PROGRESS", "COMPLETED")
            if status_val not in known:
                self.warn(ph, pn, ep, f"Unexpected status={status_val!r}")
            break

        # N+1 timing warning
        if n > 0 and elapsed > n * 0.15:
            self.warn(ph, pn, ep,
                      f"Slow {elapsed:.2f}s for {n} record(s) — possible N+1 (iv.job lazy load)",
                      "Fix: add options(joinedload(Interview.job)) in candidate_routes.py:864")
        elif n > 0:
            self.ok(ph, pn, ep, f"Response time OK: {elapsed:.2f}s for {n} record(s)")

    # ── Phase 8: Test Results + Applications (BUG-3) ─────────────────────────

    def phase8_test_results_and_apps(self):
        ph, pn = 8, "Test Results & Applications"

        # 8a: HR test-results
        ep = "GET /api/v1/hr/test-results"
        r = requests.get(f"{BASE}/api/v1/hr/test-results",
                         headers=self.ah("hr"), timeout=10)
        if r.status_code == 500:
            self.fail(ph, pn, ep, "HTTP 500 — possible ORM/table mismatch (test_results table)")
        elif r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
        else:
            results = r.json()
            self.ok(ph, pn, ep, f"{len(results)} test result(s)")
            for res in results[:2]:
                jt = res.get("job_title")
                if not jt or jt == "—":
                    self.warn(ph, pn, ep, "job_title is empty/sentinel in test-results")
                score = res.get("test_score")
                if score is not None and not isinstance(score, (int, float)):
                    self.fail(ph, pn, ep, "test_score is not numeric", f"type={type(score)}")

        # 8b: Candidate applications — BUG-3
        ep2 = "GET /api/v1/candidate/applications"
        r2 = requests.get(f"{BASE}/api/v1/candidate/applications",
                          headers=self.ah("candidate"), timeout=10)
        if r2.status_code != 200:
            self.fail(ph, pn, ep2, f"HTTP {r2.status_code}", r2.text[:200])
            return

        apps = r2.json()
        self.ok(ph, pn, ep2, f"{len(apps)} application(s)")
        for app in apps:
            # BUG-3: company must NOT be literal "Company Name"
            self.assert_not_hardcoded(app, "company", "Company Name", ph, pn, ep2)
            self.assert_populated(app, "job_title", ph, pn, ep2)
            # stage must be populated
            stage = app.get("stage") or app.get("status")
            if not stage:
                self.fail(ph, pn, ep2, "stage/status is empty")
            else:
                self.ok(ph, pn, ep2, f"stage={stage!r}")
            # ats_score must be numeric if present
            sc = app.get("ats_score")
            if sc is not None and not isinstance(sc, (int, float)):
                self.fail(ph, pn, ep2, "ats_score is not numeric", f"type={type(sc)}, value={sc!r}")
            elif sc is not None:
                self.ok(ph, pn, ep2, f"ats_score={sc} (numeric)")
            break

    # ── Phase 9: HR Interviews List (BUG-6) ──────────────────────────────────

    def phase9_hr_interviews(self):
        ph, pn = 9, "HR Interviews List"
        ep = "GET /api/v1/hr/interviews"
        start = time.time()
        r = requests.get(f"{BASE}/api/v1/hr/interviews",
                         headers=self.ah("hr"), timeout=15)
        elapsed = time.time() - start

        if r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return

        interviews = r.json()
        n = len(interviews)
        self.ok(ph, pn, ep, f"{n} interview(s) returned")

        for iv in interviews:
            # candidate_name must not be the sentinel "—"
            cn = iv.get("candidate_name")
            if cn == "—" or cn is None:
                self.warn(ph, pn, ep,
                          "candidate_name is '—'/null — lazy load iv.candidate may have failed",
                          "Fix: add joinedload(Interview.candidate) in hr_routes.py:1573")
            else:
                self.ok(ph, pn, ep, f"candidate_name={cn!r}")
            self.assert_populated(iv, "job_title", ph, pn, ep)
            break

        # BUG-6: N+1 timing warning
        if n > 0 and elapsed > n * 0.15:
            self.warn(ph, pn, ep,
                      f"Slow {elapsed:.2f}s for {n} record(s) — N+1 pattern on iv.candidate",
                      "Fix: joinedload(Interview.candidate) in hr_routes.py:1573")
        elif n >= 0:
            self.ok(ph, pn, ep, f"Response time {elapsed:.2f}s for {n} record(s)")

    # ── Phase 10: Schedule Interview ─────────────────────────────────────────

    def phase10_schedule_interview(self):
        ph, pn = 10, "Schedule Interview"
        ep = "POST /api/v1/hr/interviews (already exists)"
        # The interview was already created in Phase 5. Verify it appears in the list.
        interview_id = self.data.get("interview_id")
        if not interview_id:
            self.skip(ph, pn, ep, "No interview_id from Phase 5")
            return

        r = requests.get(f"{BASE}/api/v1/hr/interviews", headers=self.ah("hr"), timeout=10)
        if r.status_code != 200:
            self.fail(ph, pn, ep, f"HTTP {r.status_code}", r.text[:200])
            return

        found = False
        for iv in r.json():
            if str(iv.get("id")) == str(interview_id):
                found = True
                sdt = iv.get("scheduled_datetime")
                if sdt:
                    self.ok(ph, pn, ep, f"Interview {interview_id} has scheduled_datetime={sdt!r}")
                else:
                    self.warn(ph, pn, ep, "scheduled_datetime is null")
                break

        if not found:
            self.warn(ph, pn, ep, f"Interview {interview_id} not found in HR interviews list")
        else:
            self.ok(ph, pn, ep, f"Interview {interview_id} visible in HR list")

    # ── Phase 11: Interview Feedback (BUG-1) ─────────────────────────────────

    def phase11_feedback(self):
        ph, pn = 11, "Interview Feedback"
        interview_id = self.data.get("interview_id")

        # 11a: Submit feedback via interviewer endpoint
        ep_submit = f"POST /api/v1/interviewer/interviews/{{id}}/feedback"
        if not interview_id:
            self.skip(ph, pn, ep_submit, "No interview_id")
        else:
            r = requests.post(
                f"{BASE}/api/v1/interviewer/interviews/{interview_id}/feedback",
                json={
                    "technical_score": 8.5,
                    "communication_score": 7.0,
                    "problem_solving_score": 9.0,
                    "cultural_fit_score": 8.0,
                    "strengths": ["Strong Python skills", "Good problem solver", "Fast learner"],
                    "weaknesses": ["Needs more system design exposure"],
                    "recommendations": "Ready for senior role with mentoring",
                    "hire_recommendation": "hire",
                    "additional_notes": "Impressed with the coding exercise.",
                },
                headers=self.ah("interviewer"), timeout=10)
            if r.status_code in (200, 201):
                self.ok(ph, pn, ep_submit, "Feedback submitted")
            elif r.status_code == 400 and "already" in r.text.lower():
                self.ok(ph, pn, ep_submit, "Feedback already exists (idempotent OK)")
            elif r.status_code == 404:
                self.warn(ph, pn, ep_submit,
                          "404 — interview may not be assigned to this interviewer email",
                          r.text[:200])
            else:
                self.fail(ph, pn, ep_submit, f"HTTP {r.status_code}", r.text[:200])

        # 11b: HR reads feedback — BUG-1
        ep_get = "GET /api/v1/hr/feedback"
        r2 = requests.get(f"{BASE}/api/v1/hr/feedback",
                          headers=self.ah("hr"), timeout=10)
        if r2.status_code != 200:
            self.fail(ph, pn, ep_get, f"HTTP {r2.status_code}", r2.text[:200])
            return

        feedbacks = r2.json()
        self.ok(ph, pn, ep_get, f"{len(feedbacks)} feedback(s) returned")

        if not feedbacks:
            self.warn(ph, pn, ep_get, "No feedbacks yet — cannot test BUG-1 field types")
            return

        for fb in feedbacks:
            # BUG-1a: strengths must be list
            self.assert_list(fb, "strengths", ph, pn, ep_get)
            # BUG-1b: weaknesses must be list
            self.assert_list(fb, "weaknesses", ph, pn, ep_get)
            # Scores must be numeric
            for sc_field in ("technical_score", "communication_score",
                             "problem_solving_score", "cultural_fit_score", "overall_score"):
                self.assert_numeric(fb, sc_field, ph, pn, ep_get)
            self.assert_populated(fb, "hire_recommendation", ph, pn, ep_get)
            break

    # ── Phase 12: Offer Letter ────────────────────────────────────────────────

    def phase12_offer_letter(self):
        ph, pn = 12, "Offer Letter"
        ep_post = "POST /api/v1/hr/offers"
        candidate_id = self.data.get("candidate_id")
        if not candidate_id:
            self.skip(ph, pn, ep_post, "No candidate_id")
        else:
            r = requests.post(f"{BASE}/api/v1/hr/offers",
                              json={
                                  "candidate_id": candidate_id,
                                  "offer_type": "full_time",
                                  "salary": 95000.0,
                                  "bonus": 5000.0,
                                  "start_date": OFFER_START,
                                  "expiry_date": OFFER_EXPIRY,
                                  "terms": "Standard employment terms with 3 months probation.",
                              },
                              headers=self.ah("hr"), timeout=10)
            if r.status_code in (200, 201):
                resp = r.json()
                self.data["offer_id"] = resp.get("offer_id", "")
                self.ok(ph, pn, ep_post, f"Offer created id={self.data['offer_id']}")
                self.assert_numeric(resp, "salary", ph, pn, ep_post)
            else:
                self.fail(ph, pn, ep_post, f"HTTP {r.status_code}", r.text[:200])

        # 12b: Candidate views offers
        ep_cand = "GET /api/v1/candidate/offers"
        r2 = requests.get(f"{BASE}/api/v1/candidate/offers",
                          headers=self.ah("candidate"), timeout=10)
        if r2.status_code != 200:
            self.fail(ph, pn, ep_cand, f"HTTP {r2.status_code}", r2.text[:200])
        else:
            offers = r2.json().get("offers", r2.json())
            self.ok(ph, pn, ep_cand, f"{len(offers)} offer(s)")
            for o in offers:
                self.assert_numeric(o, "salary", ph, pn, ep_cand)
                self.assert_populated(o, "job_title", ph, pn, ep_cand)
                st = o.get("status")
                valid = ("draft", "sent", "accepted", "rejected", "expired")
                if st not in valid:
                    self.warn(ph, pn, ep_cand, f"Unexpected offer status={st!r}")
                break

        # 12c: HR views offers
        ep_hr = "GET /api/v1/hr/offers"
        r3 = requests.get(f"{BASE}/api/v1/hr/offers",
                          headers=self.ah("hr"), timeout=10)
        if r3.status_code != 200:
            self.fail(ph, pn, ep_hr, f"HTTP {r3.status_code}", r3.text[:200])
        else:
            hr_offers = r3.json()
            self.ok(ph, pn, ep_hr, f"{len(hr_offers)} offer(s)")
            for o in hr_offers:
                self.assert_populated(o, "candidate_name", ph, pn, ep_hr)
                self.assert_populated(o, "job_title", ph, pn, ep_hr)
                break

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def cleanup(self):
        job_id = self.data.get("job_id")
        if job_id:
            try:
                requests.delete(f"{BASE}/api/v1/hr/jobs/{job_id}",
                                headers=self.ah("hr"), timeout=5)
            except Exception:
                pass

        print("\n[CLEANUP] Note: Test users and company must be removed manually.")
        print(f"  SQL: DELETE FROM users WHERE email LIKE 'testrun_{RUN_ID}%';")
        if self.data.get("company_id"):
            print(f"  SQL: DELETE FROM companies WHERE id='{self.data['company_id']}';")

    # ── Summary ────────────────────────────────────────────────────────────────

    def print_summary(self):
        COLS = {"PASS": "\033[32m", "FAIL": "\033[31m", "WARN": "\033[33m",
                "SKIP": "\033[90m", "ERROR": "\033[35m"}
        RESET = "\033[0m"

        print("\n" + "=" * 90)
        print("HIREFLOW 12-PHASE WORKFLOW DATA MAPPING RESULTS")
        print(f"Run: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}  |  Base: {BASE}")
        print("=" * 90)
        print(f"{'Ph':>2}  {'Phase':<22}  {'Endpoint':<42}  {'Status':<6}  Issue")
        print("-" * 90)
        for row in self.results:
            color = COLS.get(row.status, "")
            ep = row.endpoint[:42]
            issue = row.issue[:35]
            print(f"{row.phase:>2}  {row.phase_name:<22}  {ep:<42}  "
                  f"{color}{row.status:<6}{RESET}  {issue}")

        counts = {s: sum(1 for r in self.results if r.status == s)
                  for s in ("PASS", "FAIL", "WARN", "SKIP", "ERROR")}
        total = len(self.results)
        print("=" * 90)
        print(f"TOTAL: {total}  |  "
              f"\033[32mPASS: {counts['PASS']}\033[0m  |  "
              f"\033[31mFAIL: {counts['FAIL']}\033[0m  |  "
              f"\033[33mWARN: {counts['WARN']}\033[0m  |  "
              f"\033[90mSKIP: {counts['SKIP']}\033[0m")

        fails = [r for r in self.results if r.status == "FAIL"]
        if fails:
            print("\nCONFIRMED DATA MAPPING BUGS:")
            for i, r in enumerate(fails, 1):
                print(f"  [{i:>2}] Ph{r.phase:>2} | {r.endpoint:<42} | {r.issue}")
        else:
            print("\n  No FAIL — all checked fields map correctly!")

        warns = [r for r in self.results if r.status == "WARN"]
        if warns:
            print("\nWARNINGS (non-fatal issues):")
            for r in warns:
                detail = f" — {r.detail}" if r.detail else ""
                print(f"       Ph{r.phase:>2} | {r.endpoint:<42} | {r.issue}{detail[:60]}")
        print("=" * 90)

        return counts["FAIL"]

    # ── Orchestrator ───────────────────────────────────────────────────────────

    def run(self):
        print("=" * 90)
        print("HireFlow 12-Phase Workflow Data Mapping Test")
        print(f"Server: {BASE}   DB password: {'*' * len(DB_PASSWORD)}")
        print("=" * 90)

        ok = self.setup()
        if not ok:
            print("\n[ABORT] Setup failed — cannot run workflow tests.")
            sys.exit(2)

        phases = [
            self.phase1_job_creation,
            self.phase2_application,
            self.phase3_ats_scoring,
            self.phase4_shortlisting,
            self.phase5_test_creation,
            self.phase6_interviewer_list,
            self.phase7_candidate_interviews,
            self.phase8_test_results_and_apps,
            self.phase9_hr_interviews,
            self.phase10_schedule_interview,
            self.phase11_feedback,
            self.phase12_offer_letter,
        ]
        for phase_fn in phases:
            try:
                phase_fn()
            except Exception as exc:
                name = phase_fn.__name__
                self.record(0, name, "?", "ERROR", str(exc)[:120])

        try:
            self.cleanup()
        except Exception:
            pass

        fail_count = self.print_summary()
        sys.exit(1 if fail_count > 0 else 0)


if __name__ == "__main__":
    HireFlowTestRunner().run()
