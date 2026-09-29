"""
Uploaded files are private (routes/file_routes.py, src/services/file_access.py).

Resumes, cover letters, photos and voice answers used to be static files at /resumes,
/uploads and /cover_letters, downloadable by anyone holding the path. These pin that
they are now served only through row-keyed routes, only to the people the rules name,
and that the links API responses hand out are signed per viewer.
"""
import time
import uuid

import pytest

from src.services.storage import storage

PASSWORD = "Pass@1234"


# ── fixtures ────────────────────────────────────────────────────────────────

def _company(db, name):
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"fa-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c


def _user(db, role, company, email=None, active=True):
    from src.auth import get_password_hash
    from src.models.models import User
    u = User(id=str(uuid.uuid4()), email=email or f"{role}_{uuid.uuid4().hex[:8]}@filesaccess.com",
             password_hash=get_password_hash(PASSWORD), role=role, first_name=role.title(),
             last_name="Tester", is_active=active, email_verified=True,
             company_id=company.id if company else None)
    db.add(u)
    db.flush()
    return u


def _headers(client, user):
    r = client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _key(folder, ext, content=b"%PDF-1.4 test"):
    key = f"{folder}/fa_test_{uuid.uuid4().hex}{ext}"
    storage.save_bytes(content, key)
    return key


@pytest.fixture()
def world(db):
    """Two companies. In A: a job owned by one HR, a candidate who applied with a
    resume and photo, an HR with no access to the job, an assigned and an unassigned
    interviewer, an admin. In B: an HR and a candidate."""
    from src.models.models import Candidate, HRProfile, Interview, JobPosting
    from src.models.question_models import CommunicationAnswer

    a, b = _company(db, "Files A"), _company(db, "Files B")
    owner_hr, other_hr = _user(db, "hr", a), _user(db, "hr", a)
    prof = HRProfile(id=str(uuid.uuid4()), user_id=owner_hr.id, company_id=a.id, employee_id="E1")
    db.add_all([prof, HRProfile(id=str(uuid.uuid4()), user_id=other_hr.id, company_id=a.id,
                                employee_id="E2")])
    db.flush()
    job = JobPosting(id=str(uuid.uuid4()), company_id=a.id, title="Dev", description="d",
                     requirements="r", status="active", hr_id=prof.id)
    db.add(job)
    db.flush()

    cand_user = _user(db, "candidate", a)
    resume, photo = _key("resumes", ".pdf"), _key("uploads/photos", ".png", b"\x89PNG fake")
    app = Candidate(id=str(uuid.uuid4()), company_id=a.id, user_id=cand_user.id, job_id=job.id,
                    email=cand_user.email, first_name="Cand", last_name="Idate",
                    resume_path=resume, photo_path=photo, current_stage="applied")
    db.add(app)
    db.flush()

    assigned_iv, unassigned_iv = _user(db, "interviewer", a), _user(db, "interviewer", a)
    iv = Interview(company_id=a.id, title="Round 1", job_id=job.id, candidate_id=app.id,
                   status="SCHEDULED", interviewer_email=assigned_iv.email)
    db.add(iv)
    db.flush()

    audio = _key("uploads/voice_answers", ".webm", b"webm fake")
    from src.models.question_models import Question
    q = Question(interview_id=iv.id, question_text="Talk", question_type="communication",
                 difficulty_level="easy")
    db.add(q)
    db.flush()
    answer = CommunicationAnswer(interview_id=iv.id, question_id=q.id, candidate_id=app.id,
                                 audio_path=audio)
    db.add(answer)
    db.flush()

    w = dict(a=a, b=b, owner_hr=owner_hr, other_hr=other_hr, cand_user=cand_user, app=app,
             assigned_iv=assigned_iv, unassigned_iv=unassigned_iv, admin=_user(db, "admin", a),
             b_hr=_user(db, "hr", b), b_cand=_user(db, "candidate", b), interview=iv,
             answer=answer, keys=[resume, photo, audio])
    yield w
    for k in w["keys"]:
        try:
            storage.delete(k)
        except Exception:
            pass


def _app_url(w, kind="resume"):
    return f"/api/v1/files/applications/{w['app'].id}/{kind}"


# ── the old public paths are gone ───────────────────────────────────────────

@pytest.mark.security
@pytest.mark.parametrize("folder", ["resumes", "uploads/photos", "cover_letters"])
def test_uploaded_files_are_not_served_statically(client, folder):
    key = _key(folder, ".pdf")
    try:
        r = client.get(f"/{key}")
        assert r.status_code == 404
        assert b"%PDF" not in r.content
    finally:
        storage.delete(key)


@pytest.mark.security
def test_logos_stay_public(client):
    key = _key("logos", ".png", b"\x89PNG logo")
    try:
        assert client.get(f"/{key}").status_code == 200
    finally:
        storage.delete(key)


# ── application files ───────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.parametrize("who,allowed", [
    ("cand_user", True),        # their own application
    ("owner_hr", True),         # HR on the job
    ("admin", True),            # admin of the company
    ("assigned_iv", True),      # interviewer on this candidate's interview
    ("other_hr", False),        # HR in the company but not on the job
    ("unassigned_iv", False),   # interviewer not on the interview
    ("b_hr", False),            # another company
    ("b_cand", False),          # another candidate
])
def test_application_resume_access(client, world, who, allowed):
    r = client.get(_app_url(world), headers=_headers(client, world[who]))
    assert r.status_code == (200 if allowed else 404), r.text
    if allowed:
        assert r.content.startswith(b"%PDF")


@pytest.mark.security
def test_anonymous_download_is_refused(client, world):
    assert client.get(_app_url(world)).status_code == 404


@pytest.mark.security
def test_superadmin_cannot_use_file_routes(client, world, db):
    from src.auth import create_access_token
    sa = _user(db, "superadmin", None)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': sa.id})}"}
    assert client.get(_app_url(world), headers=headers).status_code in (401, 403, 404)


@pytest.mark.security
def test_download_headers(client, world):
    r = client.get(_app_url(world), headers=_headers(client, world["owner_hr"]))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in r.headers["cache-control"]
    cd = r.headers["content-disposition"]
    assert cd.startswith("inline;") and "filename*=UTF-8''" in cd


@pytest.mark.security
def test_html_upload_is_forced_to_download_and_sandboxed(client, world, db):
    key = _key("resumes", ".html", b"<script>alert(1)</script>")
    world["keys"].append(key)
    world["app"].resume_path = key
    db.flush()
    r = client.get(_app_url(world), headers=_headers(client, world["owner_hr"]))
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment;")
    assert "sandbox" in r.headers["content-security-policy"]


def test_content_disposition_escapes_quotes_and_keeps_unicode():
    from src.services.file_access import content_disposition
    v = content_disposition('Zoë "x";.pdf', inline=False)
    assert v.startswith('attachment; filename="Zo_ _x__.pdf"')
    assert "filename*=UTF-8''Zo%C3%AB%20%22x%22%3B.pdf" in v


# ── signed links ────────────────────────────────────────────────────────────

@pytest.mark.security
def test_signed_link_works_without_a_header(client, world):
    from src.services.file_access import application_file_url
    url = application_file_url(world["app"], "resume", world["owner_hr"])
    assert client.get(url).status_code == 200


@pytest.mark.security
def test_signed_link_is_bound_to_path_user_and_expiry(client, world):
    from src.services.file_access import application_file_url, sign_path
    url = application_file_url(world["app"], "resume", world["owner_hr"])
    path, query = url.split("?")
    # Another viewer's id with the same signature.
    forged = url.replace(f"u={world['owner_hr'].id}", f"u={world['admin'].id}")
    assert client.get(forged).status_code == 404
    # The signature moved onto another file.
    assert client.get(_app_url(world, "photo") + "?" + query).status_code == 404
    # Expired.
    assert client.get(sign_path(path, world["owner_hr"].id, ttl=-1)).status_code == 404
    # Tampered.
    assert client.get(url[:-4] + "0000").status_code == 404


@pytest.mark.security
def test_signed_link_rechecks_access(client, world, db):
    """A link for someone who has since lost access stops working."""
    from src.services.file_access import application_file_url
    url = application_file_url(world["app"], "resume", world["owner_hr"])
    world["owner_hr"].is_active = False
    db.flush()
    assert client.get(url).status_code == 404


@pytest.mark.security
def test_signed_link_for_viewer_without_access_fails(client, world):
    from src.services.file_access import application_file_url
    url = application_file_url(world["app"], "resume", world["b_hr"])
    assert client.get(url).status_code == 404


# ── profile files ───────────────────────────────────────────────────────────

@pytest.mark.security
def test_profile_files_follow_the_relationship(client, world, db):
    from src.models.models import CandidateProfileExt
    key = _key("uploads/resumes", ".pdf")
    world["keys"].append(key)
    db.add(CandidateProfileExt(id=str(uuid.uuid4()), user_id=world["cand_user"].id,
                               company_id=world["a"].id, resume_path=key))
    db.flush()
    url = f"/api/v1/files/profiles/{world['cand_user'].id}/resume"
    assert client.get(url, headers=_headers(client, world["cand_user"])).status_code == 200
    assert client.get(url, headers=_headers(client, world["owner_hr"])).status_code == 200
    assert client.get(url, headers=_headers(client, world["other_hr"])).status_code == 404
    assert client.get(url, headers=_headers(client, world["b_hr"])).status_code == 404
    assert client.get(url, headers=_headers(client, world["b_cand"])).status_code == 404


@pytest.mark.security
def test_profile_response_carries_signed_links_not_keys(client, world, db):
    from src.models.models import CandidateProfileExt
    key = _key("uploads/resumes", ".pdf")
    world["keys"].append(key)
    db.add(CandidateProfileExt(id=str(uuid.uuid4()), user_id=world["cand_user"].id,
                               company_id=world["a"].id, resume_path=key))
    db.flush()
    body = client.get("/api/v1/candidate/profile", headers=_headers(client, world["cand_user"])).json()
    assert "resume_path" not in body and key not in str(body)
    assert body["resume_url"].startswith(f"/api/v1/files/profiles/{world['cand_user'].id}/resume?")
    assert client.get(body["resume_url"]).status_code == 200


# ── voice answers ───────────────────────────────────────────────────────────

@pytest.mark.security
@pytest.mark.parametrize("who,allowed", [
    ("cand_user", True), ("owner_hr", True), ("assigned_iv", True),
    ("other_hr", False), ("unassigned_iv", False), ("b_hr", False), ("b_cand", False),
])
def test_voice_answer_access(client, world, who, allowed):
    r = client.get(f"/api/v1/files/voice-answers/{world['answer'].id}",
                   headers=_headers(client, world[who]))
    assert r.status_code == (200 if allowed else 404), r.text
