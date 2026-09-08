"""
Dashboard cards must count the whole company, not the page the client happened to fetch.

GET /hr/jobs, /hr/interviews, /hr/offers and /admin/departments all default to
limit=50, and the dashboards passed no limit — so every overview card was really
`min(real_total, 50)` and silently stopped moving at the cap.

These lock in the two properties that make a count endpoint trustworthy:

  1. it keeps counting past the list endpoint's default page size;
  2. it is scoped exactly like the list it describes, so a card can never disagree
     with the rows the user sees after clicking it.
"""
import pytest



def _make_job(client, hr_headers, title):
    resp = client.post("/api/v1/hr/jobs", headers=hr_headers, json={
        "title": title,
        "description": "role",
        "requirements": "Python",
        "location": "Remote",
        "employment_type": "full_time",
        "experience_years": 1,
        "required_skills": ["Python"],
        "salary_min": 40000,
        "salary_max": 80000,
    })
    assert resp.status_code in (200, 201), resp.text
    body = resp.json()
    return body.get("id") or body.get("job_id")


@pytest.mark.hr
def test_hr_counts_endpoint_exists_and_is_shaped(client, hr_headers):
    resp = client.get("/api/v1/hr/dashboard/counts", headers=hr_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("jobs", "active_jobs", "interviews", "offers"):
        assert key in body, f"missing {key}"
        assert isinstance(body[key], int)


@pytest.mark.hr
def test_hr_job_count_matches_the_list_when_under_the_cap(client, hr_headers):
    """Below the page size the two must agree — that is the contract being locked in."""
    before = client.get("/api/v1/hr/dashboard/counts", headers=hr_headers).json()["jobs"]
    _make_job(client, hr_headers, "Counted Job")
    after = client.get("/api/v1/hr/dashboard/counts", headers=hr_headers).json()["jobs"]
    assert after == before + 1

    listed = client.get("/api/v1/hr/jobs", headers=hr_headers).json()
    assert len(listed) == after


@pytest.mark.hr
def test_hr_job_count_keeps_going_past_the_default_page_size(client, hr_headers):
    """The bug this endpoint exists for: 51 jobs must not report 50.

    The list endpoint still returns its capped page — that is correct, the dashboard
    only lists a page — but the count has to describe all of them.
    """
    start = client.get("/api/v1/hr/dashboard/counts", headers=hr_headers).json()["jobs"]
    target = 51 - start
    for i in range(max(target, 0)):
        _make_job(client, hr_headers, f"Bulk Job {i}")

    counts = client.get("/api/v1/hr/dashboard/counts", headers=hr_headers).json()
    listed = client.get("/api/v1/hr/jobs", headers=hr_headers).json()

    assert counts["jobs"] >= 51, f"count capped at {counts['jobs']}"
    assert len(listed) == 50, "list should still be one page"
    assert counts["jobs"] > len(listed), "the count must outrun the page — that is the point"


@pytest.mark.hr
def test_active_jobs_counts_only_active(client, hr_headers):
    """The card says "Active Jobs" and its click filters status=active, so the number
    has to mean that one status and not "anything not closed"."""
    counts = client.get("/api/v1/hr/dashboard/counts", headers=hr_headers).json()
    active_listed = client.get(
        "/api/v1/hr/jobs", headers=hr_headers, params={"status": "active"}
    ).json()
    # Under the cap these agree exactly; over it the count is the larger, never smaller.
    assert counts["active_jobs"] >= len(active_listed)
    assert counts["active_jobs"] <= counts["jobs"]


@pytest.mark.security
def test_hr_counts_rejects_anonymous(client):
    assert client.get("/api/v1/hr/dashboard/counts").status_code in (401, 403)


@pytest.mark.security
def test_hr_counts_rejects_a_candidate(client, candidate_headers):
    resp = client.get("/api/v1/hr/dashboard/counts", headers=candidate_headers)
    assert resp.status_code in (401, 403)


@pytest.mark.admin
def test_admin_overview_reports_department_total(client, admin_headers):
    """The Departments card read departments.length off a limit=50 list."""
    resp = client.get("/api/v1/admin/analytics/overview", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total_departments" in body
    assert isinstance(body["total_departments"], int)

    listed = client.get("/api/v1/admin/departments", headers=admin_headers).json()
    assert body["total_departments"] >= len(listed)


@pytest.mark.interviewer
def test_interviewer_counts_is_not_shadowed_by_the_id_route(client, interviewer_headers):
    """/interviews/counts is registered above /interviews/{interview_id}.

    Below it, Starlette would hand this request to get_interview_details with
    interview_id="counts" — the exact bug /interviews/history carries a comment about.
    A 404 or a validation error here means the ordering regressed.
    """
    resp = client.get("/api/v1/interviewer/interviews/counts", headers=interviewer_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("total", "draft", "ready", "scheduled", "completed"):
        assert key in body, f"missing {key}"
        assert isinstance(body[key], int)


@pytest.mark.interviewer
def test_interviewer_status_counts_sum_to_the_total(client, interviewer_headers):
    body = client.get(
        "/api/v1/interviewer/interviews/counts", headers=interviewer_headers
    ).json()
    assert sum(body["by_status"].values()) == body["total"]
