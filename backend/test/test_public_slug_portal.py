"""
Brand-name company portals: ``career.innomaxskills.com/<slug>``.

A company is identified in a URL by its **slug**, a public identifier, rather than by
``Company.api_key`` — an ``sk_live_`` secret that used to travel in every shared link,
access-log line and ``Referer`` header.

Two things need holding still:

* the slug path returns exactly what the key path did, so migrating a tenant is not a
  behaviour change; and
* the endpoints a slug can now reach do not leak more than they did when reaching them
  needed a 43-character secret. A slug is guessable; the key was not.
"""
import uuid

import pytest


def _make_company(db, name: str, slug: str, *, api_key: str = None, active: bool = True):
    from src.models.models import Company
    company = Company(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug,
        api_key=api_key or ("sk_live_" + uuid.uuid4().hex),
        is_active=active,
    )
    db.add(company)
    db.flush()
    return company


def _make_job(db, company_id: str, title: str = "Backend Engineer", status: str = "active"):
    from src.models.models import JobPosting
    job = JobPosting(
        id=str(uuid.uuid4()),
        title=title,
        description="Build things." * 40,
        requirements="Python",
        status=status,
        company_id=company_id,
    )
    db.add(job)
    db.flush()
    return job


# ── the slug path must match the key path ──────────────────────────────────

@pytest.mark.auth
def test_slug_and_key_return_identical_payloads(client, db):
    """?c=<slug> and ?key=<api_key> resolve the same company and the same body.

    This is what makes the migration safe: the embed a client already has on their site
    keeps rendering exactly the same job board after they switch to the slug URL.
    """
    company = _make_company(db, "Acme Corp", "acme-corp")
    _make_job(db, company.id)

    by_slug = client.get("/api/v1/public/jobs?c=acme-corp")
    by_key = client.get(f"/api/v1/public/jobs?key={company.api_key}")

    assert by_slug.status_code == by_key.status_code == 200, by_slug.text
    assert by_slug.json() == by_key.json()
    assert by_slug.json()["company"]["slug"] == "acme-corp"


@pytest.mark.auth
def test_company_info_by_slug(client, db):
    _make_company(db, "Acme Two", "acme-two")
    resp = client.get("/api/v1/public/company-info?c=acme-two")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Acme Two"
    assert resp.json()["slug"] == "acme-two"


@pytest.mark.auth
def test_slug_lookup_is_case_insensitive(client, db):
    """A slug printed on a job ad gets typed back in whatever case the reader likes."""
    _make_company(db, "Acme Three", "acme-three")
    assert client.get("/api/v1/public/jobs?c=ACME-THREE").status_code == 200
    assert client.get("/api/v1/public/jobs?c=Acme-Three").status_code == 200


@pytest.mark.auth
def test_unknown_and_inactive_slugs_are_404(client, db):
    """404, not 401 — a slug is an identifier, so a miss is "no such company"."""
    _make_company(db, "Gone Ltd", "gone-ltd", active=False)
    assert client.get("/api/v1/public/jobs?c=no-such-company").status_code == 404
    assert client.get("/api/v1/public/jobs?c=gone-ltd").status_code == 404


@pytest.mark.auth
def test_platform_company_is_not_addressable_as_a_tenant(client, db):
    """Innomax owns the platform; it is not a tenant on it.

    Exposing it as one would also hand it a slug-scoped login, which sidesteps the
    "superadmin may only sign in from the platform host" check in auth_routes. The
    company-info lookup is what the slug login/portal pages are built on.
    """
    from src.tenancy.platform_company import ensure_platform_company, platform_slug
    ensure_platform_company(db)
    db.flush()

    resp = client.get(f"/api/v1/public/company-info?c={platform_slug()}")
    assert resp.status_code == 404, resp.text


@pytest.mark.auth
def test_platform_company_job_board_lists_its_openings(client, db):
    """The platform's own careers page is a read-only listing, so it may resolve —
    flagged is_platform so the board sends applicants to /login, not a slug portal."""
    from src.models.models import JobPosting
    from src.tenancy.platform_company import ensure_platform_company, platform_slug
    company = ensure_platform_company(db)
    db.add(JobPosting(id=str(uuid.uuid4()), company_id=company.id, title="Platform Engineer",
                      description="d", requirements="r", status="active"))
    db.flush()

    resp = client.get(f"/api/v1/public/jobs?c={platform_slug()}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["company"]["is_platform"] is True
    assert "Platform Engineer" in [j["title"] for j in body["jobs"]]


@pytest.mark.auth
def test_missing_identifier_is_422(client):
    assert client.get("/api/v1/public/jobs").status_code == 422


@pytest.mark.auth
def test_auth_also_refuses_the_platform_slug(client, db):
    """The refusal has to hold on the auth routes too, not just the public ones.

    public_routes justifies its guard by saying that exposing the platform org would hand
    it a slug-scoped login — so if only public_routes enforced it, the stated reason would
    be the one thing left unenforced.
    """
    from src.tenancy.platform_company import ensure_platform_company, platform_slug
    ensure_platform_company(db)
    db.flush()

    resp = client.post("/api/v1/auth/login", json={
        "email": "someone@test.com",
        "password": "Pass@1234",
        "company_slug": platform_slug(),
    })
    assert resp.status_code == 404, resp.text


# ── legacy key exchange ────────────────────────────────────────────────────

@pytest.mark.auth
def test_resolve_key_returns_the_slug(client, db):
    """The SPA trades an old ?key= link for the slug, then scrubs the address bar."""
    company = _make_company(db, "Acme Four", "acme-four")
    resp = client.post("/api/v1/public/resolve-key", json={"key": company.api_key})
    assert resp.status_code == 200, resp.text
    assert resp.json()["slug"] == "acme-four"


@pytest.mark.auth
def test_resolve_key_rejects_a_dead_key(client):
    assert client.post(
        "/api/v1/public/resolve-key", json={"key": "sk_live_not_a_real_key"}
    ).status_code == 401


# ── auth scoping by slug ───────────────────────────────────────────────────

@pytest.mark.auth
@pytest.mark.parametrize("selector", ["company_slug", "api_key"])
def test_login_scopes_the_same_way_by_slug_or_key(client, db, selector):
    """A slug scopes a login exactly as the key did."""
    from src.auth import get_password_hash
    from src.models.models import User

    company = _make_company(db, "Scoped Co", "scoped-co")
    email = f"scoped_{selector}@test.com"
    db.add(User(
        id=str(uuid.uuid4()), email=email,
        password_hash=get_password_hash("Pass@1234"),
        role="candidate", first_name="S", last_name="C",
        is_active=True, email_verified=True, company_id=company.id,
    ))
    db.flush()

    value = company.slug if selector == "company_slug" else company.api_key
    resp = client.post("/api/v1/auth/login", json={
        "email": email, "password": "Pass@1234", selector: value,
    })
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()


@pytest.mark.auth
def test_register_binds_the_candidate_to_the_slugged_company(client, db):
    """And ``company_slug`` must not leak through into create_user.

    RegisterRequest carries the tenant selector; the User model knows nothing about it,
    so it has to be stripped before the model_dump reaches create_user. Miss that and it
    is a 500 at runtime, not an error at import.
    """
    from src.models.models import User

    company = _make_company(db, "Bind Co", "bind-co")
    email = "bound_candidate@test.com"
    resp = client.post("/api/v1/auth/register", json={
        "email": email, "password": "Pass@1234", "role": "candidate",
        "first_name": "Bound", "last_name": "Candidate",
        "company_slug": "bind-co",
    })
    assert resp.status_code in (200, 201), resp.text

    user = db.query(User).filter(User.email == email).first()
    assert user is not None
    assert user.company_id == company.id


@pytest.mark.auth
def test_a_named_company_that_does_not_resolve_fails_closed(client, db):
    """A supplied-but-unresolvable selector must 404, not silently drop scoping.

    Falling through to host resolution used to leave ``company_id`` as None on the
    platform host, so ``_scoped_user_by_email`` did a global lookup — a typo'd or rotated
    portal link quietly signed people in against no company at all. Only an *absent*
    selector may fall back.
    """
    resp = client.post("/api/v1/auth/login", json={
        "email": "someone@test.com", "password": "Pass@1234",
        "company_slug": "no-such-company",
    })
    assert resp.status_code == 404, resp.text

    resp = client.post("/api/v1/auth/login", json={
        "email": "someone@test.com", "password": "Pass@1234",
        "api_key": "sk_live_rotated_away",
    })
    assert resp.status_code == 404, resp.text
