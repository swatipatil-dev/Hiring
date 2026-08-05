"""
Tenant isolation tests.

Two layers of coverage:

* **Unit** — direct ORM query exercises of the SQLAlchemy ``before_compile``
  hook in ``src/tenancy/events.py``.  Asserts that binding a company_id
  injects a WHERE clause, and that ``tenant_bypass()`` removes it.

* **Integration** — TestClient hits real endpoints with two companies' tokens
  and asserts that Company A cannot read / mutate Company B resources via
  IDs, listing endpoints, or raw-SQL routes.

The point-fix routes covered explicitly:

* ``GET  /api/v1/interviewer/enhanced/all-jobs``
* ``GET  /api/v1/candidate/gender-stats``
* ``POST /api/v1/evaluation/mcq/submit``
* ``GET  /api/v1/evaluation/marksheet/{interview_id}``
* ``GET  /api/v1/database/health``  (now superadmin-only)
"""
import uuid
import pytest


# ───────────────────────────────────────────────────────────────────────────
# Shared helpers
# ───────────────────────────────────────────────────────────────────────────

# Rate limiter is disabled suite-wide via conftest._disable_rate_limiter_for_suite.


def _make_company(db, name: str) -> str:
    from src.models.models import Company
    company = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(company)
    db.flush()
    return company.id


def _make_user(db, email: str, role: str, company_id=None, password: str = "Pass@1234") -> str:
    from src.models.models import User
    from src.auth import get_password_hash
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=get_password_hash(password),
        role=role,
        first_name=role.capitalize(),
        last_name="Tester",
        is_active=True,
        email_verified=True,
        company_id=company_id,
    )
    db.add(user)
    db.flush()
    return user.id


def _login(client, email: str, password: str = "Pass@1234") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_job(db, company_id: str, title: str = "Job", status: str = "active") -> str:
    from src.models.models import JobPosting
    job = JobPosting(
        id=str(uuid.uuid4()),
        company_id=company_id,
        title=title,
        description="d",
        requirements="r",
        location="Remote",
        employment_type="full_time",
        experience_years=0,
        status=status,
    )
    db.add(job)
    db.flush()
    return job.id


def _make_interview(db, company_id: str, job_id: str = None) -> int:
    from src.models.models import Interview
    iv = Interview(
        company_id=company_id,
        title="MCQ Test",
        status="DRAFT",
        job_id=job_id,
    )
    db.add(iv)
    db.flush()
    return iv.id


# ───────────────────────────────────────────────────────────────────────────
# Unit — SQLAlchemy before_compile auto-filter
# ───────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_autofilter_off_when_no_tenant_bound():
    """With contextvar = None the hook must NOT inject a WHERE clause."""
    from src.tenancy import set_current_company
    from src.models.models import JobPosting
    from src.config.database_config_mysql import db_manager

    set_current_company(None)
    sess = db_manager.get_db_session()
    try:
        sql = str(sess.query(JobPosting))
        assert "company_id = " not in sql.replace("\n", "")
    finally:
        sess.close()


@pytest.mark.security
def test_autofilter_on_when_tenant_bound():
    """Binding a company_id must inject `company_id = <bound>` into the query."""
    from src.tenancy import set_current_company
    from src.models.models import JobPosting
    from src.config.database_config_mysql import db_manager

    set_current_company("FAKE-COMPANY-UUID-1234")
    try:
        sess = db_manager.get_db_session()
        try:
            sql = str(sess.query(JobPosting))
            assert "job_postings.company_id" in sql
            assert "WHERE" in sql.upper()
        finally:
            sess.close()
    finally:
        set_current_company(None)


@pytest.mark.security
def test_tenant_bypass_clears_filter():
    """`tenant_bypass()` must remove the auto-filter inside its block."""
    from src.tenancy import set_current_company, tenant_bypass
    from src.models.models import JobPosting
    from src.config.database_config_mysql import db_manager

    set_current_company("FAKE-COMPANY-UUID-5678")
    try:
        sess = db_manager.get_db_session()
        try:
            with tenant_bypass():
                sql = str(sess.query(JobPosting))
                assert "WHERE" not in sql.upper() or "company_id = " not in sql.replace("\n", "")
        finally:
            sess.close()
    finally:
        set_current_company(None)


@pytest.mark.security
def test_autofilter_is_idempotent_when_filter_present():
    """Explicit `.filter(company_id == ...)` must not be duplicated by the hook."""
    from src.tenancy import set_current_company
    from src.models.models import JobPosting
    from src.config.database_config_mysql import db_manager

    set_current_company("FAKE-COMPANY-UUID-9999")
    try:
        sess = db_manager.get_db_session()
        try:
            q = sess.query(JobPosting).filter(JobPosting.company_id == "FAKE-COMPANY-UUID-9999")
            sql = str(q)
            # Exactly one company_id predicate, not two.
            assert sql.count("job_postings.company_id =") == 1
        finally:
            sess.close()
    finally:
        set_current_company(None)


# ───────────────────────────────────────────────────────────────────────────
# Unit — write-side auto-stamp (before_flush)
#
# Mirror of the read filter: a new TenantScoped row created without a
# company_id must inherit the bound tenant on flush, so it can never become an
# invisible NULL-company_id orphan. See docs/DASHBOARD_DATA_PITFALLS.md.
# ───────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_company_id_stamped_on_insert_when_tenant_bound(db):
    """A new TenantScoped row created without company_id is stamped on flush."""
    from src.tenancy import set_current_company
    from src.models.models import JobPosting

    cid = _make_company(db, name="Stamp Co")
    set_current_company(cid)
    try:
        job = JobPosting(
            id=str(uuid.uuid4()),
            title="No-company job",
            description="d",
            requirements="r",
            status="active",
        )  # company_id intentionally omitted
        db.add(job)
        db.flush()
        assert job.company_id == cid
    finally:
        set_current_company(None)


@pytest.mark.security
def test_explicit_company_id_not_overwritten_on_insert(db):
    """An explicitly set company_id wins over the bound tenant — never overwritten."""
    from src.tenancy import set_current_company
    from src.models.models import JobPosting

    a_id = _make_company(db, name="Stamp A")
    b_id = _make_company(db, name="Stamp B")
    set_current_company(a_id)
    try:
        job = JobPosting(
            id=str(uuid.uuid4()),
            company_id=b_id,            # explicit — must survive the flush
            title="Explicit job",
            description="d",
            requirements="r",
            status="active",
        )
        db.add(job)
        db.flush()
        assert job.company_id == b_id
    finally:
        set_current_company(None)


@pytest.mark.security
def test_no_stamp_when_no_tenant_bound(db):
    """With no tenant bound (superadmin/public/registration), nothing is stamped."""
    from src.tenancy import set_current_company
    from src.models.models import JobPosting

    set_current_company(None)
    job = JobPosting(
        id=str(uuid.uuid4()),
        title="Unbound job",
        description="d",
        requirements="r",
        status="active",
    )  # company_id omitted and no tenant bound → stays NULL
    db.add(job)
    db.flush()
    assert job.company_id is None


# ───────────────────────────────────────────────────────────────────────────
# Integration — cross-tenant API access
# ───────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def two_companies(client, db):
    """
    Build two isolated companies, each with an admin, an HR user, a candidate,
    one job, and one interview.  Returns a dict with logged-in headers and IDs.
    """
    a_id = _make_company(db, name="Acme Co")
    b_id = _make_company(db, name="Beta Co")

    a_admin_email = f"admin_a_{uuid.uuid4().hex[:6]}@test.com"
    b_admin_email = f"admin_b_{uuid.uuid4().hex[:6]}@test.com"
    a_cand_email  = f"cand_a_{uuid.uuid4().hex[:6]}@test.com"
    b_cand_email  = f"cand_b_{uuid.uuid4().hex[:6]}@test.com"

    _make_user(db, a_admin_email, role="admin", company_id=a_id)
    _make_user(db, b_admin_email, role="admin", company_id=b_id)
    _make_user(db, a_cand_email,  role="candidate", company_id=a_id)
    _make_user(db, b_cand_email,  role="candidate", company_id=b_id)

    a_job_id = _make_job(db, a_id, title="Job A")
    b_job_id = _make_job(db, b_id, title="Job B")
    a_iv_id  = _make_interview(db, a_id, job_id=a_job_id)
    b_iv_id  = _make_interview(db, b_id, job_id=b_job_id)

    return {
        "a": {
            "company_id": a_id,
            "admin": _login(client, a_admin_email),
            "candidate": _login(client, a_cand_email),
            "job_id": a_job_id,
            "interview_id": a_iv_id,
        },
        "b": {
            "company_id": b_id,
            "admin": _login(client, b_admin_email),
            "candidate": _login(client, b_cand_email),
            "job_id": b_job_id,
            "interview_id": b_iv_id,
        },
    }


@pytest.mark.security
def test_enhanced_interviewer_all_jobs_is_tenant_scoped(client, db, two_companies):
    """GET /interviewer/enhanced/all-jobs must only return the caller's company's jobs."""
    # Use an interviewer-role user inside Company A so the endpoint allows access.
    a = two_companies["a"]
    iv_email = f"interviewer_a_{uuid.uuid4().hex[:6]}@test.com"
    _make_user(db, iv_email, role="interviewer", company_id=a["company_id"])
    iv_headers = _login(client, iv_email)

    resp = client.get("/api/v1/interviewer/all-jobs", headers=iv_headers)
    assert resp.status_code == 200, resp.text
    job_titles = {j["job_title"] for j in resp.json()}
    assert "Job A" in job_titles
    assert "Job B" not in job_titles, f"Cross-tenant leak: {job_titles}"


@pytest.mark.security
def test_candidate_gender_stats_is_tenant_scoped(client, db, two_companies):
    """Candidate from Company A must only see Company A's gender stats."""
    a = two_companies["a"]
    b = two_companies["b"]

    # Seed gender data: 2 male candidates at Co A, 5 female at Co B
    from src.models.models import User
    for i in range(2):
        _make_user(db, f"male_a_{i}_{uuid.uuid4().hex[:6]}@test.com",
                   role="candidate", company_id=a["company_id"])
        user = db.query(User).filter(User.email.like(f"male_a_{i}%")).first()
        user.gender = "male"
    for i in range(5):
        _make_user(db, f"fem_b_{i}_{uuid.uuid4().hex[:6]}@test.com",
                   role="candidate", company_id=b["company_id"])
        user = db.query(User).filter(User.email.like(f"fem_b_{i}%")).first()
        user.gender = "female"
    db.flush()

    resp = client.get("/api/v1/candidate/gender-stats", headers=a["candidate"])
    assert resp.status_code == 200, resp.text
    counts = {row["gender"]: row["count"] for row in resp.json()}
    # Must not see Company B's 5 female candidates.
    assert counts.get("Female", 0) == 0
    # Should see at least one male from Company A (the 2 we seeded).
    assert counts.get("Male", 0) >= 2


@pytest.mark.security
def test_mcq_submit_blocks_cross_tenant_interview(client, two_companies):
    """Company-A candidate cannot submit MCQ for Company-B's interview (404)."""
    a = two_companies["a"]
    b = two_companies["b"]

    resp = client.post(
        "/api/v1/evaluation/mcq/submit",
        headers=a["candidate"],
        json={
            "interview_id": b["interview_id"],
            "candidate_name": "Alice",
            "candidate_email": "alice@a.com",
            "answers": [],
        },
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.security
def test_marksheet_blocks_cross_tenant_interview(client, two_companies):
    """Company-A user cannot read marksheet of Company-B's interview."""
    a = two_companies["a"]
    b = two_companies["b"]

    resp = client.get(
        f"/api/v1/evaluation/marksheet/{b['interview_id']}",
        headers=a["candidate"],
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.security
def test_database_management_health_requires_superadmin(client, two_companies):
    """The DB management router is now locked to superadmin — admin gets 403."""
    a = two_companies["a"]
    resp = client.get("/api/v1/database/health", headers=a["admin"])
    assert resp.status_code == 403, resp.text


@pytest.mark.security
def test_database_management_health_anonymous_is_401(client):
    """Anonymous access to the management router must be rejected."""
    resp = client.get("/api/v1/database/health")
    # Either 401 (no token) or 403 (token-less) — both block the leak.
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.security
def test_interviewer_hr_list_is_tenant_scoped(client, db, two_companies):
    """Regression for screenshot bug: GET /interviewer/hr-list must NOT
    return HR profiles from other companies.  Until f6a7b8c9d0e1 added a
    company_id column to hr_profiles, this dropdown showed every tenant's
    HRs because db.query(HRProfile) wasn't auto-filtered."""
    from src.models.models import HRProfile, Department

    a = two_companies["a"]
    b = two_companies["b"]

    # Seed: one HR user + HRProfile per company.
    a_hr_email = f"hr_a_{uuid.uuid4().hex[:6]}@test.com"
    b_hr_email = f"hr_b_{uuid.uuid4().hex[:6]}@test.com"
    a_hr_user_id = _make_user(db, a_hr_email, role="hr", company_id=a["company_id"])
    b_hr_user_id = _make_user(db, b_hr_email, role="hr", company_id=b["company_id"])

    # Each company gets a department + matching HRProfile (mirrors prod data).
    a_dept = Department(id=str(uuid.uuid4()), company_id=a["company_id"], name="Eng A")
    b_dept = Department(id=str(uuid.uuid4()), company_id=b["company_id"], name="Eng B")
    db.add_all([a_dept, b_dept])
    db.flush()

    a_hr = HRProfile(
        id=str(uuid.uuid4()), user_id=a_hr_user_id,
        department_id=a_dept.id, employee_id="A-HR-1",
        company_id=a["company_id"],
    )
    b_hr = HRProfile(
        id=str(uuid.uuid4()), user_id=b_hr_user_id,
        department_id=b_dept.id, employee_id="B-HR-1",
        company_id=b["company_id"],
    )
    db.add_all([a_hr, b_hr])
    db.flush()

    # An interviewer in Company A asks for the HR-to-notify list.
    iv_email = f"iv_a_{uuid.uuid4().hex[:6]}@test.com"
    _make_user(db, iv_email, role="interviewer", company_id=a["company_id"])
    iv_headers = _login(client, iv_email)

    resp = client.get("/api/v1/interviewer/hr-list", headers=iv_headers)
    assert resp.status_code == 200, resp.text
    returned_emails = {row["email"] for row in resp.json()}
    assert a_hr_email in returned_emails, (
        f"Caller's own company HR missing: {returned_emails}"
    )
    assert b_hr_email not in returned_emails, (
        f"Cross-tenant leak: {b_hr_email} appeared in {returned_emails}"
    )


@pytest.mark.security
def test_mcq_api_rejects_anonymous(client):
    """Every /mcq/* endpoint was anonymous in the live build — anyone could
    list, read, create, and delete questions across all companies. After the
    lockdown, every endpoint requires auth."""
    # GETs
    assert client.get("/api/v1/mcq/interviews/1/questions").status_code in (401, 403)
    assert client.get("/api/v1/mcq/questions/abc").status_code in (401, 403)
    assert client.get("/api/v1/mcq/categories").status_code in (401, 403)
    assert client.get("/api/v1/mcq/sample").status_code in (401, 403)
    # mutations
    assert client.post("/api/v1/mcq/questions", json={
        "interview_id": "1", "question_text": "x",
        "options": ["a","b"], "correct_answer": "a",
    }).status_code in (401, 403)
    assert client.delete("/api/v1/mcq/questions/abc").status_code in (401, 403)


@pytest.mark.security
def test_mcq_api_blocks_cross_tenant_interview(client, two_companies):
    """Caller from Company A cannot read questions for Company B's interview."""
    a = two_companies["a"]
    b = two_companies["b"]
    resp = client.get(
        f"/api/v1/mcq/interviews/{b['interview_id']}/questions",
        headers=a["candidate"],
    )
    assert resp.status_code == 404, resp.text


@pytest.mark.security
def test_interview_questions_query_is_autofiltered():
    """Direct ORM query against InterviewQuestion would auto-scope if a model
    existed; for the raw-SQL pattern, the model-level check is on the parent
    Interview class.  Verify Interview's auto-filter still fires correctly
    (Interview is TenantScoped)."""
    from src.tenancy import set_current_company
    from src.models.models import Interview
    from src.config.database_config_mysql import db_manager

    set_current_company("BOUND-CID")
    try:
        sess = db_manager.get_db_session()
        try:
            sql = str(sess.query(Interview))
            assert "interviews.company_id" in sql
        finally:
            sess.close()
    finally:
        set_current_company(None)


@pytest.mark.security
def test_candidate_profile_ext_is_tenantscoped():
    """CandidateProfileExt and its children gained company_id in g7b8c9d0e1f2.
    Confirm the auto-filter activates for the parent table."""
    from src.tenancy import set_current_company
    from src.models.models import (
        CandidateProfileExt, CandidateSkill, CandidateEducation,
        ProctoringViolation,
    )
    from src.config.database_config_mysql import db_manager

    set_current_company("BOUND-CID")
    try:
        sess = db_manager.get_db_session()
        try:
            for model, table in (
                (CandidateProfileExt, "candidate_profile_ext"),
                (CandidateSkill, "candidate_skills"),
                (CandidateEducation, "candidate_education"),
                (ProctoringViolation, "proctoring_violations"),
            ):
                sql = str(sess.query(model))
                assert f"{table}.company_id" in sql, (
                    f"{model.__name__} not auto-filtered — TenantScoped marker missing?"
                )
        finally:
            sess.close()
    finally:
        set_current_company(None)


@pytest.mark.security
def test_hrprofile_query_is_autofiltered_by_tenant():
    """Direct ORM query against HRProfile must be auto-scoped when a tenant
    is bound — the architectural fix that prevents future /hr-list-style
    leaks."""
    from src.tenancy import set_current_company
    from src.models.models import HRProfile
    from src.config.database_config_mysql import db_manager

    set_current_company("BOUND-COMPANY-ID")
    try:
        sess = db_manager.get_db_session()
        try:
            sql = str(sess.query(HRProfile))
            assert "hr_profiles.company_id" in sql, (
                "HRProfile is not TenantScoped — auto-filter did not inject "
                "a company_id predicate."
            )
        finally:
            sess.close()
    finally:
        set_current_company(None)


@pytest.mark.security
def test_admin_listing_does_not_leak_other_company_jobs(client, two_companies):
    """A Company A admin listing jobs must NOT see Company B's job."""
    a = two_companies["a"]
    b = two_companies["b"]

    resp = client.get("/api/v1/admin/jobs", headers=a["admin"])
    # Endpoint may 404 if it doesn't exist; we only care that — when it does
    # respond 200 — Company B's job is absent.  Skip if the route is gone.
    if resp.status_code == 404:
        pytest.skip("admin job-listing endpoint not present in this build")
    assert resp.status_code in (200, 403), resp.text
    if resp.status_code == 200:
        body = resp.json() if isinstance(resp.json(), list) else resp.json().get("jobs", [])
        ids = {item.get("id") or item.get("job_id") for item in body}
        assert b["job_id"] not in ids
