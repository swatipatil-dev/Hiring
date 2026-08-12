"""Resume pool: importing registered portal candidates into the HR Resume Matcher.

The matcher used to show only resumes HR uploaded through it, so anyone who
registered on the candidate portal was invisible. These cover the import that
closes that gap, plus the two guards it depends on:

* Move to ATS may run more than once for DIFFERENT jobs (an imported person is
  usually already a Candidate for the job they applied to themselves), but never
  twice for the same one.
* Deleting a pool row must not delete a resume file the pool does not own —
  an imported row points at the candidate's own file.

Scoring is stubbed throughout so the suite never runs a sentence-transformer pass.
"""

import json
import uuid
from types import SimpleNamespace

import pytest

from src.models.models import (
    Candidate,
    CandidateEducation,
    CandidateProfileExt,
    CandidateSkill,
    Company,
    Department,
    JobPosting,
    ResumePoolEntry,
    User,
)
from src.auth import get_password_hash


def _uid():
    return str(uuid.uuid4())


@pytest.fixture()
def hr_company(db, hr_headers):
    """The Company the hr_headers fixture attached to the HR user."""
    hr_user = db.query(User).filter(User.email == "hr_test@test.com").first()
    return db.query(Company).filter(Company.id == hr_user.company_id).first()


@pytest.fixture(autouse=True)
def stub_scorer(monkeypatch):
    """Keep MiniLM/spaCy out of the fast suite.

    Returns the shape `_score_against_jobs` reads off the result object, and counts
    calls so tests can prove the apply hook reuses the score rather than re-running it.
    """
    from src.services.enhanced_ats_service import enhanced_ats_service

    calls = {"n": 0}

    def _fake(resume_text=None, job_description=None, required_skills=None,
              required_experience=None, **kw):
        calls["n"] += 1
        return SimpleNamespace(
            ats_score=72.0, rating="Very Good",
            matched_skills=["python"], missing_skills=["kafka"],
            skills_match_score=70.0, semantic_score=68.0, experience_score=80.0,
            keyword_score=60.0, education_score=75.0,
            extracted_skills=["python", "fastapi"], extracted_experience=3.0,
        )

    monkeypatch.setattr(enhanced_ats_service, "enhanced_score_candidate", _fake)
    return calls


def _make_job(db, company, title="Backend Engineer"):
    department = db.query(Department).filter(Department.company_id == company.id).first()
    job = JobPosting(
        id=_uid(), company_id=company.id, title=title,
        description="Build APIs", requirements="Python, FastAPI",
        department_id=department.id if department else None,
        experience_years=3, required_skills=json.dumps(["python", "fastapi"]),
        status="active",
    )
    db.add(job)
    db.flush()
    return job


def _make_registered_user(db, company, email, first="Anjali", last="Rao"):
    """Someone who signed up on the portal: a User plus the placeholder Candidate
    row src.auth.create_user writes at registration."""
    user = User(
        id=_uid(), email=email, password_hash=get_password_hash("Pass@1234"),
        role="candidate", first_name=first, last_name=last,
        is_active=True, email_verified=True, company_id=company.id,
    )
    db.add(user)
    db.flush()
    db.add(Candidate(
        id=_uid(), company_id=company.id, user_id=user.id, email=email,
        first_name=first, last_name=last, current_stage="registered",
    ))
    db.flush()
    return user


def _make_profile(db, company, user, *, institution=None, end_year=None,
                  degree=None, skills=(), resume_path=None, headline=None):
    profile = CandidateProfileExt(
        id=_uid(), company_id=company.id, user_id=user.id,
        headline=headline, resume_path=resume_path, total_experience_years=4.0,
    )
    db.add(profile)
    db.flush()
    if institution:
        db.add(CandidateEducation(
            id=_uid(), company_id=company.id, profile_id=profile.id,
            institution_name=institution, end_year=end_year, degree=degree,
            education_level="undergraduate",
        ))
    for name in skills:
        db.add(CandidateSkill(id=_uid(), company_id=company.id,
                              profile_id=profile.id, skill_name=name))
    db.flush()
    return profile


def _import(client, hr_headers, **body):
    return client.post("/api/v1/hr/resume-pool/import-registered",
                       json={"limit": 50, **body}, headers=hr_headers)


# ── Import ────────────────────────────────────────────────────────────────────

def test_import_creates_a_row_per_registered_user(client, db, hr_headers, hr_company):
    _make_job(db, hr_company)
    _make_registered_user(db, hr_company, "one@portal.com")
    _make_registered_user(db, hr_company, "two@portal.com", "Rohit", "Sen")

    r = _import(client, hr_headers)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 2

    rows = db.query(ResumePoolEntry).filter(
        ResumePoolEntry.company_id == hr_company.id).all()
    assert len(rows) == 2
    assert {row.source for row in rows} == {"registered"}
    assert all(row.source_user_id for row in rows)
    assert all(row.company_id == hr_company.id for row in rows)


def test_import_is_idempotent(client, db, hr_headers, hr_company):
    _make_job(db, hr_company)
    _make_registered_user(db, hr_company, "once@portal.com")

    assert _import(client, hr_headers).json()["imported"] == 1
    second = _import(client, hr_headers).json()
    assert second["imported"] == 0
    assert second["remaining"] == 0
    assert db.query(ResumePoolEntry).count() == 1


def test_signup_only_user_is_imported_without_a_score(client, db, hr_headers, hr_company):
    """Decision: everyone registered shows up, resume or not. No resume and no
    profile means nothing to score — but the person must still appear."""
    _make_job(db, hr_company)
    _make_registered_user(db, hr_company, "bare@portal.com")

    body = _import(client, hr_headers).json()
    assert body["imported"] == 1
    assert body["scoreless"] == 1

    row = db.query(ResumePoolEntry).first()
    assert row.best_score is None
    assert row.resume_path is None

    listing = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()
    result = listing["results"][0]
    assert result["best_match"] is None
    assert result["has_resume"] is False
    assert result["source"] == "registered"


def test_structured_profile_beats_the_resume_parser(client, db, hr_headers, hr_company):
    """A filled-in profile is better data than anything parsed from prose, so the
    education table must win outright."""
    _make_job(db, hr_company)
    user = _make_registered_user(db, hr_company, "profiled@portal.com")
    _make_profile(db, hr_company, user, institution="NIT Trichy", end_year=2021,
                  degree="B.Tech", skills=["python", "django"],
                  headline="Senior Backend Engineer")

    assert _import(client, hr_headers).json()["imported"] == 1

    row = db.query(ResumePoolEntry).first()
    assert row.college == "NIT Trichy"
    assert row.year_of_passing == 2021
    assert row.highest_degree == "B.Tech"
    assert row.job_role == "Senior Backend Engineer"
    assert set(json.loads(row.extracted_skills)) == {"python", "django"}
    # Profile text was scoreable, so the row ranked.
    assert row.best_score is not None


def test_applicant_with_several_candidate_rows_imports_once(client, db, hr_headers, hr_company):
    """Registration writes one Candidate and each application writes another, so
    enumerating candidates instead of users would import this person twice."""
    job = _make_job(db, hr_company)
    user = _make_registered_user(db, hr_company, "applied@portal.com")
    db.add(Candidate(
        id=_uid(), company_id=hr_company.id, user_id=user.id, job_id=job.id,
        email=user.email, first_name="Anjali", last_name="Rao",
        resume_path="resumes/abc123.pdf", resume_text="Python engineer resume",
        current_stage="hr_review",
    ))
    db.flush()

    assert _import(client, hr_headers).json()["imported"] == 1
    rows = db.query(ResumePoolEntry).all()
    assert len(rows) == 1
    # The application row, not the registration placeholder, is the linked one.
    assert rows[0].resume_path == "resumes/abc123.pdf"


def test_import_adopts_a_matching_hr_upload(client, db, hr_headers, hr_company):
    """HR may already have uploaded this person's CV by hand — don't show them twice."""
    _make_job(db, hr_company)
    user = _make_registered_user(db, hr_company, "dupe@portal.com")
    db.add(ResumePoolEntry(
        id=_uid(), company_id=hr_company.id, email="dupe@portal.com",
        filename="dupe.pdf", resume_path="resumes/pool/xyz.pdf",
        resume_text="text", source="upload",
    ))
    db.flush()

    assert _import(client, hr_headers).json()["refreshed"] == 1
    rows = db.query(ResumePoolEntry).all()
    assert len(rows) == 1
    assert rows[0].source == "registered"
    assert rows[0].source_user_id == user.id
    # HR's uploaded file is a real artifact the pool owns; keep it.
    assert rows[0].resume_path == "resumes/pool/xyz.pdf"


def test_list_filters_by_source_and_reports_pending(client, db, hr_headers, hr_company):
    _make_job(db, hr_company)
    _make_registered_user(db, hr_company, "portal@portal.com")
    db.add(ResumePoolEntry(
        id=_uid(), company_id=hr_company.id, email="manual@x.com",
        filename="manual.pdf", resume_path="resumes/pool/m.pdf", source="upload",
    ))
    db.flush()

    pending = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()
    assert pending["registered_pending"] == 1
    assert pending["active_jobs"], "active jobs power the move dialog for unscored rows"

    _import(client, hr_headers)

    registered = client.get("/api/v1/hr/resume-pool?source=registered", headers=hr_headers).json()
    uploaded = client.get("/api/v1/hr/resume-pool?source=upload", headers=hr_headers).json()
    assert registered["total"] == 1
    assert uploaded["total"] == 1
    assert registered["registered_pending"] == 0


def test_import_ignores_other_companies(client, db, hr_headers, hr_company):
    """Tenant isolation: another company's registered users are not importable."""
    other = Company(id=_uid(), name="Other Co", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    _make_registered_user(db, other, "outsider@portal.com")
    _make_job(db, hr_company)

    assert _import(client, hr_headers).json()["imported"] == 0
    assert db.query(ResumePoolEntry).count() == 0


# ── Move to ATS ───────────────────────────────────────────────────────────────

def _move(client, hr_headers, entry_id, job_id):
    return client.post(
        f"/api/v1/hr/resume-pool/{entry_id}/move-to-ats",
        json={"job_id": job_id, "first_name": "Anjali", "last_name": "Rao",
              "email": "moved@portal.com"},
        headers=hr_headers,
    )


def test_move_to_ats_allows_a_second_different_job(client, db, hr_headers, hr_company):
    job_a = _make_job(db, hr_company, "Backend Engineer")
    job_b = _make_job(db, hr_company, "Platform Engineer")
    entry = ResumePoolEntry(
        id=_uid(), company_id=hr_company.id, email="moved@portal.com",
        filename="a.pdf", resume_path="resumes/pool/a.pdf", resume_text="text",
        source="upload",
    )
    db.add(entry)
    db.flush()

    assert _move(client, hr_headers, entry.id, job_a.id).status_code == 200
    assert _move(client, hr_headers, entry.id, job_b.id).status_code == 200

    candidates = db.query(Candidate).filter(Candidate.email == "moved@portal.com").all()
    assert {c.job_id for c in candidates} == {job_a.id, job_b.id}


def test_move_to_ats_still_blocks_the_same_job(client, db, hr_headers, hr_company):
    job = _make_job(db, hr_company)
    entry = ResumePoolEntry(
        id=_uid(), company_id=hr_company.id, email="moved@portal.com",
        filename="a.pdf", resume_path="resumes/pool/a.pdf", resume_text="text",
        source="upload",
    )
    db.add(entry)
    db.flush()

    assert _move(client, hr_headers, entry.id, job.id).status_code == 200
    second = _move(client, hr_headers, entry.id, job.id)
    assert second.status_code == 409
    assert "already in the pipeline" in second.json()["detail"]


# ── Delete ────────────────────────────────────────────────────────────────────

def test_delete_keeps_a_resume_file_the_pool_does_not_own(client, db, hr_headers,
                                                          hr_company, monkeypatch):
    """An imported row points at the candidate's own file. Removing them from the
    matcher must not delete the resume their profile still serves."""
    from src.services import storage as storage_mod

    deleted = []
    monkeypatch.setattr(storage_mod.storage, "delete", lambda key: deleted.append(key))

    user = _make_registered_user(db, hr_company, "own@portal.com")
    entry = ResumePoolEntry(
        id=_uid(), company_id=hr_company.id, email="own@portal.com",
        filename="own.pdf", resume_path="uploads/resumes/user_ab12cd34.pdf",
        source="registered", source_user_id=user.id,
    )
    db.add(entry)
    db.flush()

    assert client.delete(f"/api/v1/hr/resume-pool/{entry.id}",
                         headers=hr_headers).status_code == 200
    assert deleted == [], f"deleted a file the pool does not own: {deleted}"


def test_delete_still_removes_a_file_the_pool_owns(client, db, hr_headers,
                                                   hr_company, monkeypatch):
    from src.services import storage as storage_mod

    deleted = []
    monkeypatch.setattr(storage_mod.storage, "delete", lambda key: deleted.append(key))

    entry = ResumePoolEntry(
        id=_uid(), company_id=hr_company.id, email="pooled@x.com",
        filename="pooled.pdf", resume_path="resumes/pool/pooled.pdf", source="upload",
    )
    db.add(entry)
    db.flush()

    assert client.delete(f"/api/v1/hr/resume-pool/{entry.id}",
                         headers=hr_headers).status_code == 200
    assert deleted == ["resumes/pool/pooled.pdf"]


# ── Talent pool: coverage, history and outcomes ──────────────────────────────

def _apply(db, company, user, job, stage="applied", email=None):
    """A Candidate row representing one application."""
    cand = Candidate(
        id=_uid(), company_id=company.id, user_id=user.id if user else None,
        job_id=job.id, email=email or (user.email if user else "x@x.com"),
        first_name="Anjali", last_name="Rao", current_stage=stage,
        resume_text="Python engineer resume", resume_path="resumes/abc.pdf",
    )
    db.add(cand)
    db.flush()
    return cand


def _pool_of(db, company, email):
    return db.query(ResumePoolEntry).filter(
        ResumePoolEntry.company_id == company.id,
        ResumePoolEntry.email == email).first()


def test_orphan_candidate_without_a_user_is_imported(client, db, hr_headers, hr_company):
    """HR-created candidates have no user account, so the registered pass cannot see
    them. Without the orphan pass they would be missing from the talent pool."""
    job = _make_job(db, hr_company)
    _apply(db, hr_company, None, job, email="noaccount@x.com")

    body = _import(client, hr_headers).json()
    assert body["imported"] == 1

    row = _pool_of(db, hr_company, "noaccount@x.com")
    assert row is not None
    assert row.source == "applied"
    assert row.source_user_id is None


def test_orphan_pass_groups_several_applications_into_one_row(client, db, hr_headers, hr_company):
    job_a = _make_job(db, hr_company, "Backend Engineer")
    job_b = _make_job(db, hr_company, "Platform Engineer")
    _apply(db, hr_company, None, job_a, email="multi@x.com")
    _apply(db, hr_company, None, job_b, email="multi@x.com")

    assert _import(client, hr_headers).json()["imported"] == 1
    rows = db.query(ResumePoolEntry).filter(
        ResumePoolEntry.email == "multi@x.com").all()
    assert len(rows) == 1


def test_orphan_pass_is_idempotent(client, db, hr_headers, hr_company):
    job = _make_job(db, hr_company)
    _apply(db, hr_company, None, job, email="once@x.com")

    assert _import(client, hr_headers).json()["imported"] == 1
    assert _import(client, hr_headers).json()["imported"] == 0
    assert db.query(ResumePoolEntry).count() == 1


def test_list_returns_every_application_with_job_and_stage(client, db, hr_headers, hr_company):
    job_a = _make_job(db, hr_company, "Backend Engineer")
    job_b = _make_job(db, hr_company, "Platform Engineer")
    user = _make_registered_user(db, hr_company, "history@portal.com")
    _apply(db, hr_company, user, job_a, stage="rejected")
    _apply(db, hr_company, user, job_b, stage="interview")

    _import(client, hr_headers)
    result = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()["results"][0]

    # The signup placeholder is not an application and must not be listed.
    assert result["application_count"] == 2
    assert {a["job_title"] for a in result["applications"]} == {"Backend Engineer", "Platform Engineer"}
    assert {a["stage"] for a in result["applications"]} == {"rejected", "interview"}


def test_outcome_prefers_a_live_pipeline_over_a_past_rejection(client, db, hr_headers, hr_company):
    """The whole point of the pool: a stale rejection must never bury someone who is
    currently interviewing for something else."""
    job_a = _make_job(db, hr_company, "Backend Engineer")
    job_b = _make_job(db, hr_company, "Platform Engineer")
    user = _make_registered_user(db, hr_company, "live@portal.com")
    _apply(db, hr_company, user, job_a, stage="rejected")
    _apply(db, hr_company, user, job_b, stage="interview")
    _import(client, hr_headers)

    assert client.get("/api/v1/hr/resume-pool",
                      headers=hr_headers).json()["results"][0]["outcome"] == "in_process"
    assert client.get("/api/v1/hr/resume-pool?outcome=rejected",
                      headers=hr_headers).json()["total"] == 0
    assert client.get("/api/v1/hr/resume-pool?outcome=in_process",
                      headers=hr_headers).json()["total"] == 1


def test_outcome_prefers_hired_over_rejected(client, db, hr_headers, hr_company):
    job_a = _make_job(db, hr_company, "Backend Engineer")
    job_b = _make_job(db, hr_company, "Platform Engineer")
    user = _make_registered_user(db, hr_company, "hired@portal.com")
    _apply(db, hr_company, user, job_a, stage="rejected")
    _apply(db, hr_company, user, job_b, stage="hired")
    _import(client, hr_headers)

    assert client.get("/api/v1/hr/resume-pool",
                      headers=hr_headers).json()["results"][0]["outcome"] == "hired"
    assert client.get("/api/v1/hr/resume-pool?outcome=hired",
                      headers=hr_headers).json()["total"] == 1
    assert client.get("/api/v1/hr/resume-pool?outcome=rejected",
                      headers=hr_headers).json()["total"] == 0


def test_rejected_person_is_findable(client, db, hr_headers, hr_company):
    job = _make_job(db, hr_company)
    user = _make_registered_user(db, hr_company, "rejected@portal.com")
    _apply(db, hr_company, user, job, stage="rejected")
    _import(client, hr_headers)

    body = client.get("/api/v1/hr/resume-pool?outcome=rejected", headers=hr_headers).json()
    assert body["total"] == 1
    assert body["results"][0]["outcome"] == "rejected"


def test_registration_placeholder_is_not_an_application(client, db, hr_headers, hr_company):
    """A signup writes a Candidate with job_id NULL. Counting it would make every
    registered person look like they were in an active pipeline."""
    _make_job(db, hr_company)
    _make_registered_user(db, hr_company, "never@portal.com")
    _import(client, hr_headers)

    result = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()["results"][0]
    assert result["applications"] == []
    assert result["outcome"] == "never_applied"
    assert client.get("/api/v1/hr/resume-pool?outcome=never_applied",
                      headers=hr_headers).json()["total"] == 1


def test_outcome_buckets_are_mutually_exclusive_and_exhaustive(client, db, hr_headers, hr_company):
    """Every person lands in exactly one bucket. This is what catches the SQL and the
    Python outcome logic drifting apart."""
    job = _make_job(db, hr_company)
    for name, stage in [("never", None), ("live", "interview"), ("rej", "rejected"),
                        ("hire", "hired"), ("off", "offer_sent"), ("decl", "offer_rejected")]:
        user = _make_registered_user(db, hr_company, f"{name}@portal.com")
        if stage:
            _apply(db, hr_company, user, job, stage=stage)
    _import(client, hr_headers)

    unfiltered = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()["total"]
    buckets = ["never_applied", "in_process", "rejected", "hired", "offer", "declined"]
    per_bucket = {
        b: client.get(f"/api/v1/hr/resume-pool?outcome={b}", headers=hr_headers).json()["total"]
        for b in buckets
    }
    assert sum(per_bucket.values()) == unfiltered, per_bucket


def test_outcome_filter_is_applied_before_pagination(client, db, hr_headers, hr_company):
    """If the filter ran per page, `total` would be wrong and pages would be ragged."""
    job = _make_job(db, hr_company)
    for i in range(5):
        user = _make_registered_user(db, hr_company, f"rej{i}@portal.com")
        _apply(db, hr_company, user, job, stage="rejected")
    for i in range(3):
        user = _make_registered_user(db, hr_company, f"hire{i}@portal.com")
        _apply(db, hr_company, user, job, stage="hired")
    _import(client, hr_headers)

    body = client.get("/api/v1/hr/resume-pool?outcome=rejected&limit=2",
                      headers=hr_headers).json()
    assert body["total"] == 5
    assert len(body["results"]) == 2
    assert all(r["outcome"] == "rejected" for r in body["results"])


def test_pagination_reports_total_independent_of_limit(client, db, hr_headers, hr_company):
    _make_job(db, hr_company)
    for i in range(5):
        _make_registered_user(db, hr_company, f"page{i}@portal.com")
    _import(client, hr_headers)

    first = client.get("/api/v1/hr/resume-pool?limit=2", headers=hr_headers).json()
    assert first["total"] == 5 and len(first["results"]) == 2
    last = client.get("/api/v1/hr/resume-pool?skip=4&limit=2", headers=hr_headers).json()
    assert last["total"] == 5 and len(last["results"]) == 1


def test_email_match_never_steals_another_users_applications(client, db, hr_headers, hr_company):
    """Candidate.email is free-form — HR types it into the Move-to-ATS modal — so an
    email match must never attribute one person's history to another. A Candidate that
    belongs to a different user account is off-limits."""
    job = _make_job(db, hr_company)
    owner = _make_registered_user(db, hr_company, "shared@x.com")
    other = _make_registered_user(db, hr_company, "other@x.com")
    # Same email address, but it demonstrably belongs to `other`.
    _apply(db, hr_company, other, job, stage="rejected", email="shared@x.com")
    _import(client, hr_headers)

    rows = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()["results"]
    owner_row = next(r for r in rows if r["source_user_id"] == owner.id)
    assert owner_row["applications"] == []
    assert owner_row["outcome"] == "never_applied"


# ── Sourcing CRM: manual adds, inline edit, stages, notes, metrics ───────────

def _create(client, hr_headers, **body):
    return client.post("/api/v1/hr/resume-pool",
                       json={"full_name": "Manual Person", **body}, headers=hr_headers)


def test_add_a_candidate_by_hand(client, db, hr_headers, hr_company):
    """Sourcing often starts with just a name, so only the name is required."""
    r = _create(client, hr_headers, email="manual@x.com", job_role="Designer",
                skills=["figma"], source_channel="LinkedIn", notes="Met at a meetup")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["full_name"] == "Manual Person"
    assert body["stage"] == "new"
    assert body["source"] == "manual"
    assert body["source_channel"] == "LinkedIn"
    assert body["notes"] == "Met at a meetup"
    assert body["extracted_skills"] == ["figma"]


def test_add_a_candidate_with_only_a_name(client, db, hr_headers, hr_company):
    assert _create(client, hr_headers).status_code == 201


def test_adding_a_duplicate_email_is_refused(client, db, hr_headers, hr_company):
    _create(client, hr_headers, email="dupe@x.com")
    second = _create(client, hr_headers, email="dupe@x.com")
    assert second.status_code == 409
    assert "already in the talent pool" in second.json()["detail"]


def test_inline_edit_only_touches_the_fields_sent(client, db, hr_headers, hr_company):
    """A patch that changes the phone must not blank the notes."""
    entry_id = _create(client, hr_headers, notes="Keep me", job_role="Designer").json()["id"]

    r = client.patch(f"/api/v1/hr/resume-pool/{entry_id}",
                     json={"phone": "+91 99999 11111"}, headers=hr_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["phone"] == "+91 99999 11111"
    assert body["notes"] == "Keep me"
    assert body["job_role"] == "Designer"


def test_moving_stage_in_one_click(client, db, hr_headers, hr_company):
    entry_id = _create(client, hr_headers).json()["id"]
    for stage in ("ongoing", "archived", "new"):
        r = client.patch(f"/api/v1/hr/resume-pool/{entry_id}/stage",
                         json={"stage": stage}, headers=hr_headers)
        assert r.status_code == 200
        assert r.json()["stage"] == stage


def test_an_unknown_stage_is_refused(client, db, hr_headers, hr_company):
    entry_id = _create(client, hr_headers).json()["id"]
    r = client.patch(f"/api/v1/hr/resume-pool/{entry_id}/stage",
                     json={"stage": "banana"}, headers=hr_headers)
    assert r.status_code == 400


def test_stage_is_independent_of_ats_outcome(client, db, hr_headers, hr_company):
    """The whole point of the manual lane: someone rejected by the ATS can still be
    parked in Ongoing because HR wants to keep them warm."""
    job = _make_job(db, hr_company)
    user = _make_registered_user(db, hr_company, "warm@portal.com")
    _apply(db, hr_company, user, job, stage="rejected")
    _import(client, hr_headers)

    entry_id = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()["results"][0]["id"]
    r = client.patch(f"/api/v1/hr/resume-pool/{entry_id}/stage",
                     json={"stage": "ongoing"}, headers=hr_headers)
    assert r.status_code == 200
    assert r.json()["stage"] == "ongoing"      # HR's lane
    assert r.json()["outcome"] == "rejected"   # untouched ATS history


def test_marking_contacted_stamps_the_date(client, db, hr_headers, hr_company):
    entry_id = _create(client, hr_headers).json()["id"]
    assert client.get("/api/v1/hr/resume-pool", headers=hr_headers
                      ).json()["results"][0]["last_contact_at"] is None

    r = client.patch(f"/api/v1/hr/resume-pool/{entry_id}",
                     json={"mark_contacted": True}, headers=hr_headers)
    assert r.status_code == 200
    assert r.json()["last_contact_at"] is not None
    assert r.json()["days_since_contact"] == 0


def test_list_filters_by_stage(client, db, hr_headers, hr_company):
    a = _create(client, hr_headers, email="a@x.com").json()["id"]
    _create(client, hr_headers, email="b@x.com")
    client.patch(f"/api/v1/hr/resume-pool/{a}/stage", json={"stage": "archived"},
                 headers=hr_headers)

    assert client.get("/api/v1/hr/resume-pool?stage=archived",
                      headers=hr_headers).json()["total"] == 1
    assert client.get("/api/v1/hr/resume-pool?stage=new",
                      headers=hr_headers).json()["total"] == 1


def test_metrics_report_the_pipeline_and_sources(client, db, hr_headers, hr_company):
    a = _create(client, hr_headers, email="a@x.com", source_channel="LinkedIn").json()["id"]
    _create(client, hr_headers, email="b@x.com", source_channel="LinkedIn")
    _create(client, hr_headers, email="c@x.com", source_channel="Referral")
    client.patch(f"/api/v1/hr/resume-pool/{a}/stage", json={"stage": "ongoing"},
                 headers=hr_headers)

    m = client.get("/api/v1/hr/talent-pool/metrics", headers=hr_headers).json()
    assert m["total"] == 3
    assert m["new_count"] == 2 and m["ongoing_count"] == 1 and m["archived_count"] == 0
    assert {p["stage"]: p["count"] for p in m["pipeline"]} == {
        "new": 2, "ongoing": 1, "archived": 0}
    assert dict((s["name"], s["count"]) for s in m["sources"])["LinkedIn"] == 2
    assert m["never_contacted"] == 3


def test_metrics_are_scoped_to_the_company(client, db, hr_headers, hr_company):
    other = Company(id=_uid(), name="Other Co", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    db.add(ResumePoolEntry(id=_uid(), company_id=other.id, email="them@x.com",
                           filename="them", source="manual", stage="new"))
    db.flush()
    _create(client, hr_headers, email="ours@x.com")

    assert client.get("/api/v1/hr/talent-pool/metrics", headers=hr_headers).json()["total"] == 1


def test_attachment_upload_rejects_unusable_files(client, db, hr_headers, hr_company):
    """When nothing could be stored the error names the offending file, so HR can see
    which one was the problem rather than a generic list of accepted types."""
    entry_id = _create(client, hr_headers).json()["id"]
    r = client.post(f"/api/v1/hr/resume-pool/{entry_id}/attachments",
                    files=[("files", ("notes.exe", b"nope", "application/octet-stream"))],
                    headers=hr_headers)
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "notes.exe" in detail and "not accepted" in detail


def test_oversize_and_wrong_type_files_are_named_not_silently_dropped(
        client, db, hr_headers, hr_company, monkeypatch):
    """A file the user picked that just vanishes is worse than an error — they cannot
    tell a failed upload from one that was never going to work."""
    from src.services import storage as storage_mod
    monkeypatch.setattr(storage_mod.storage, "save_fileobj", lambda fh, key: key)

    entry_id = _create(client, hr_headers).json()["id"]
    r = client.post(
        f"/api/v1/hr/resume-pool/{entry_id}/attachments",
        files=[("files", ("good.pdf", b"%PDF-1.4 hello", "application/pdf")),
               ("files", ("bad.exe", b"nope", "application/octet-stream"))],
        headers=hr_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["entry"]["attachments"]) == 1
    assert len(body["rejected"]) == 1
    assert "bad.exe" in body["rejected"][0]
    assert "skipped" in body["message"]


def test_replacing_a_file_keeps_its_place(client, db, hr_headers, hr_company, monkeypatch):
    from src.services import storage as storage_mod
    monkeypatch.setattr(storage_mod.storage, "save_fileobj", lambda fh, key: key)
    monkeypatch.setattr(storage_mod.storage, "delete", lambda key: None)

    entry_id = _create(client, hr_headers).json()["id"]
    first = client.post(f"/api/v1/hr/resume-pool/{entry_id}/attachments",
                        files=[("files", ("old.pdf", b"%PDF old", "application/pdf"))],
                        headers=hr_headers).json()["entry"]
    att_id = first["attachments"][0]["id"]

    r = client.put(f"/api/v1/hr/resume-pool/{entry_id}/attachments/{att_id}",
                   files={"file": ("new.pdf", b"%PDF new", "application/pdf")},
                   headers=hr_headers)
    assert r.status_code == 200, r.text
    atts = r.json()["entry"]["attachments"]
    # Same row, new file — not a second attachment.
    assert len(atts) == 1
    assert atts[0]["id"] == att_id
    assert atts[0]["filename"] == "new.pdf"


def test_replacing_the_scored_resume_with_an_image_clears_the_score_source(
        client, db, hr_headers, hr_company, monkeypatch):
    """An image cannot carry the match, so the profile must not keep pointing at a
    resume path that no longer holds a scoreable document."""
    from src.services import storage as storage_mod
    monkeypatch.setattr(storage_mod.storage, "save_fileobj", lambda fh, key: key)
    monkeypatch.setattr(storage_mod.storage, "delete", lambda key: None)

    entry_id = _create(client, hr_headers).json()["id"]
    first = client.post(f"/api/v1/hr/resume-pool/{entry_id}/attachments",
                        files=[("files", ("cv.pdf", b"%PDF cv", "application/pdf"))],
                        headers=hr_headers).json()["entry"]
    assert first["has_resume"] is True
    att_id = first["attachments"][0]["id"]

    r = client.put(f"/api/v1/hr/resume-pool/{entry_id}/attachments/{att_id}",
                   files={"file": ("scan.png", b"\x89PNG", "image/png")},
                   headers=hr_headers)
    assert r.status_code == 200
    assert r.json()["entry"]["has_resume"] is False
    assert r.json()["entry"]["attachments"][0]["is_scoreable"] is False


def test_last_contact_can_be_set_to_a_specific_date(client, db, hr_headers, hr_company):
    """HR often logs a call days after it happened, so 'now' is not enough."""
    entry_id = _create(client, hr_headers).json()["id"]
    r = client.patch(f"/api/v1/hr/resume-pool/{entry_id}",
                     json={"last_contact_at": "2026-07-01T12:00:00"}, headers=hr_headers)
    assert r.status_code == 200, r.text
    assert r.json()["last_contact_at"].startswith("2026-07-01")
    assert r.json()["days_since_contact"] > 0


def test_last_contact_can_be_cleared(client, db, hr_headers, hr_company):
    entry_id = _create(client, hr_headers).json()["id"]
    client.patch(f"/api/v1/hr/resume-pool/{entry_id}",
                 json={"mark_contacted": True}, headers=hr_headers)
    r = client.patch(f"/api/v1/hr/resume-pool/{entry_id}",
                     json={"last_contact_at": None}, headers=hr_headers)
    assert r.json()["last_contact_at"] is None
    assert r.json()["days_since_contact"] is None


def test_image_attaches_but_is_not_scoreable(client, db, hr_headers, hr_company, monkeypatch):
    """Images display on the profile but cannot be ATS-scored — the matcher runs no
    OCR, so scoring one would produce a meaningless zero."""
    from src.services import storage as storage_mod
    monkeypatch.setattr(storage_mod.storage, "save_fileobj", lambda fh, key: key)

    entry_id = _create(client, hr_headers).json()["id"]
    r = client.post(f"/api/v1/hr/resume-pool/{entry_id}/attachments",
                    files=[("files", ("cv.png", b"\x89PNG\r\n", "image/png"))],
                    headers=hr_headers)
    assert r.status_code == 200, r.text
    atts = r.json()["entry"]["attachments"]
    assert len(atts) == 1
    assert atts[0]["filename"] == "cv.png"
    assert atts[0]["is_scoreable"] is False
    # An image must not become the scored resume.
    assert r.json()["entry"]["has_resume"] is False


# ── Backlog scoring ──────────────────────────────────────────────────────────

def _pool_row(db, company, **kw):
    entry = ResumePoolEntry(
        id=_uid(), company_id=company.id, source="manual", stage="new",
        filename=kw.pop("filename", "person"), **kw,
    )
    db.add(entry)
    db.flush()
    return entry


def _score(client, hr_headers, **body):
    return client.post("/api/v1/hr/talent-pool/score-backlog",
                       json={"limit": 10, **body}, headers=hr_headers)


def test_scoring_ranks_people_who_have_text(client, db, hr_headers, hr_company):
    _make_job(db, hr_company)
    _pool_row(db, hr_company, email="a@x.com", resume_text="Python engineer")

    r = _score(client, hr_headers)
    assert r.status_code == 200, r.text
    assert r.json()["scored"] == 1
    assert r.json()["remaining"] == 0

    row = db.query(ResumePoolEntry).filter(ResumePoolEntry.email == "a@x.com").first()
    db.refresh(row)
    assert row.best_score is not None
    assert row.best_job_id is not None


def test_people_without_text_are_reported_and_never_block_the_loop(
        client, db, hr_headers, hr_company):
    """The termination guarantee. A row with no text can never be scored, so counting
    it as outstanding would leave the caller looping on a number that never falls."""
    _make_job(db, hr_company)
    _pool_row(db, hr_company, email="has@x.com", resume_text="Python engineer")
    _pool_row(db, hr_company, email="none@x.com", resume_text=None)

    body = _score(client, hr_headers).json()
    assert body["scored"] == 1
    assert body["unscoreable"] == 1
    # The textless row is excluded from remaining, so the caller stops.
    assert body["remaining"] == 0

    untouched = db.query(ResumePoolEntry).filter(ResumePoolEntry.email == "none@x.com").first()
    db.refresh(untouched)
    assert untouched.best_score is None


def test_scoring_does_not_redo_people_who_already_have_a_score(
        client, db, hr_headers, hr_company, stub_scorer):
    _make_job(db, hr_company)
    _pool_row(db, hr_company, email="done@x.com", resume_text="text", best_score=61.0)

    before = stub_scorer["n"]
    body = _score(client, hr_headers).json()
    assert body["scored"] == 0
    assert body["remaining"] == 0
    # Repeat clicks must stay cheap — no model passes at all.
    assert stub_scorer["n"] == before


def test_scoring_does_not_overwrite_hand_edited_skills(client, db, hr_headers, hr_company):
    """HR corrects the parser on the card. Scoring must fill gaps, not clobber."""
    _make_job(db, hr_company)
    _pool_row(db, hr_company, email="edited@x.com", resume_text="text",
              extracted_skills=json.dumps(["hand-typed"]))

    assert _score(client, hr_headers).json()["scored"] == 1
    row = db.query(ResumePoolEntry).filter(ResumePoolEntry.email == "edited@x.com").first()
    db.refresh(row)
    assert json.loads(row.extracted_skills) == ["hand-typed"]


def test_scoring_respects_the_batch_limit_and_reports_the_rest(
        client, db, hr_headers, hr_company):
    _make_job(db, hr_company)
    for i in range(5):
        _pool_row(db, hr_company, email=f"p{i}@x.com", resume_text="text")

    body = _score(client, hr_headers, limit=2).json()
    assert body["scored"] == 2
    assert body["remaining"] == 3


def test_a_client_cannot_ask_for_an_unbounded_batch(client, db, hr_headers, hr_company):
    """The cap is a safety property — it protects a gunicorn worker from a
    multi-minute request, so it must not be defeatable from the browser."""
    _make_job(db, hr_company)
    for i in range(12):
        _pool_row(db, hr_company, email=f"q{i}@x.com", resume_text="text")

    body = _score(client, hr_headers, limit=500).json()
    assert body["scored"] == 10          # SCORE_BATCH_MAX
    assert body["remaining"] == 2


def test_scoring_refuses_when_there_are_no_open_roles(client, db, hr_headers, hr_company):
    _pool_row(db, hr_company, email="a@x.com", resume_text="text")
    r = _score(client, hr_headers)
    assert r.status_code == 400
    assert "no open roles" in r.json()["detail"].lower()


def test_scoring_is_scoped_to_the_company(client, db, hr_headers, hr_company):
    other = Company(id=_uid(), name="Other Co", slug=f"o-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    _make_job(db, hr_company)
    _pool_row(db, other, email="theirs@x.com", resume_text="text")

    body = _score(client, hr_headers).json()
    assert body["scored"] == 0
    theirs = db.query(ResumePoolEntry).filter(ResumePoolEntry.email == "theirs@x.com").first()
    assert theirs.best_score is None


def test_metrics_report_the_unscored_backlog(client, db, hr_headers, hr_company):
    _make_job(db, hr_company)
    _pool_row(db, hr_company, email="a@x.com", resume_text="text")
    _pool_row(db, hr_company, email="b@x.com", resume_text=None)
    _pool_row(db, hr_company, email="c@x.com", resume_text="text", best_score=70.0)

    m = client.get("/api/v1/hr/talent-pool/metrics", headers=hr_headers).json()
    assert m["unscored"] == 1
    assert m["unscoreable"] == 1


# ── College filter ───────────────────────────────────────────────────────────

def _colleges(client, hr_headers, value):
    return client.get("/api/v1/hr/resume-pool", params={"college": value},
                      headers=hr_headers).json()


def _campus(db, company):
    _pool_row(db, company, email="psg@x.com", college="PSG College of Technology")
    _pool_row(db, company, email="iiit@x.com", college="IIIT Pune")
    _pool_row(db, company, email="other@x.com", college="Parul University")


def test_college_filter_still_takes_a_single_name(client, db, hr_headers, hr_company):
    """The old single-value behaviour is what every existing caller sends."""
    _campus(db, hr_company)

    body = _colleges(client, hr_headers, "psg college")
    assert body["total"] == 1
    assert body["results"][0]["college"] == "PSG College of Technology"


def test_college_filter_matches_any_of_several(client, db, hr_headers, hr_company):
    """Campus hiring is a set operation — a region or the whole IIIT network arrives
    as one comma-separated list and must come back as the union, not the intersection."""
    _campus(db, hr_company)

    body = _colleges(client, hr_headers, "psg college,iiit pune")
    assert body["total"] == 2
    assert {r["college"] for r in body["results"]} == {
        "PSG College of Technology", "IIIT Pune"}


def test_college_filter_ignores_blank_terms(client, db, hr_headers, hr_company):
    """A chip removed mid-list leaves stray separators in the query string; they must
    not become an empty '%%' term that matches everyone."""
    _campus(db, hr_company)

    assert _colleges(client, hr_headers, " psg college , ,iiit pune, ")["total"] == 2
    # All-empty degrades to no filter rather than matching nothing.
    assert _colleges(client, hr_headers, " , ")["total"] == 3


def test_college_filter_counts_before_paging(client, db, hr_headers, hr_company):
    """`total` drives the pager, so it has to reflect the filter, not the page."""
    for i in range(4):
        _pool_row(db, hr_company, email=f"c{i}@x.com", college="IIIT Nagpur")
    _pool_row(db, hr_company, email="z@x.com", college="Parul University")

    body = client.get("/api/v1/hr/resume-pool",
                      params={"college": "iiit nagpur,iiit pune", "limit": 2},
                      headers=hr_headers).json()
    assert body["total"] == 4
    assert len(body["results"]) == 2


def test_history_survives_a_job_with_no_company(client, db, hr_headers, hr_company):
    """Proves the read path uses two queries rather than a join.

    This database has orphan rows with NULL company_id (see
    docs/DASHBOARD_DATA_PITFALLS.md). Joining JobPosting would let the tenant
    auto-filter append `job_postings.company_id = X`, and since `NULL = X` is false
    that turns the outer join inner and silently drops the whole application — the
    person would read as never_applied.
    """
    orphan_job = JobPosting(
        id=_uid(), company_id=None, title="Legacy Role",
        description="Old", requirements="Old", status="active",
    )
    db.add(orphan_job)
    db.flush()

    user = _make_registered_user(db, hr_company, "ghost@portal.com")
    _apply(db, hr_company, user, orphan_job, stage="rejected")
    _import(client, hr_headers)

    result = client.get("/api/v1/hr/resume-pool", headers=hr_headers).json()["results"][0]
    assert result["application_count"] == 1, "the application was dropped by a join"
    assert result["applications"][0]["job_title"] == "Legacy Role"
    assert result["outcome"] == "rejected"
