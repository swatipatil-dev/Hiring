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

    # setitem on the instance __dict__, not setattr: undoing setattr on an instance
    # stores the real bound method there, which then hides any later class-level patch
    # (test_ats_prepared_resume) whenever both files share an xdist worker. setitem
    # deletes the key on undo, leaving the instance as it was.
    monkeypatch.setitem(vars(enhanced_ats_service), "enhanced_score_candidate", _fake)
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

    The orphan job itself is not readable: a row with no company belongs to no
    tenant, so the filter hides it from every tenant. Only its title is lost;
    scripts/backfill_orphan_company_ids.py is the fix for the data.
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
    assert result["applications"][0]["job_title"] is None
    assert result["outcome"] == "rejected"


def test_hard_deleting_a_job_survives_pool_references(client, db, hr_headers, hr_company):
    """Hard-deleting a job must not trip over resume_pool's foreign keys.

    A pool entry that was moved to ATS points at both the job it matched
    (best_job_id) and the Candidate the move produced (moved_candidate_id).
    delete_job_permanent deletes the job and its candidates but never clears
    either column, so MySQL's default RESTRICT blocks the whole transaction.
    """
    job = JobPosting(id=_uid(), company_id=hr_company.id, title="Backend Engineer",
                     description="d", requirements="r", status="active")
    db.add(job)
    db.flush()

    cand = Candidate(id=_uid(), company_id=hr_company.id, job_id=job.id,
                     email="pooled@example.com", first_name="Pooled", last_name="Person")
    db.add(cand)
    db.flush()

    entry = ResumePoolEntry(id=_uid(), company_id=hr_company.id,
                            filename="cv.pdf", resume_path="resumes/pool/x.pdf",
                            full_name="Pooled Person", email="pooled@example.com",
                            best_job_id=job.id, status="moved",
                            moved_candidate_id=cand.id)
    db.add(entry)
    db.flush()

    # Hold the ids as plain strings: expire_all() below makes any later attribute
    # access on a deleted instance re-query and raise instead of returning the value.
    job_id, cand_id, entry_id = job.id, cand.id, entry.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.query(Candidate).filter(Candidate.id == cand_id).first() is None
    assert db.query(JobPosting).filter(JobPosting.id == job_id).first() is None

    # The pool row is an independent record — HR's uploaded CV outlives the job it
    # happened to match — so it must survive with its pointers cleared, not be deleted.
    survivor = db.query(ResumePoolEntry).filter(ResumePoolEntry.id == entry_id).first()
    assert survivor is not None, "the pool entry was deleted along with the job"
    assert survivor.best_job_id is None
    assert survivor.moved_candidate_id is None
    # No longer promoted: the Candidate the move produced is gone, so the entry goes
    # back in the pool rather than claiming a move that no longer exists.
    assert survivor.status == "new"


# ── Returning candidates to the pool when their job goes away ─────────────────
# Closing or deleting a job used to strand its candidates. Hard delete is the worse
# case: for an HR-created candidate the Candidate row is the only copy of them, and it
# was destroyed outright along with any record of what they applied for.

def _job_and_candidate(db, company, *, title="Backend Engineer", stage="hr_review"):
    job = JobPosting(id=_uid(), company_id=company.id, title=title,
                     description="d", requirements="r", status="active")
    db.add(job)
    db.flush()
    cand = Candidate(id=_uid(), company_id=company.id, job_id=job.id,
                     email=f"pooled-{_uid()[:8]}@example.com",
                     first_name="Pooled", last_name="Person",
                     phone="555-0100", resume_path="resumes/abc.pdf",
                     resume_text="python fastapi", extracted_skills=json.dumps(["Python"]),
                     experience_years=4, current_stage=stage, ats_score=71.5)
    db.add(cand)
    db.flush()
    return job, cand


def _pool_rows(db, company, email):
    return db.query(ResumePoolEntry).filter(
        ResumePoolEntry.company_id == company.id,
        ResumePoolEntry.email == email,
    ).all()


def test_closing_a_job_puts_its_candidates_in_the_pool(client, db, hr_headers, hr_company):
    """A closed job's candidates become reachable in the talent pool — and stay in the
    job's pipeline, because a close is reversible and reopening must not be lossy."""
    job, cand = _job_and_candidate(db, hr_company)
    email, job_id, cand_id = cand.email, job.id, cand.id
    assert _pool_rows(db, hr_company, email) == []

    assert client.delete(f"/api/v1/hr/jobs/{job_id}", headers=hr_headers).status_code == 200

    db.expire_all()
    rows = _pool_rows(db, hr_company, email)
    assert len(rows) == 1, "expected exactly one pool entry for the candidate"
    entry = rows[0]
    assert entry.full_name == "Pooled Person"
    assert entry.phone == "555-0100"
    assert entry.resume_path == "resumes/abc.pdf"

    # Still in the pipeline: the candidate row is untouched and still on the job.
    survivor = db.query(Candidate).filter(Candidate.id == cand_id).first()
    assert survivor is not None and survivor.job_id == job_id


def test_closing_a_job_twice_does_not_duplicate_the_pool_entry(client, db, hr_headers, hr_company):
    """Close/reopen/close must be idempotent — the lookup, not the DB, prevents this."""
    job, cand = _job_and_candidate(db, hr_company)
    email, job_id = cand.email, job.id

    assert client.delete(f"/api/v1/hr/jobs/{job_id}", headers=hr_headers).status_code == 200
    assert client.put(f"/api/v1/hr/jobs/{job_id}/reopen", headers=hr_headers).status_code == 200
    assert client.delete(f"/api/v1/hr/jobs/{job_id}", headers=hr_headers).status_code == 200

    db.expire_all()
    assert len(_pool_rows(db, hr_company, email)) == 1


def test_hard_delete_preserves_the_candidate_and_the_job_they_applied_for(
        client, db, hr_headers, hr_company):
    """The person survives in the pool, and the job title survives with them.

    This is the case with no other copy: the Candidate row carries the only record of an
    HR-created person, and the job posting carries the only copy of its own title.
    """
    job, cand = _job_and_candidate(db, hr_company, title="Staff Platform Engineer")
    email, job_id, cand_id = cand.email, job.id, cand.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    assert db.query(Candidate).filter(Candidate.id == cand_id).first() is None
    assert db.query(JobPosting).filter(JobPosting.id == job_id).first() is None

    rows = _pool_rows(db, hr_company, email)
    assert len(rows) == 1
    entry = rows[0]
    assert entry.full_name == "Pooled Person"
    assert entry.resume_path == "resumes/abc.pdf"
    assert json.loads(entry.extracted_skills) == ["Python"]

    # And the API replays the application, by title, after the job is gone.
    listed = client.get("/api/v1/hr/resume-pool", headers=hr_headers, params={"limit": 50})
    assert listed.status_code == 200, listed.text
    result = next(r for r in listed.json()["results"] if r["email"] == email)
    apps = result["applications"]
    assert len(apps) == 1, apps
    assert apps[0]["job_title"] == "Staff Platform Engineer"
    assert apps[0]["archived"] is True
    assert result["application_count"] == 1
    # Not "never_applied": they demonstrably applied, the job just no longer exists.
    assert result["outcome"] != "never_applied"


def test_hard_delete_does_not_duplicate_a_candidate_moved_from_the_pool(
        client, db, hr_headers, hr_company):
    """The duplicate the database cannot catch.

    Move to ATS leaves source_user_id and source_candidate_id NULL on the pool row and
    creates the Candidate with user_id=None, so a blind insert violates neither unique
    constraint and silently splits one person into two rows. Only the lookup stops it.
    """
    job, cand = _job_and_candidate(db, hr_company)
    entry = ResumePoolEntry(id=_uid(), company_id=hr_company.id, filename="cv.pdf",
                            resume_path="resumes/pool/x.pdf", full_name="Pooled Person",
                            email=cand.email, status="moved", moved_candidate_id=cand.id)
    db.add(entry)
    db.flush()
    email, job_id, entry_id = cand.email, job.id, entry.id

    resp = client.delete(f"/api/v1/hr/jobs/{job_id}/permanent", headers=hr_headers)
    assert resp.status_code == 200, resp.text

    db.expire_all()
    rows = _pool_rows(db, hr_company, email)
    assert len(rows) == 1, f"the person was split into {len(rows)} pool rows"
    assert rows[0].id == entry_id, "a second entry was created instead of reusing theirs"
    assert json.loads(rows[0].archived_applications)[0]["job_title"] == "Backend Engineer"


# ── Dashboard: the same filters, over aggregates ─────────────────────────────
#
# The dashboard sits above the Candidates list and describes it. If the two ever
# disagree about who is in view, the charts are lying — so the load-bearing test here
# is not any single number, it is that `metrics.total` and the list's `total` are
# equal for every filter.

def _metrics(client, hr_headers, **params):
    r = client.get("/api/v1/hr/talent-pool/metrics", headers=hr_headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _list_total(client, hr_headers, **params):
    r = client.get("/api/v1/hr/resume-pool", headers=hr_headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()["total"]


def _dashboard_fixture(db, company):
    """Six people spread across college, year, stage and score."""
    _pool_row(db, company, email="a@x.com", college="IIT Madras",
              year_of_passing=2024, job_role="Backend Engineer", best_score=91.0)
    _pool_row(db, company, email="b@x.com", college="IIT Madras",
              year_of_passing=2024, job_role="Backend Engineer", best_score=72.5)
    _pool_row(db, company, email="c@x.com", college="IIT Madras",
              year_of_passing=2023, job_role="Data Analyst", best_score=55.0)
    _pool_row(db, company, email="d@x.com", college="NIT Trichy",
              year_of_passing=2024, job_role="Backend Engineer", best_score=40.0)
    _pool_row(db, company, email="e@x.com", college="NIT Trichy",
              year_of_passing=2022, job_role="Data Analyst")
    _pool_row(db, company, email="f@x.com", college="  BITS Pilani  ",
              year_of_passing=2024, job_role="Designer", best_score=100.0)


@pytest.mark.parametrize("params", [
    {},
    {"college": "IIT Madras"},
    {"year_of_passing": 2024},
    {"college": "IIT Madras,NIT Trichy", "year_of_passing": 2024},
    {"job_role": "Backend"},
    {"min_score": 70},
    {"stage": "new"},
    {"search": "Data"},
    {"college": "Nobody Went Here"},
])
def test_dashboard_total_always_matches_the_list_it_describes(
        client, db, hr_headers, hr_company, params):
    """The invariant the whole feature rests on.

    Both endpoints take the same PoolFilters dependency and the same predicate list, so
    this holds by construction — which is precisely why it is worth pinning down. The
    moment someone re-declares a filter on one side, this fails.
    """
    _dashboard_fixture(db, hr_company)
    assert _metrics(client, hr_headers, **params)["total"] == \
        _list_total(client, hr_headers, **params)


def test_pool_total_stays_whole_pool_under_a_filter(client, db, hr_headers, hr_company):
    """`total` narrows, `pool_total` does not — that pair is how the UI says
    "312 of 4,180" instead of leaving the reader to guess a filter is on."""
    _dashboard_fixture(db, hr_company)
    m = _metrics(client, hr_headers, college="IIT Madras")
    assert m["total"] == 3
    assert m["pool_total"] == 6


def test_the_scoring_backlog_ignores_the_filter(client, db, hr_headers, hr_company):
    """`unscored`/`unscoreable` drive "Rank all N", and score_backlog() ranks the whole
    backlog rather than the current view. A filtered count would promise something the
    button does not do."""
    _make_job(db, hr_company)
    _pool_row(db, hr_company, email="scored@x.com", college="IIT Madras",
              resume_text="text", best_score=70.0)
    _pool_row(db, hr_company, email="waiting@x.com", college="NIT Trichy",
              resume_text="text")
    _pool_row(db, hr_company, email="cannot@x.com", college="NIT Trichy")

    m = _metrics(client, hr_headers, college="IIT Madras")
    assert m["total"] == 1, "the filter should still narrow everything else"
    assert m["unscored"] == 1
    assert m["unscoreable"] == 1


def test_score_bands_are_filtered_and_never_absorb_the_unscored(
        client, db, hr_headers, hr_company):
    """A NULL score is "never ranked", not "scored zero" — putting it in the 0-9 bar
    would invent a cohort of terrible candidates out of people nobody has ranked."""
    _dashboard_fixture(db, hr_company)
    m = _metrics(client, hr_headers, college="IIT Madras")

    bands = {b["floor"]: b["count"] for b in m["score_bands"]}
    assert len(m["score_bands"]) == 10, "all ten bands present, zero-filled"
    assert bands[90] == 1        # 91.0
    assert bands[70] == 1        # 72.5
    assert bands[50] == 1        # 55.0
    assert bands[0] == 0, "nobody scored 0-9"
    assert sum(bands.values()) == 3

    unfiltered = _metrics(client, hr_headers)
    assert unfiltered["unscored_in_view"] == 1, "e@x.com has no score"
    assert {b["floor"]: b["count"] for b in unfiltered["score_bands"]}[0] == 0


def test_a_perfect_score_lands_in_the_top_band(client, db, hr_headers, hr_company):
    """FLOOR(100/10) is 10, which is an eleventh band that must not exist."""
    _pool_row(db, hr_company, email="perfect@x.com", best_score=100.0)
    bands = {b["floor"]: b["count"] for b in _metrics(client, hr_headers)["score_bands"]}
    assert bands[90] == 1
    assert max(bands) == 90


def test_band_boundaries_split_at_the_ten(client, db, hr_headers, hr_company):
    _pool_row(db, hr_company, email="just_under@x.com", best_score=79.99)
    _pool_row(db, hr_company, email="just_over@x.com", best_score=80.00)
    bands = {b["floor"]: b["count"] for b in _metrics(client, hr_headers)["score_bands"]}
    assert bands[70] == 1
    assert bands[80] == 1


def test_avg_score_follows_the_filter(client, db, hr_headers, hr_company):
    _dashboard_fixture(db, hr_company)
    assert _metrics(client, hr_headers, college="NIT Trichy")["avg_score"] == 40.0
    assert _metrics(client, hr_headers, college="Nobody Went Here")["avg_score"] is None


def test_top_colleges_and_roles_rank_within_the_filter(client, db, hr_headers, hr_company):
    _dashboard_fixture(db, hr_company)

    everyone = _metrics(client, hr_headers)
    colleges = [c["name"] for c in everyone["top_colleges"]]
    assert colleges[0] == "IIT Madras", "most common first"
    assert "BITS Pilani" in colleges, "grouped on TRIM(), so the padded row joins in"

    narrowed = _metrics(client, hr_headers, year_of_passing=2024)
    assert {c["name"]: c["count"] for c in narrowed["top_colleges"]} == {
        "IIT Madras": 2, "NIT Trichy": 1, "BITS Pilani": 1,
    }
    assert [r["name"] for r in narrowed["top_roles"]][0] == "Backend Engineer"


def test_the_trend_is_zero_filled(client, db, hr_headers, hr_company):
    """MySQL returns no row for a week nobody joined. Charting only what came back
    closes the gap and draws a flat line over a hiring freeze."""
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    _pool_row(db, hr_company, email="old@x.com", uploaded_at=now - timedelta(days=60))
    _pool_row(db, hr_company, email="new@x.com", uploaded_at=now)

    # "to: today" in a date picker, not this exact instant: uploaded_at is a
    # 0-precision DATETIME and MySQL rounds the fractional seconds up on insert.
    m = _metrics(client, hr_headers,
                 uploaded_from=(now - timedelta(days=70)).isoformat(),
                 uploaded_to=(now + timedelta(days=1)).isoformat())

    assert m["trend_granularity"] == "week", "a 71-day window is too short for months"
    counts = [p["count"] for p in m["added_over_time"]]
    assert sum(counts) == 2
    assert len(counts) > 2, "the empty weeks between them are present"
    assert 0 in counts


def test_the_trend_switches_to_months_over_a_long_window(client, db, hr_headers, hr_company):
    from datetime import datetime, timedelta

    now = datetime.utcnow()
    _pool_row(db, hr_company, email="a@x.com", uploaded_at=now)
    m = _metrics(client, hr_headers,
                 uploaded_from=(now - timedelta(days=300)).isoformat(),
                 uploaded_to=(now + timedelta(days=1)).isoformat())
    assert m["trend_granularity"] == "month"
    assert sum(p["count"] for p in m["added_over_time"]) == 1


def test_the_dashboard_is_scoped_to_the_company(client, db, hr_headers, hr_company):
    """Every new aggregate, not just `total` — one leak is enough."""
    other = Company(id=_uid(), name="Rival Ltd", slug=f"rival-{_uid()[:8]}")
    db.add(other)
    db.flush()
    _pool_row(db, other, email="theirs@x.com", college="Rival College",
              job_role="Their Role", best_score=88.0)
    _pool_row(db, hr_company, email="ours@x.com", college="Our College",
              job_role="Our Role", best_score=61.0)

    m = _metrics(client, hr_headers)
    assert m["total"] == 1
    assert m["pool_total"] == 1
    assert [c["name"] for c in m["top_colleges"]] == ["Our College"]
    assert [r["name"] for r in m["top_roles"]] == ["Our Role"]
    assert m["avg_score"] == 61.0
    assert sum(p["count"] for p in m["added_over_time"]) == 1
    assert sum(b["count"] for b in m["score_bands"]) == 1


def test_an_empty_slice_returns_a_renderable_shape(client, db, hr_headers, hr_company):
    """A filter matching nobody must still return every series, so the UI shows an
    empty state instead of crashing on a missing key."""
    _dashboard_fixture(db, hr_company)
    m = _metrics(client, hr_headers, college="Nobody Went Here")

    assert m["total"] == 0
    assert m["outcomes"] == []
    assert m["sources"] == []
    assert m["top_colleges"] == []
    assert m["top_roles"] == []
    assert len(m["score_bands"]) == 10 and all(b["count"] == 0 for b in m["score_bands"])
    assert all(p["count"] == 0 for p in m["added_over_time"])
    assert m["avg_score"] is None


def test_outcome_counts_survive_a_filter(client, db, hr_headers, hr_company):
    """The single-pass SUM(CASE) rewrite has to stay filterable, and the buckets have
    to stay exclusive within the filtered set."""
    job = _make_job(db, hr_company)
    user = _make_registered_user(db, hr_company, "applicant@x.com")
    db.add(Candidate(id=_uid(), company_id=hr_company.id, user_id=user.id,
                     email="applicant@x.com", first_name="Anjali", last_name="Rao",
                     job_id=job.id, current_stage="hr_review"))
    db.flush()
    _pool_row(db, hr_company, email="applicant@x.com", source_user_id=user.id,
              college="IIT Madras")
    _pool_row(db, hr_company, email="quiet@x.com", college="NIT Trichy")

    inside = _metrics(client, hr_headers, college="IIT Madras")
    assert {o["name"]: o["count"] for o in inside["outcomes"]} == {"in_process": 1}

    outside = _metrics(client, hr_headers, college="NIT Trichy")
    assert {o["name"]: o["count"] for o in outside["outcomes"]} == {"never_applied": 1}

    everyone = _metrics(client, hr_headers)
    assert sum(o["count"] for o in everyone["outcomes"]) == everyone["total"]


def test_the_list_rejects_nonsense_paging(client, db, hr_headers, hr_company):
    """`limit=-1` used to reach MySQL as `LIMIT -1`. The dashboard re-queries on every
    keystroke, so the list's parameters are worth bounding."""
    assert client.get("/api/v1/hr/resume-pool", headers=hr_headers,
                      params={"limit": -1}).status_code == 422
    assert client.get("/api/v1/hr/resume-pool", headers=hr_headers,
                      params={"skip": -5}).status_code == 422
    assert client.get("/api/v1/hr/resume-pool", headers=hr_headers,
                      params={"limit": 5000}).status_code == 422


# ── Skills: supply vs demand ─────────────────────────────────────────────────
#
# The load-bearing property is not any single count: it is that this tab agrees with
# the Has/Gaps chips on the candidate card underneath it. Four different skill-matching
# rules are live in this repo, and the tab mirrors exactly one of them.

def _skills(client, hr_headers, **params):
    r = client.get("/api/v1/hr/talent-pool/skills", headers=hr_headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _row(report, skill):
    """One row of the gap table, by canonical skill name."""
    return next((r for r in report["demand"] if r["skill"].lower() == skill.lower()), None)


def _job_wanting(db, company, skills, title="Platform Engineer", status="active"):
    job = _make_job(db, company, title=title)
    job.required_skills = json.dumps(skills)
    job.status = status
    db.flush()
    return job


def test_skill_aliases_resolve_supply_against_demand(client, db, hr_headers, hr_company):
    """A resume saying "k8s" covers a role asking for "Kubernetes".

    Both sides go through the ATS's own alias map, so the tab cannot disagree with the
    score about whether this person has the skill.
    """
    _job_wanting(db, hr_company, ["Kubernetes", "Docker"])
    _pool_row(db, hr_company, email="a@x.com", extracted_skills=json.dumps(["k8s"]))

    k8s = _row(_skills(client, hr_headers), "Kubernetes")
    assert k8s is not None, "a demanded skill must appear even when nobody has it"
    assert k8s["candidates"] == 1, "k8s should have resolved to Kubernetes"
    assert k8s["roles"] == 1


def test_a_cluster_sibling_is_its_own_state_not_a_match(client, db, hr_headers, hr_company):
    """Docker for Kubernetes is neither "has it" nor "nothing there".

    The ATS computes this third state and then drops it at persist time, so it is
    invisible everywhere else in the app. It must not be silently folded into either
    bucket here.
    """
    _job_wanting(db, hr_company, ["Kubernetes"])
    _pool_row(db, hr_company, email="a@x.com", extracted_skills=json.dumps(["Docker"]))

    k8s = _row(_skills(client, hr_headers), "Kubernetes")
    assert k8s["candidates"] == 0, "Docker is not Kubernetes"
    assert k8s["related"] == 1
    assert "Docker" in k8s["related_examples"]
    # The grouping is named so a recruiter can judge it rather than trust it.
    assert k8s["related_cluster"]


def test_the_headline_counts_only_the_truly_unheld(client, db, hr_headers, hr_company):
    """`fully_unmet` is the number to act on, so a cluster near-miss must not inflate it."""
    _job_wanting(db, hr_company, ["Kubernetes", "Rust"])
    _pool_row(db, hr_company, email="a@x.com", extracted_skills=json.dumps(["Docker"]))

    r = _skills(client, hr_headers)
    assert r["demanded_skills"] == 2
    assert r["fully_unmet"] == 1, "Kubernetes has a related holder; only Rust is unmet"


def test_the_gap_table_reads_worst_first(client, db, hr_headers, hr_company):
    """The working order. Ties break on demand: a skill three roles need and nobody has
    outranks one role needs and nobody has."""
    _job_wanting(db, hr_company, ["Python", "Rust"], title="A")
    _job_wanting(db, hr_company, ["Rust"], title="B")
    _pool_row(db, hr_company, email="a@x.com", extracted_skills=json.dumps(["Python"]))

    demand = _skills(client, hr_headers)["demand"]
    assert [d["skill"] for d in demand] == ["Rust", "Python"]
    assert demand[0]["roles"] == 2


def test_one_person_counts_once_however_they_spell_it(client, db, hr_headers, hr_company):
    """A verbose resume must not outvote real people."""
    _job_wanting(db, hr_company, ["React"])
    _pool_row(db, hr_company, email="a@x.com",
              extracted_skills=json.dumps(["React", "react", "ReactJS", "React.js"]))

    assert _row(_skills(client, hr_headers), "React")["candidates"] == 1


def test_demand_ignores_roles_that_are_not_open(client, db, hr_headers, hr_company):
    """A draft or closed posting is not a staffing need."""
    _job_wanting(db, hr_company, ["Rust"], title="Draft role", status="draft")
    _job_wanting(db, hr_company, ["Python"], title="Live role")

    r = _skills(client, hr_headers)
    assert r["active_roles"] == 1
    assert _row(r, "Rust") is None
    assert _row(r, "Python") is not None


def test_malformed_skill_columns_degrade_rather_than_500(client, db, hr_headers, hr_company):
    """`admin_routes.py:751` writes the raw Pydantic value with no json.dumps, so a
    Python repr() can reach these columns. One bad row must not take out the tab — and
    where the real names are recoverable, they should be recovered."""
    _job_wanting(db, hr_company, ["Python"])
    _pool_row(db, hr_company, email="repr@x.com", extracted_skills="['Python', 'React']")
    _pool_row(db, hr_company, email="junk@x.com", extracted_skills="not json at all")
    _pool_row(db, hr_company, email="empty@x.com", extracted_skills=None)

    r = _skills(client, hr_headers)
    assert _row(r, "Python")["candidates"] == 1, "the repr() row should still be readable"
    assert r["no_skills_on_record"] == 1, "only the NULL row has nothing on record"


def test_people_with_no_skills_recorded_are_not_evidence_of_a_gap(
        client, db, hr_headers, hr_company):
    """An unparsed resume is not a person who lacks every skill."""
    _job_wanting(db, hr_company, ["Python"])
    _pool_row(db, hr_company, email="known@x.com", extracted_skills=json.dumps(["Python"]))
    _pool_row(db, hr_company, email="unread@x.com", extracted_skills=None)

    r = _skills(client, hr_headers)
    assert r["people"] == 2
    assert r["people_with_skills"] == 1
    assert r["no_skills_on_record"] == 1
    # Coverage is a share of the people we actually know something about.
    assert _row(r, "Python")["coverage"] == 100.0


def test_the_filter_narrows_supply_but_never_demand(client, db, hr_headers, hr_company):
    """Filtering to 2024 graduates does not change what the open roles need. If demand
    shrank with the filter, the gap would vanish as you searched."""
    _job_wanting(db, hr_company, ["Python", "Rust"])
    _pool_row(db, hr_company, email="a@x.com", college="IIT Madras",
              extracted_skills=json.dumps(["Python"]))
    _pool_row(db, hr_company, email="b@x.com", college="NIT Trichy",
              extracted_skills=json.dumps(["Python"]))

    everyone = _skills(client, hr_headers)
    narrowed = _skills(client, hr_headers, college="IIT Madras")

    assert _row(everyone, "Python")["candidates"] == 2
    assert _row(narrowed, "Python")["candidates"] == 1
    assert narrowed["active_roles"] == everyone["active_roles"] == 1
    assert narrowed["demanded_skills"] == everyone["demanded_skills"] == 2


def test_skills_are_scoped_to_the_company(client, db, hr_headers, hr_company):
    """Supply and demand both — another company's roles must not appear as our need."""
    other = Company(id=_uid(), name="Rival Ltd", slug=f"rival-{_uid()[:8]}")
    db.add(other)
    db.flush()
    _job_wanting(db, other, ["Their Skill"], title="Their role")
    _job_wanting(db, hr_company, ["Python"], title="Our role")
    _pool_row(db, other, email="theirs@x.com", extracted_skills=json.dumps(["Their Skill"]))
    _pool_row(db, hr_company, email="ours@x.com", extracted_skills=json.dumps(["Python"]))

    r = _skills(client, hr_headers)
    assert r["active_roles"] == 1
    assert r["people"] == 1
    assert _row(r, "Their Skill") is None
    assert [t["skill"] for t in r["top_skills"]] == ["Python"]


def test_top_skills_show_what_no_role_asks_for(client, db, hr_headers, hr_company):
    """The gap table structurally cannot show a strength nobody is hiring for."""
    _job_wanting(db, hr_company, ["Python"])
    _pool_row(db, hr_company, email="a@x.com",
              extracted_skills=json.dumps(["Python", "Figma"]))

    r = _skills(client, hr_headers)
    assert _row(r, "Figma") is None, "no role asks for it, so it is not a gap"
    assert "Figma" in [t["skill"] for t in r["top_skills"]]


# ── The `skill` filter behind the drill-down ─────────────────────────────────

def test_the_skill_filter_matches_whole_entries_not_substrings(
        client, db, hr_headers, hr_company):
    """`search=java` matches "javascript" inside the JSON text, which is fine for a
    search box and useless for a drill-down claiming to list the people who have Java."""
    _pool_row(db, hr_company, email="js@x.com", extracted_skills=json.dumps(["JavaScript"]))
    _pool_row(db, hr_company, email="java@x.com", extracted_skills=json.dumps(["Java"]))

    by_skill = client.get("/api/v1/hr/resume-pool", headers=hr_headers,
                          params={"skill": "Java"}).json()
    assert [e["email"] for e in by_skill["results"]] == ["java@x.com"]
    assert by_skill["total"] == 1

    by_search = client.get("/api/v1/hr/resume-pool", headers=hr_headers,
                           params={"search": "Java"}).json()
    assert by_search["total"] == 2, "search is deliberately the blunt one"


def test_the_skill_filter_reaches_the_dashboard_too(client, db, hr_headers, hr_company):
    """It lives in the shared predicate list, so every chart narrows with it and the
    two views cannot disagree about who is in scope."""
    _pool_row(db, hr_company, email="a@x.com", extracted_skills=json.dumps(["Python"]))
    _pool_row(db, hr_company, email="b@x.com", extracted_skills=json.dumps(["Rust"]))

    m = client.get("/api/v1/hr/talent-pool/metrics", headers=hr_headers,
                   params={"skill": "Python"}).json()
    lst = client.get("/api/v1/hr/resume-pool", headers=hr_headers,
                     params={"skill": "Python"}).json()
    assert m["total"] == lst["total"] == 1
    assert m["pool_total"] == 2


def test_the_skill_filter_survives_hostile_input(client, db, hr_headers, hr_company):
    """The term is interpolated into a LIKE pattern, so wildcards and quotes must not
    turn a filter into a match-everything."""
    _pool_row(db, hr_company, email="a@x.com", extracted_skills=json.dumps(["Python"]))

    # `_` is a single-character LIKE wildcard too, so an unescaped "Pytho_" would match
    # Python — and a term stripped to nothing would drop the predicate entirely, which
    # is a filter that silently matches everyone.
    for term in ('%', '"', '\\', '%%', 'Py%hon', 'Pytho_', '_ython', '%thon', '___%'):
        r = client.get("/api/v1/hr/resume-pool", headers=hr_headers, params={"skill": term})
        assert r.status_code == 200, f"{term!r} -> {r.text}"
        assert r.json()["total"] == 0, f"{term!r} matched somebody"

    # ...and the real name still works, so the escaping did not break the filter itself.
    assert client.get("/api/v1/hr/resume-pool", headers=hr_headers,
                      params={"skill": "Python"}).json()["total"] == 1
