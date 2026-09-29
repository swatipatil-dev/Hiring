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
# Unit — read-side auto-filter (do_orm_execute + with_loader_criteria)
#
# These run real queries against rows in two companies and assert on which rows
# come back. They used to assert on str(query), which passed while the filter
# missed db.get(), select(), lazy loads and bulk deletes entirely.
# ───────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def bound_tenant():
    """Bind a tenant for the duration of a test and always unbind afterwards."""
    from src.tenancy import set_current_company

    def _bind(company_id):
        set_current_company(company_id)

    yield _bind
    set_current_company(None)


@pytest.fixture()
def two_tenant_rows(db):
    """One company, job and HR profile per tenant, created with no tenant bound."""
    from src.models.models import HRProfile
    from src.tenancy import set_current_company

    set_current_company(None)
    a_id = _make_company(db, name="Filter A")
    b_id = _make_company(db, name="Filter B")
    a_job = _make_job(db, a_id, title="A job")
    b_job = _make_job(db, b_id, title="B job")
    a_hr = _make_user(db, f"hr_a_{uuid.uuid4().hex[:6]}@t.com", "hr", company_id=a_id)
    b_hr = _make_user(db, f"hr_b_{uuid.uuid4().hex[:6]}@t.com", "hr", company_id=b_id)
    for uid, cid in ((a_hr, a_id), (b_hr, b_id)):
        db.add(HRProfile(id=str(uuid.uuid4()), user_id=uid, company_id=cid,
                         employee_id=f"E-{uuid.uuid4().hex[:8]}"))
    db.flush()
    db.expire_all()
    return {"a": a_id, "b": b_id, "a_job": a_job, "b_job": b_job,
            "a_hr": a_hr, "b_hr": b_hr}


@pytest.mark.security
def test_autofilter_off_when_no_tenant_bound(db, two_tenant_rows):
    from src.models.models import JobPosting
    ids = {j.id for j in db.query(JobPosting).all()}
    assert {two_tenant_rows["a_job"], two_tenant_rows["b_job"]} <= ids


@pytest.mark.security
def test_autofilter_scopes_every_orm_access_path(db, two_tenant_rows, bound_tenant):
    """Every way the ORM can load a row is scoped, not just db.query()."""
    from sqlalchemy import select, func
    from src.models.models import JobPosting, HRProfile, User

    t = two_tenant_rows
    bound_tenant(t["a"])

    assert db.query(JobPosting).filter(JobPosting.id == t["b_job"]).first() is None
    assert db.get(JobPosting, t["b_job"]) is None
    assert db.query(JobPosting).get(t["b_job"]) is None
    assert db.execute(
        select(JobPosting).where(JobPosting.id == t["b_job"])
    ).scalars().first() is None
    listed = {j.id for j in db.scalars(select(JobPosting)).all()}
    assert t["a_job"] in listed and t["b_job"] not in listed
    assert db.query(func.count(JobPosting.id)).scalar() == 1
    # Column-only queries and joins on another scoped model.
    assert (t["b_job"],) not in db.query(JobPosting.id).all()
    joined = db.query(HRProfile).join(User, User.id == HRProfile.user_id).all()
    assert {p.user_id for p in joined} == {t["a_hr"]}
    # Own rows still load.
    assert db.get(JobPosting, t["a_job"]) is not None


@pytest.mark.security
def test_autofilter_blocks_bulk_update_and_delete(db, two_tenant_rows, bound_tenant):
    """A bulk .update()/.delete() by id must not touch another tenant's row."""
    from src.models.models import JobPosting
    from src.tenancy import set_current_company

    t = two_tenant_rows
    bound_tenant(t["a"])
    assert db.query(JobPosting).filter(JobPosting.id == t["b_job"]).update(
        {"title": "hijacked"}, synchronize_session=False) == 0
    assert db.query(JobPosting).filter(JobPosting.id == t["b_job"]).delete(
        synchronize_session=False) == 0

    set_current_company(None)
    db.expire_all()
    assert db.get(JobPosting, t["b_job"]).title == "B job"


@pytest.mark.security
def test_autofilter_ignores_explicit_other_company_filter(db, two_tenant_rows, bound_tenant):
    """Naming another company's id in a filter must not escape the tenant."""
    from src.models.models import JobPosting
    t = two_tenant_rows
    bound_tenant(t["a"])
    assert db.query(JobPosting).filter(JobPosting.company_id == t["b"]).all() == []


@pytest.mark.security
def test_autofilter_scopes_relationship_lazy_loads(db, two_tenant_rows, bound_tenant):
    """A relationship reached from a bypass-loaded row doesn't pull another tenant's row."""
    from src.models.models import User
    from src.tenancy import tenant_bypass

    t = two_tenant_rows
    bound_tenant(t["a"])
    with tenant_bypass():
        b_user = db.get(User, t["b_hr"])
    # Loaded under bypass; the lazy load of its profile runs with tenant A bound.
    assert b_user.hr_profile is None
    assert db.get(User, t["a_hr"]).hr_profile is not None


@pytest.mark.security
def test_autofilter_does_not_break_refresh_of_bypass_loaded_row(db, two_tenant_rows, bound_tenant):
    """Expiring a row loaded under tenant_bypass() must not raise ObjectDeletedError."""
    from src.models.models import JobPosting
    from src.tenancy import tenant_bypass

    t = two_tenant_rows
    bound_tenant(t["a"])
    with tenant_bypass():
        b_job = db.get(JobPosting, t["b_job"])
    db.expire(b_job)
    assert b_job.title == "B job"


@pytest.mark.security
def test_tenant_filter_switches_company(db, two_tenant_rows, bound_tenant):
    """The cached lambda criteria must pick up the newly bound company each time."""
    from src.models.models import JobPosting
    t = two_tenant_rows
    bound_tenant(t["a"])
    assert {j.id for j in db.query(JobPosting).all()} == {t["a_job"]}
    bound_tenant(t["b"])
    db.expire_all()
    assert {j.id for j in db.query(JobPosting).all()} == {t["b_job"]}


@pytest.mark.security
def test_tenant_bypass_clears_filter(db, two_tenant_rows, bound_tenant):
    from src.models.models import JobPosting
    from src.tenancy import tenant_bypass
    t = two_tenant_rows
    bound_tenant(t["a"])
    with tenant_bypass():
        assert db.get(JobPosting, t["b_job"]) is not None
    assert db.query(JobPosting).execution_options(_tenant_bypass=True).filter(
        JobPosting.id == t["b_job"]).first() is not None


@pytest.mark.security
def test_tenant_binding_reaches_endpoint(db, two_tenant_rows):
    """get_current_user must bind the tenant where the endpoint can see it.

    It was a sync dependency; FastAPI ran it in a threadpool on a copy of the
    context and discarded the value, so the filter never ran in any request.
    Checked for sync and async endpoints and through require_role.
    """
    from fastapi import FastAPI, Depends
    from fastapi.testclient import TestClient
    from src.auth import get_current_user, require_role, create_access_token
    from src.config.database_config_mysql import get_db
    from src.models.models import JobPosting
    from src.tenancy import get_current_company

    mini = FastAPI()

    @mini.get("/sync")
    def _sync(u=Depends(get_current_user), s=Depends(get_db)):
        return {"bound": get_current_company(),
                "jobs": [j.id for j in s.query(JobPosting).all()]}

    @mini.get("/async")
    async def _async(u=Depends(require_role(["hr"]))):
        return {"bound": get_current_company()}

    def _db():
        yield db

    mini.dependency_overrides[get_db] = _db
    t = two_tenant_rows
    headers = {"Authorization": f"Bearer {create_access_token({'sub': t['a_hr']})}"}
    with TestClient(mini) as c:
        body = c.get("/sync", headers=headers).json()
        assert body["bound"] == t["a"]
        assert body["jobs"] == [t["a_job"]]
        assert c.get("/async", headers=headers).json()["bound"] == t["a"]


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
    """Company-A staff cannot submit MCQ for Company-B's interview (404).

    (Candidates can't call this endpoint at all — it returns the answer key; see
    test_question_access.py.)"""
    a = two_companies["a"]
    b = two_companies["b"]

    resp = client.post(
        "/api/v1/evaluation/mcq/submit",
        headers=a["admin"],
        json={
            "interview_id": b["interview_id"],
            "candidate_name": "Alice",
            "candidate_email": "alice@a.com",
            "answers": [],
        },
    )
    assert resp.status_code == 404, resp.text


def _make_mcq_candidate_and_question(db, company_id, job_id, interview_id, email):
    """Attach a real candidate + one MCQ question to an interview.

    The MCQ submit handler 404s when an interview has no questions, so a test
    that needs to reach the email path has to seed one.
    """
    from sqlalchemy import text as _text
    from src.models.models import Candidate, Interview

    cand = Candidate(
        id=str(uuid.uuid4()), company_id=company_id, job_id=job_id,
        email=email, first_name="Real", last_name="Candidate",
        current_stage="shortlisted",
    )
    db.add(cand)
    db.flush()

    db.query(Interview).filter(Interview.id == interview_id).update(
        {"candidate_id": cand.id}
    )

    qid = str(uuid.uuid4())
    db.execute(_text(
        "INSERT INTO interview_questions "
        "(id, interview_id, question_text, question_type, difficulty_level, "
        " correct_answer, points) "
        "VALUES (:id, :iid, '2+2?', 'MCQ', 'EASY', '4', 10)"
    ), {"id": qid, "iid": interview_id})
    db.flush()
    return cand, qid


@pytest.mark.security
def test_mcq_submit_ignores_client_supplied_recipient(client, db, two_companies, monkeypatch):
    """The result email goes to the interview's candidate, not the request body.

    Regression for the authenticated open relay: the handler used to mail
    whatever ``candidate_email`` the submitter sent, so any signed-in user could
    have the tenant's SMTP identity deliver to an arbitrary address with company
    branding and aligned SPF/DKIM.
    """
    import routes.evaluation_routes as ev

    a = two_companies["a"]
    real_email = f"real_{uuid.uuid4().hex[:6]}@a.com"
    _, qid = _make_mcq_candidate_and_question(
        db, a["company_id"], a["job_id"], a["interview_id"], real_email
    )

    captured = {}
    monkeypatch.setattr(
        ev, "send_mcq_result",
        lambda to, name, *args, **kwargs: captured.update(to=to, name=name)
                                          or {"sent": True},
    )

    resp = client.post(
        "/api/v1/evaluation/mcq/submit",
        headers=a["admin"],
        json={
            "interview_id": a["interview_id"],
            "candidate_name": "Attacker Display Name",
            "candidate_email": "attacker@evil.com",
            "answers": [{"question_id": qid, "selected_answer": "4"}],
        },
    )
    assert resp.status_code == 200, resp.text

    assert captured.get("to") == real_email, (
        f"MCQ result mailed to {captured.get('to')!r} — the client-supplied "
        f"address was trusted. Recipient must come from interview.candidate_id."
    )
    assert captured["to"] != "attacker@evil.com"
    assert "Attacker" not in (captured.get("name") or "")
    assert resp.json()["email_sent"] is True


@pytest.mark.security
def test_mcq_submit_sends_nothing_when_interview_has_no_candidate(
    client, db, two_companies, monkeypatch
):
    """No candidate on the interview means no mail — and no sender resolution.

    Falling through to ``resolve_sender(db, None, ...)`` would reach the
    platform's env SMTP identity, letting an unattached interview borrow the
    platform's sending reputation.
    """
    from sqlalchemy import text as _text
    import routes.evaluation_routes as ev

    a = two_companies["a"]
    qid = str(uuid.uuid4())
    db.execute(_text(
        "INSERT INTO interview_questions "
        "(id, interview_id, question_text, question_type, difficulty_level, "
        " correct_answer, points) "
        "VALUES (:id, :iid, '2+2?', 'MCQ', 'EASY', '4', 10)"
    ), {"id": qid, "iid": a["interview_id"]})
    db.flush()

    calls = []
    monkeypatch.setattr(ev, "send_mcq_result",
                        lambda *args, **kwargs: calls.append(args) or {"sent": True})
    monkeypatch.setattr(ev, "resolve_sender",
                        lambda *a_, **k_: pytest.fail("resolve_sender must not run"))

    resp = client.post(
        "/api/v1/evaluation/mcq/submit",
        headers=a["admin"],
        json={
            "interview_id": a["interview_id"],
            "candidate_name": "Attacker",
            "candidate_email": "attacker@evil.com",
            "answers": [{"question_id": qid, "selected_answer": "4"}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert calls == [], "mail was sent for an interview with no candidate"
    assert resp.json()["email_sent"] is False
    assert resp.json()["email_status"] == "skipped"


@pytest.mark.security
def test_marksheet_blocks_cross_tenant_interview(client, two_companies):
    """Company-A user cannot read marksheet of Company-B's interview.

    Staff-only: a candidate is refused outright, and another company's admin gets 404.
    """
    a = two_companies["a"]
    b = two_companies["b"]

    url = f"/api/v1/evaluation/marksheet/{b['interview_id']}"
    assert client.get(url, headers=a["candidate"]).status_code == 403
    resp = client.get(url, headers=a["admin"])
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
