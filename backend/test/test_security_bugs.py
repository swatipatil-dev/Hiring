"""
SECURITY BUG REGRESSION TESTS
==============================
Each test documents a real bug found in the codebase.
Tests are written to FAIL first (proving the bug exists),
then PASS after the corresponding fix is applied.

Bug sources: deep code audit + PROJECT_ROADMAP.md security section.
"""
import pytest
import uuid
import io
from sqlalchemy import text


# ── BUG-01: Offer respond endpoint has NO authentication ─────────────────────

@pytest.mark.security
def test_offer_respond_requires_auth(client, db):
    """
    BUG-01 (CRITICAL): POST /api/v1/evaluation/offers/{id}/respond
    accepts requests with NO Authorization header.
    Any anonymous user can accept or reject ANY offer.

    Fix: Add `current_user = Depends(get_current_user)` to the endpoint
         and verify the candidate owns the offer.
    """
    # Insert a fake offer row directly so we have an ID to target
    offer_id = str(uuid.uuid4())
    db.execute(text(
        "INSERT IGNORE INTO offers (id, status, salary, job_id, candidate_id) "
        "VALUES (:id, 'sent', 50000, 'fake-job', 'fake-cand')"
    ), {"id": offer_id})
    db.commit()

    # Call with NO auth token — should get 401/403, not 200/404
    resp = client.post(
        f"/api/v1/evaluation/offers/{offer_id}/respond",
        json={"decision": "accept"},
        # No Authorization header
    )
    assert resp.status_code in (401, 403), (
        f"BUG-01 CONFIRMED: Offer respond endpoint returned {resp.status_code} "
        f"with no auth. Expected 401/403. "
        f"Fix: add Depends(get_current_user) to routes/evaluation_routes.py"
    )


# ── BUG-02: candidate.company_id is NULL after applying ─────────────────────

@pytest.mark.security
def test_candidate_company_id_not_null(client, hr_headers, candidate_headers, db):
    """
    BUG-02 (CRITICAL / roadmap Challenge-13): When a candidate applies to a job,
    the Candidate row is created with company_id = NULL.
    In a multi-tenant system this breaks data isolation across companies.

    Fix: Add `company_id=job.company_id` in routes/candidate_routes.py
         inside the Candidate() constructor call.
    """
    from src.models.models import Candidate

    # Create and approve a job
    job_resp = client.post("/api/v1/hr/jobs", headers=hr_headers, json={
        "title": "Bug02 Test Job",
        "description": "test",
        "requirements": "test",
        "location": "Remote",
        "employment_type": "full_time",
        "experience_years": 1,
        "required_skills": ["Python"],
        "salary_min": 40000,
        "salary_max": 80000,
    })
    assert job_resp.status_code in (200, 201), "Job creation failed"
    job = job_resp.json()
    job_id = job.get("id") or job.get("job_id")

    client.put(f"/api/v1/hr/jobs/{job_id}/approve", headers=hr_headers, json={
        "salary_min": 40000, "salary_max": 80000, "location": "Remote",
    })

    # Apply as candidate
    apply_resp = client.post(
        f"/api/v1/candidate/jobs/{job_id}/apply",
        headers=candidate_headers,
        files={"resume": ("r.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")},
        data={
            "first_name": "Bug02",
            "last_name": "Test",
            "email": "candidate_test@test.com",
            "phone": "0000000000",
            "years_experience": "1",
        },
    )
    assert apply_resp.status_code in (200, 201), f"Apply failed: {apply_resp.text}"

    # Filter by job_id so we get the apply-created row, not the placeholder
    # auto-created at registration time.
    cand = db.query(Candidate).filter(
        Candidate.email == "candidate_test@test.com",
        Candidate.job_id == job_id,
    ).first()
    assert cand is not None, "Candidate row not found after applying"
    assert cand.company_id is not None, (
        "BUG-02 CONFIRMED: candidate.company_id is NULL after applying. "
        "Fix: routes/candidate_routes.py — add company_id=job.company_id to Candidate() constructor."
    )


# ── BUG-03: MCQ submit endpoint has NO authentication ───────────────────────

@pytest.mark.security
def test_mcq_submit_requires_auth(client):
    """
    BUG-03 (MEDIUM): POST /api/v1/evaluation/mcq/submit
    has no authentication. Anyone can submit answers for any interview_id,
    inflating or sabotaging test scores.

    Fix: Add `Depends(get_current_user)` to the endpoint in evaluation_routes.py.
    """
    resp = client.post(
        "/api/v1/evaluation/mcq/submit",
        json={
            "interview_id": 99999,
            "candidate_name": "Hacker",
            "candidate_email": "hacker@evil.com",
            "answers": [],
        },
        # No Authorization header
    )
    assert resp.status_code in (401, 403), (
        f"BUG-03 CONFIRMED: MCQ submit returned {resp.status_code} with no auth. "
        f"Expected 401/403. "
        f"Fix: add Depends(get_current_user) to routes/evaluation_routes.py MCQ submit endpoint."
    )


# ── BUG-04: Draft offer can be accepted/rejected ────────────────────────────

@pytest.mark.security
def test_draft_offer_not_respondable(client, db, candidate_headers):
    """
    BUG-04 (MEDIUM): The offer respond endpoint allows responding to offers
    with status='draft'. An offer should only be respondable after it's been
    formally sent (status='sent').

    Current code: `if status not in ('sent', 'draft')` — allows draft.
    Fix: Change to `if status != 'sent'` in routes/evaluation_routes.py line ~291.
    """
    # Insert a Candidate, JobPosting, and DRAFT Offer so the endpoint's JOIN succeeds.
    # The candidate row must use candidate_test@test.com so the auth-ownership
    # check in respond_to_offer passes and we actually reach the status check.
    from src.models.models import Candidate, JobPosting
    job_id = str(uuid.uuid4())
    cand_id = str(uuid.uuid4())
    offer_id = str(uuid.uuid4())

    db.add(JobPosting(
        id=job_id, title="Draft Test Job", description="x", requirements="x",
        location="Remote", employment_type="full_time", status="active",
    ))
    db.add(Candidate(
        id=cand_id, job_id=job_id,
        email="candidate_test@test.com", first_name="Cand", last_name="User",
    ))
    db.flush()

    db.execute(text(
        "INSERT IGNORE INTO offers (id, status, salary, job_id, candidate_id) "
        "VALUES (:oid, 'draft', 60000, :jid, :cid)"
    ), {"oid": offer_id, "jid": job_id, "cid": cand_id})
    db.commit()

    resp = client.post(
        f"/api/v1/evaluation/offers/{offer_id}/respond",
        headers=candidate_headers,
        json={"decision": "accept"},
    )
    assert resp.status_code == 400, (
        f"BUG-04 CONFIRMED: Draft offer can be accepted — got {resp.status_code}. "
        f"Expected 400. "
        f"Fix: change `status not in ('sent', 'draft')` to `status != 'sent'` "
        f"in routes/evaluation_routes.py."
    )


# ── BUG-05: SHA-256 password fallback is insecure ───────────────────────────

@pytest.mark.security
def test_no_sha256_fallback_login(client, db):
    """
    BUG-05 (LOW / roadmap S3): src/auth.py verify_password() has an except block
    that falls back to SHA-256 if bcrypt fails. SHA-256 without salt is cryptographically
    weak. If the fallback is triggered in production, accounts become vulnerable.

    This test stores a SHA-256 hash directly in the DB and verifies the system
    rejects the login (meaning the fallback is NOT being used for normal auth).

    Fix: Remove the except/hashlib block in src/auth.py verify_password().
    """
    import hashlib
    from src.models.models import User

    sha256_hash = hashlib.sha256("Pass@1234".encode()).hexdigest()
    user_id = str(uuid.uuid4())

    db.execute(text(
        "INSERT INTO users (id, email, password_hash, role, first_name, last_name, is_active) "
        "VALUES (:id, :email, :pw, 'candidate', 'SHA', 'User', 1)"
    ), {"id": user_id, "email": "sha256user@test.com", "pw": sha256_hash})
    db.commit()

    resp = client.post("/api/v1/auth/login", json={
        "email": "sha256user@test.com",
        "password": "Pass@1234",
    })
    assert resp.status_code in (400, 401), (
        f"BUG-05 CONFIRMED: SHA-256 hashed password was accepted — got {resp.status_code}. "
        f"The insecure fallback is active. "
        f"Fix: remove except/hashlib block in src/auth.py verify_password()."
    )


# ── R-04: Raw SQL errors exposed to clients ──────────────────────────────────

@pytest.mark.security
def test_error_response_not_raw_sql(client, admin_headers):
    """
    R-04 (roadmap S7 / Challenge-16): Internal server errors should return
    a generic message, not raw Python exception text with SQL/file paths.

    Trigger a server error by sending an invalid payload that forces a DB error.
    The response must NOT contain SQL keywords or Python traceback strings.
    """
    # Send clearly invalid data to a typed endpoint
    resp = client.post("/api/v1/admin/departments", headers=admin_headers, json={
        "name": None,  # null name should cause DB constraint error
    })

    if resp.status_code == 500:
        body = resp.text.lower()
        bad_strings = ["sqlalchemy", "traceback", "pymysql", "integrityerror",
                       "operationalerror", "file \""]
        for bad in bad_strings:
            assert bad not in body, (
                f"R-04 CONFIRMED: Raw error exposed — '{bad}' found in 500 response. "
                f"Fix: add global exception handler in main.py returning generic message."
            )


# ── R-07: Port hardcoded instead of reading $PORT ────────────────────────────

@pytest.mark.security
def test_main_py_reads_port_from_env():
    """
    R-07 (roadmap H4 / Challenge-1): main.py must read port from $PORT env var.
    Cloud platforms (Railway, Render, Heroku) assign a random port via $PORT.
    Hardcoding 8001 causes deployment failures on every cloud platform.

    Fix: `port=int(os.getenv("PORT", 8000))` in main.py uvicorn.run() call.
    """
    import ast
    import re

    with open("main.py", "r", encoding="utf-8") as f:
        content = f.read()

    # Check that uvicorn.run uses os.getenv or environ for port
    hardcoded = re.search(r'uvicorn\.run\(.*port\s*=\s*\d+', content, re.DOTALL)
    uses_env = re.search(r'uvicorn\.run\(.*port\s*=.*(?:os\.getenv|os\.environ|PORT)', content, re.DOTALL)

    assert not hardcoded or uses_env, (
        "R-07 CONFIRMED: main.py has hardcoded port in uvicorn.run(). "
        "Fix: port=int(os.getenv('PORT', 8000))"
    )
