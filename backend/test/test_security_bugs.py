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
    from src.models.models import Candidate, User

    # Put the candidate in the HR's company before applying. The stock fixtures do not
    # line up — hr_headers builds a fresh "Test Company" while a bare candidate
    # registration resolves to the platform org — and applying across that boundary is
    # exactly what BUG-09 closed. Production has no such gap: a candidate reaches a
    # company through its portal key or tenant domain and is assigned that company_id.
    # This test is about whether the Candidate row gets a company_id at all, so the
    # alignment restores its subject rather than weakening it.
    _hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    _cand = db.query(User).filter(User.email == "candidate_test@test.com").first()
    _cand.company_id = _hr.company_id
    db.flush()

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


# ── BUG-08: every /api/v1/ats route was reachable with no account ────────────

@pytest.mark.security
def test_ats_endpoints_require_auth(client):
    """
    BUG-08 (HIGH): the ATS router declared no dependencies and none of its seven
    endpoints declared one either, so all were callable anonymously. Two run work
    on caller-supplied text — /score does a sentence-transformer pass and
    /score/hybrid calls an LLM — so anyone who found the path could saturate a
    shared box for free.

    Fix: dependencies=[Depends(get_current_user)] on the router, the same way
         database_management_routes.py guards /api/v1/db.
    """
    scored = client.post("/api/v1/ats/score",
                         json={"resume_text": "python fastapi", "job_description": "python role"})
    assert scored.status_code in (401, 403), \
        f"/ats/score answered {scored.status_code} with no credentials"

    for path in ("/api/v1/ats/demo", "/api/v1/ats/metrics", "/api/v1/ats/system/status"):
        resp = client.get(path)
        assert resp.status_code in (401, 403), \
            f"{path} answered {resp.status_code} with no credentials"


# ── BUG-09: a candidate could apply into another tenant ──────────────────────

@pytest.mark.security
def test_cannot_apply_to_another_companys_job(client, db, hr_headers, candidate_headers):
    """
    BUG-09 (HIGH): POST /candidate/jobs/{id}/apply selected on `status == "active"`
    and named no company, so a candidate in one tenant could apply to another
    tenant's active job by id and get a 200 back. Measured, not assumed — removing
    the company clause makes this test fail with 200.

    Note the endpoint choice. An earlier version of this test used /quick-apply and
    was worthless: that endpoint 404s with "Candidate profile not found" for a
    candidate with no CandidateProfileExt, which every fixture candidate lacks. It
    asserted 404 and got 404 for an unrelated reason, and passed with the fix
    reverted. /apply takes its identity from form fields, so it reaches the job
    lookup and the assertion means something.
    """
    from src.models.models import User, JobPosting

    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    cand = db.query(User).filter(User.email == "candidate_test@test.com").first()
    assert hr and cand
    assert hr.company_id != cand.company_id, \
        "this test is meaningless unless the fixtures sit in different companies"

    job = JobPosting(id=str(uuid.uuid4()), company_id=hr.company_id,
                     title="Other Co Role", description="d", requirements="r",
                     status="active")
    db.add(job)
    db.flush()

    resp = client.post(
        f"/api/v1/candidate/jobs/{job.id}/apply",
        headers=candidate_headers,
        files={"resume": ("r.pdf", io.BytesIO(b"%PDF fake"), "application/pdf")},
        data={
            "first_name": "Cross", "last_name": "Tenant",
            "email": "candidate_test@test.com", "phone": "0000000000",
            "years_experience": "1",
        },
    )
    assert resp.status_code == 404, \
        f"cross-tenant apply returned {resp.status_code}: {resp.text}"


# ── BUG-10: the backup feature produced unrestorable files ───────────────────

@pytest.mark.security
def test_backup_refuses_rather_than_writing_an_unrestorable_file(tmp_path, monkeypatch):
    """
    BUG-10 (HIGH): backup_database built SQL by interpolating Python's tuple repr —
    no CREATE TABLE at all, None where NULL belongs, datetime.datetime(...) and
    Decimal('1.5') as literals, a trailing comma on every single-column row, and no
    column list. Every file it produced was unrestorable, which is worse than having
    no backup because it stops you taking a real one.

    It now shells out to mysqldump. The property under test is the one that makes
    that safe: with no mysqldump available it must refuse loudly and leave nothing
    behind, never fall back to writing something that merely looks like a backup.
    """
    import os
    import shutil as _shutil
    from src.config.database_config_mysql import db_manager

    monkeypatch.delenv("MYSQLDUMP_PATH", raising=False)
    monkeypatch.setattr(_shutil, "which", lambda *a, **k: None)

    target = str(tmp_path / "backup_should_not_exist.sql")
    with pytest.raises(RuntimeError, match="mysqldump"):
        db_manager.backup_database(target)

    assert not os.path.exists(target), "a failed backup must not leave a file behind"


@pytest.mark.security
def test_backup_invokes_mysqldump_safely(tmp_path, monkeypatch):
    """The positive case, with mysqldump stubbed so it needs no server.

    An earlier version ran the real binary against db_manager, which points at
    DATABASE_URL rather than the test database — so it depended on a live server
    holding the app's production schema. CI had mysqldump installed but no such
    database, so it failed rather than skipping. A test whose result depends on
    the machine tells you about the machine, not the code.

    What is worth pinning is the invocation, and that is fully checkable here: the
    flags a restorable dump needs, and that the password reaches mysqldump through
    the environment rather than argv, where `ps` would show it to every user on a
    shared host.
    """
    import subprocess as _sp
    from src.config import database_config_mysql as mod

    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["env"] = kwargs.get("env") or {}
        # Stand in for mysqldump: write what it would write, trailer included.
        dest = next(a.split("=", 1)[1] for a in argv if a.startswith("--result-file="))
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("CREATE TABLE t (id int);\n-- Dump completed on 2026-08-13\n")
        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.delenv("MYSQLDUMP_PATH", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda *a, **k: "/usr/bin/mysqldump")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    target = str(tmp_path / "backup_real.sql")
    assert mod.db_manager.backup_database(target) == target

    argv = seen["argv"]
    for flag in ("--single-transaction", "--no-tablespaces",
                 "--default-character-set=utf8mb4", "--protocol=TCP"):
        assert flag in argv, f"{flag} missing from invocation: {argv}"

    password = mod.db_manager.engine.url.password
    if password:
        assert not any(str(password) in a for a in argv), \
            "password leaked into argv, where ps would expose it"
        assert seen["env"].get("MYSQL_PWD") == str(password), \
            "password must reach mysqldump through MYSQL_PWD"


@pytest.mark.security
def test_backup_rejects_a_truncated_dump(tmp_path, monkeypatch):
    """mysqldump writes its trailer only on success, so a dump without one is
    truncated. It must be treated as a failure and the file removed, or a partial
    dump sits there wearing a backup's name."""
    import os
    import subprocess as _sp
    from src.config import database_config_mysql as mod

    def fake_run(argv, **kwargs):
        dest = next(a.split("=", 1)[1] for a in argv if a.startswith("--result-file="))
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("CREATE TABLE t (id int);\n-- cut off here")   # no trailer
        return _sp.CompletedProcess(argv, 0, "", "")

    monkeypatch.delenv("MYSQLDUMP_PATH", raising=False)
    monkeypatch.setattr(mod.shutil, "which", lambda *a, **k: "/usr/bin/mysqldump")
    monkeypatch.setattr(mod.subprocess, "run", fake_run)

    target = str(tmp_path / "truncated.sql")
    with pytest.raises(RuntimeError, match="truncated"):
        mod.db_manager.backup_database(target)
    assert not os.path.exists(target), "a truncated dump must not be left behind"
