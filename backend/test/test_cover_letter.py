"""
AI cover-letter generation tests.

Contract tests (run in CI): auth required, 404 for unknown job, and a graceful
503 when the Ollama service raises — verified by monkeypatching generate_text so
the path is deterministic without a live LLM.

The real end-to-end generation test is marked `slow` (needs Ollama) and is
excluded from the CI run via `-m "not slow"`, matching the suite convention.
"""
import pytest


def _create_active_job(client, hr_headers):
    resp = client.post("/api/v1/hr/jobs", headers=hr_headers, json={
        "title": "Software Engineer",
        "description": "Build great software",
        "requirements": "Python, FastAPI",
        "location": "Remote",
        "employment_type": "full_time",
        "experience_years": 2,
        "required_skills": ["Python", "FastAPI"],
        "salary_min": 50000,
        "salary_max": 90000,
    })
    assert resp.status_code in (200, 201), f"Job creation failed: {resp.text}"
    job = resp.json()
    job_id = job.get("id") or job.get("job_id")
    ap = client.put(f"/api/v1/hr/jobs/{job_id}/approve", headers=hr_headers, json={
        "salary_min": 50000, "salary_max": 90000, "location": "Remote",
    })
    # POST /hr/jobs already returns an active job, so this approve is a belt-and-braces
    # call that 400s with "Job is already active". Either outcome is fine — what has to
    # hold is that the job really ended up active, since a job left as 'draft' would
    # resurface later as an unexplained 404 from whichever endpoint filters on status.
    assert job.get("status") == "active" or ap.status_code == 200, (
        f"Job is not active. create={job.get('status')!r} approve={ap.status_code} {ap.text}"
    )
    return job_id


@pytest.fixture()
def candidate_headers_same_company(client, db, hr_headers, candidate_headers):
    """A candidate in the same company as the HR who owns the job.

    Cover-letter generation is tenant-scoped, so the candidate must belong to the
    company that owns the job — which is what production does: candidates reach a
    client company through its portal key or tenant domain and are assigned that
    company_id (routes/auth_routes.py:23-36).

    The stock fixtures don't line up. hr_headers builds a fresh "Test Company"
    (conftest.py:196-205), while a bare candidate registration resolves to the
    platform org (routes/auth_routes.py:111-118). Align them, the same way
    admin_headers reassigns company_id after login (conftest.py:178-186).

    Reassigning after login is enough because get_current_user re-reads the User
    row on every request (src/auth.py:217) instead of trusting claims baked into
    the JWT, so no new token is needed.
    """
    from src.models.models import User
    hr = db.query(User).filter(User.email == "hr_test@test.com").first()
    cand = db.query(User).filter(User.email == "candidate_test@test.com").first()
    cand.company_id = hr.company_id
    db.flush()
    return candidate_headers


@pytest.mark.candidate
def test_cover_letter_requires_auth(client):
    assert client.post("/api/v1/candidate/cover-letter/generate",
                       json={"job_id": "x"}).status_code in (401, 403)


@pytest.mark.candidate
def test_cover_letter_unknown_job_404(client, candidate_headers):
    resp = client.post("/api/v1/candidate/cover-letter/generate", headers=candidate_headers,
                       json={"job_id": "does-not-exist"})
    assert resp.status_code == 404


@pytest.mark.candidate
def test_cover_letter_503_when_ollama_down(client, hr_headers, candidate_headers_same_company, monkeypatch):
    """If the AI service raises, the endpoint degrades to a 503 (never a 500)."""
    job_id = _create_active_job(client, hr_headers)

    from src.services.gemini_service import gemini_service

    async def _boom(*args, **kwargs):
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr(gemini_service, "generate_text", _boom)

    resp = client.post("/api/v1/candidate/cover-letter/generate", headers=candidate_headers_same_company,
                       json={"job_id": job_id, "tone": "professional"})
    assert resp.status_code == 503
    assert "unavailable" in resp.json()["detail"].lower()


@pytest.mark.candidate
def test_cover_letter_success_when_ai_returns(client, hr_headers, candidate_headers_same_company, monkeypatch):
    """Happy path with the LLM stubbed: returns prose + the model name."""
    job_id = _create_active_job(client, hr_headers)

    from src.services.gemini_service import gemini_service

    async def _ok(*args, **kwargs):
        return "Dear Hiring Manager, I am excited to apply..."

    monkeypatch.setattr(gemini_service, "generate_text", _ok)

    resp = client.post("/api/v1/candidate/cover-letter/generate", headers=candidate_headers_same_company,
                       json={"job_id": job_id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["cover_letter"].startswith("Dear Hiring Manager")
    assert "model" in body


@pytest.mark.candidate
def test_cover_letter_rejects_other_companys_job(client, hr_headers, candidate_headers):
    """A candidate must not read another company's job through this endpoint.

    The job's title, description and requirements are fed into the prompt and come
    back inside the generated prose, so an unscoped lookup hands over the contents
    of any job in any tenant. Uses the *unaligned* candidate_headers deliberately:
    it resolves to the platform org while hr_headers owns a separate "Test Company",
    so this really is cross-tenant. Fails against the pre-930a234 endpoint.
    """
    job_id = _create_active_job(client, hr_headers)
    resp = client.post("/api/v1/candidate/cover-letter/generate", headers=candidate_headers,
                       json={"job_id": job_id})
    assert resp.status_code == 404, resp.text


@pytest.mark.candidate
@pytest.mark.slow
def test_cover_letter_real_generation(client, hr_headers, candidate_headers_same_company):
    """End-to-end against a live Ollama. Excluded from CI (slow); skipped if down."""
    job_id = _create_active_job(client, hr_headers)
    resp = client.post("/api/v1/candidate/cover-letter/generate", headers=candidate_headers_same_company,
                       json={"job_id": job_id})
    if resp.status_code == 503:
        pytest.skip("Ollama not available in this environment")
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["cover_letter"]) > 50
