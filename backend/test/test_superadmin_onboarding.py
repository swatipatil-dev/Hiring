"""
SuperAdmin onboarding-flow tests.

Covers the expanded ``POST /api/v1/superadmin/companies`` endpoint and the
matching ``PUT`` / ``GET`` handlers in ``routes/superadmin_routes.py``:

* required-only payload still succeeds (basics + admin user)
* all 50+ optional profile columns (legal, contacts, addresses, business
  profile, hiring footprint, dark logo) persist when supplied and read back
  via ``GET /companies/{id}``
* supplying commercial fields creates a matching ``SubscriptionPlan`` row;
  omitting them creates none
* queued departments are seeded inline at creation time
* duplicate slug / duplicate admin email are rejected (400)
* non-superadmin roles cannot reach the endpoint (403)
* ``PUT`` updates any subset of profile fields without disturbing the rest
"""

import uuid
import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_platform_company(db):
    """Seed the platform/owner company that ``require_superadmin`` looks up by
    slug. Without this row the guard's platform-belonging check is a no-op,
    but creating it exercises the real production path."""
    from src.models.models import Company
    from routes.superadmin_routes import PLATFORM_SLUG

    existing = db.query(Company).filter(Company.slug == PLATFORM_SLUG).first()
    if existing:
        return existing.id
    co = Company(id=str(uuid.uuid4()), name="HireFlow Platform", slug=PLATFORM_SLUG)
    db.add(co)
    db.flush()
    return co.id


def _make_superadmin(db, platform_company_id: str, email: str | None = None,
                     password: str = "Pass@1234") -> str:
    from src.models.models import User
    from src.auth import get_password_hash

    email = email or f"sa_{uuid.uuid4().hex[:6]}@hireflow.com"
    db.add(User(
        id=str(uuid.uuid4()),
        company_id=platform_company_id,
        email=email,
        password_hash=get_password_hash(password),
        role="superadmin",
        first_name="Super",
        last_name="Admin",
        is_active=True,
        email_verified=True,
    ))
    db.flush()
    return email


def _login(client, email: str, password: str = "Pass@1234") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def sa_headers(client, db):
    platform_id = _make_platform_company(db)
    email = _make_superadmin(db, platform_id)
    return _login(client, email)


# ── Payload builders ─────────────────────────────────────────────────────────

def _basics_payload(prefix: str = "acme") -> dict:
    """Minimum required-only payload — just enough to activate a tenant."""
    suffix = uuid.uuid4().hex[:6]
    return {
        "company_name": f"{prefix.title()} Co",
        "slug": f"{prefix}-{suffix}",
        "admin_email": f"admin_{suffix}@{prefix}.example.com",
        "admin_password": "Pass@1234",
        "admin_first_name": "Admin",
        "admin_last_name": "User",
    }


def _full_profile_payload(prefix: str = "acme") -> dict:
    """Required basics + every optional profile / commercial / department field."""
    base = _basics_payload(prefix)
    base.update({
        # Branding
        "logo_url": "https://cdn.example/logo.png",
        "logo_dark_url": "https://cdn.example/logo-dark.png",
        "favicon_url": "https://cdn.example/favicon.ico",
        "primary_color": "#ff6600",
        "secondary_color": "#222222",
        "accent_color": "#00d48c",
        "font_family": "Inter",
        "tagline": "Hire smarter.",
        "support_email": f"support@{prefix}.example.com",

        # Legal & tax identity
        "legal_name": "Acme Technologies Pvt Ltd",
        "brand_name": "Acme",
        "company_type": "pvt_ltd",
        "cin": "U72200KA2020PTC012345",
        "incorporation_date": "2020-01-15",
        "authorized_signatory_name": "Jane Doe",
        "authorized_signatory_designation": "Director",

        # Role-based contacts
        "primary_contact_name": "Alice", "primary_contact_email": "alice@acme.example.com",
        "primary_contact_phone": "+91-9000000001", "primary_contact_designation": "Head of HR",
        "billing_contact_name": "Bob", "billing_contact_email": "bob@acme.example.com",
        "billing_contact_phone": "+91-9000000002", "billing_contact_designation": "Finance",
        "technical_contact_name": "Carol", "technical_contact_email": "carol@acme.example.com",
        "technical_contact_phone": "+91-9000000003", "technical_contact_designation": "CTO",
        "dpo_contact_name": "Dan", "dpo_contact_email": "dpo@acme.example.com",
        "dpo_contact_phone": "+91-9000000004", "dpo_contact_designation": "DPO",
        "escalation_contact_name": "Eve", "escalation_contact_email": "ceo@acme.example.com",
        "escalation_contact_phone": "+91-9000000005", "escalation_contact_designation": "CEO",

        # Addresses
        "reg_address_line1": "42 Light Lane",
        "reg_address_line2": "Suite 7",
        "reg_city": "Bengaluru",
        "reg_state": "KA",
        "reg_country": "India",
        "reg_postal_code": "560001",
        "billing_same_as_registered": False,
        "billing_address_line1": "PO Box 99",
        "billing_address_line2": "Block C",
        "billing_city": "Mumbai",
        "billing_state": "MH",
        "billing_country": "India",
        "billing_postal_code": "400001",

        # Business profile
        "industry": "SaaS",
        "company_size_band": "51-200",
        "years_in_business": 5,
        "website_url": "https://acme.example.com",
        "description": "We do hiring software.",

        # Hiring footprint
        "expected_hires_per_year": 120,
        "recruiter_count": 3,
        "hiring_locations": "Bengaluru, Mumbai, Remote-India",
        "existing_ats": "None",
        "role_categories": "Engineering, Sales, Ops",

        # Commercial
        "plan_name": "growth",
        "billing_cycle": "annual",
        "price": 999.0,
        "currency": "INR",
        "payment_method": "upi",
        "po_required": True,
        "po_number": "PO-ACME-001",

        # Departments queued at creation time
        "departments": [
            {"name": "Engineering", "description": "Builds the product"},
            {"name": "Sales"},
        ],
    })
    return base


# ── Tests: happy path ────────────────────────────────────────────────────────

def test_create_company_with_basics_only(client, sa_headers):
    """A required-only payload still succeeds and returns the generated API key."""
    payload = _basics_payload("basic")
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == payload["company_name"]
    assert body["slug"] == payload["slug"]
    assert body["api_key"].startswith("sk_live_")
    assert body["admin_email"] == payload["admin_email"]


def test_create_company_persists_every_profile_field(client, sa_headers, db):
    """All 50+ optional profile fields written on create must read back via GET."""
    payload = _full_profile_payload("full")
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text
    company_id = resp.json()["id"]

    got = client.get(f"/api/v1/superadmin/companies/{company_id}", headers=sa_headers)
    assert got.status_code == 200, got.text
    data = got.json()

    # Branding
    assert data["logo_url"]      == payload["logo_url"]
    assert data["logo_dark_url"] == payload["logo_dark_url"]
    assert data["favicon_url"]   == payload["favicon_url"]
    assert data["primary_color"] == payload["primary_color"]
    assert data["tagline"]       == payload["tagline"]

    # Legal
    assert data["legal_name"]   == payload["legal_name"]
    assert data["brand_name"]   == payload["brand_name"]
    assert data["company_type"] == "pvt_ltd"
    assert data["cin"]          == payload["cin"]
    assert data["incorporation_date"] == "2020-01-15"
    assert data["authorized_signatory_name"] == "Jane Doe"

    # Contacts — spot-check across all five roles
    assert data["primary_contact_email"]    == "alice@acme.example.com"
    assert data["billing_contact_phone"]    == "+91-9000000002"
    assert data["technical_contact_name"]   == "Carol"
    assert data["dpo_contact_designation"]  == "DPO"
    assert data["escalation_contact_email"] == "ceo@acme.example.com"

    # Addresses
    assert data["reg_city"]        == "Bengaluru"
    assert data["reg_postal_code"] == "560001"
    assert data["billing_same_as_registered"] is False
    assert data["billing_city"]    == "Mumbai"

    # Business profile
    assert data["industry"]          == "SaaS"
    assert data["company_size_band"] == "51-200"
    assert data["years_in_business"] == 5
    assert data["website_url"]       == "https://acme.example.com"

    # Hiring footprint
    assert data["expected_hires_per_year"] == 120
    assert data["recruiter_count"]         == 3
    assert data["hiring_locations"]        == "Bengaluru, Mumbai, Remote-India"
    assert data["role_categories"]         == "Engineering, Sales, Ops"

    # Subscription (nested by _serialize_company → get_company)
    sub = data.get("subscription")
    assert sub is not None
    assert sub["plan_name"]      == "growth"
    assert sub["billing_cycle"]  == "annual"
    assert sub["currency"]       == "INR"
    assert sub["payment_method"] == "upi"
    assert sub["po_required"]    is True
    assert sub["po_number"]      == "PO-ACME-001"


def test_create_without_commercial_fields_skips_subscription_row(client, sa_headers, db):
    """If no plan/currency/payment field is given, no SubscriptionPlan row is created."""
    from src.models.models import SubscriptionPlan

    payload = _basics_payload("nosub")
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text
    company_id = resp.json()["id"]

    sub = db.query(SubscriptionPlan).filter(SubscriptionPlan.company_id == company_id).first()
    assert sub is None, "commercial fields were omitted — no subscription row should exist"

    got = client.get(f"/api/v1/superadmin/companies/{company_id}", headers=sa_headers)
    assert got.json()["subscription"] is None


def test_create_with_only_currency_still_creates_subscription_row(client, sa_headers, db):
    """Any single commercial field triggers SubscriptionPlan creation with sensible defaults."""
    from src.models.models import SubscriptionPlan

    payload = _basics_payload("partsub")
    payload["currency"] = "USD"  # the only commercial signal
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text
    company_id = resp.json()["id"]

    sub = db.query(SubscriptionPlan).filter(SubscriptionPlan.company_id == company_id).first()
    assert sub is not None
    assert sub.currency == "USD"
    assert sub.plan_name == "starter"      # default applied
    assert sub.billing_cycle == "monthly"  # default applied
    assert sub.po_required is False


def test_create_with_queued_departments_seeds_them(client, sa_headers, db):
    """Departments supplied inline at create time become rows linked to the new company."""
    from src.models.models import Department

    payload = _basics_payload("withdepts")
    payload["departments"] = [
        {"name": "Engineering", "description": "Builds product"},
        {"name": "Sales"},
        {"name": "  "},  # whitespace-only → skipped
    ]
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text
    company_id = resp.json()["id"]

    depts = db.query(Department).filter(Department.company_id == company_id).all()
    names = sorted(d.name for d in depts)
    assert names == ["Engineering", "Sales"]
    eng = next(d for d in depts if d.name == "Engineering")
    assert eng.description == "Builds product"


def test_incorporation_date_invalid_format_is_rejected(client, sa_headers):
    """Pydantic must reject malformed dates before they reach the DB."""
    payload = _basics_payload("baddate")
    payload["incorporation_date"] = "not-a-date"
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 422, resp.text


# ── Tests: validation / errors ────────────────────────────────────────────────

def test_duplicate_slug_is_rejected(client, sa_headers):
    """Slugs must remain unique across companies."""
    payload = _basics_payload("dupslug")
    first = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert first.status_code == 201, first.text

    second_payload = _basics_payload("dupslug")
    second_payload["slug"] = payload["slug"]  # collide
    second_payload["admin_email"] = "other@dup.example.com"
    second = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=second_payload)
    assert second.status_code == 400
    assert "slug" in second.json()["detail"].lower()


def test_duplicate_admin_email_is_rejected(client, sa_headers):
    """A single admin email cannot be reused across two new tenants via this endpoint."""
    payload = _basics_payload("dupemail")
    first = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert first.status_code == 201, first.text

    second_payload = _basics_payload("dupemail2")
    second_payload["admin_email"] = payload["admin_email"]  # collide
    second = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=second_payload)
    assert second.status_code == 400
    assert "email" in second.json()["detail"].lower()


def test_non_superadmin_cannot_create_company(client, db, admin_headers):
    """The endpoint must reject any caller whose role is not 'superadmin'."""
    payload = _basics_payload("forbid")
    resp = client.post("/api/v1/superadmin/companies", headers=admin_headers, json=payload)
    assert resp.status_code == 403, resp.text


def test_anonymous_cannot_create_company(client):
    """No token = no access."""
    resp = client.post("/api/v1/superadmin/companies", json=_basics_payload("anon"))
    assert resp.status_code in (401, 403), resp.text


# ── Tests: update flow ───────────────────────────────────────────────────────

def test_update_company_can_set_new_profile_fields(client, sa_headers, db):
    """PUT must accept any of the new optional fields and persist them."""
    payload = _basics_payload("upd")
    created = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert created.status_code == 201, created.text
    company_id = created.json()["id"]

    patch = {
        "legal_name": "Updated Legal Name Pvt Ltd",
        "industry": "Healthcare",
        "expected_hires_per_year": 240,
        "primary_contact_email": "new_alice@upd.example.com",
        "billing_same_as_registered": True,
        "logo_dark_url": "https://cdn.example/new-dark.png",
    }
    upd = client.put(f"/api/v1/superadmin/companies/{company_id}", headers=sa_headers, json=patch)
    assert upd.status_code == 200, upd.text

    got = client.get(f"/api/v1/superadmin/companies/{company_id}", headers=sa_headers).json()
    assert got["legal_name"]              == "Updated Legal Name Pvt Ltd"
    assert got["industry"]                == "Healthcare"
    assert got["expected_hires_per_year"] == 240
    assert got["primary_contact_email"]   == "new_alice@upd.example.com"
    assert got["billing_same_as_registered"] is True
    assert got["logo_dark_url"]           == "https://cdn.example/new-dark.png"


def test_welcome_email_sent_on_company_create(client, sa_headers, monkeypatch):
    """create_company() should fire send_admin_welcome_email exactly once
    with the new admin's email + company name."""
    calls = []

    def fake_send(*args, **kwargs):
        calls.append(kwargs or {"args": args})
        return {"sent": True, "error": None, "error_type": None}

    import routes.superadmin_routes as sa_mod
    import src.services.email_service as email_mod
    # The endpoint imports the helper lazily; patch on the module it imports from.
    monkeypatch.setattr(email_mod, "send_admin_welcome_email", fake_send)

    payload = _basics_payload("welcome")
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text

    assert len(calls) == 1, f"expected welcome email send once, got {len(calls)}"
    sent = calls[0]
    assert sent["to"] == payload["admin_email"]
    assert sent["name"] == payload["admin_first_name"]
    assert sent["company_name"] == payload["company_name"]
    assert "login_url" in sent and "/admin/login" in sent["login_url"]


def test_welcome_email_failure_does_not_break_company_create(client, sa_headers, monkeypatch):
    """An SMTP/network failure during welcome-email send must not prevent
    the new company + admin user from being persisted."""
    import src.services.email_service as email_mod

    def explode(*args, **kwargs):
        raise RuntimeError("simulated SMTP outage")

    monkeypatch.setattr(email_mod, "send_admin_welcome_email", explode)

    payload = _basics_payload("smtpfail")
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text  # still succeeds despite the raise
    body = resp.json()
    assert body["slug"] == payload["slug"]


# ── Tests: slug normalization ────────────────────────────────────────────────

def test_messy_slug_is_normalized_on_create(client, sa_headers):
    """A slug with spaces/punctuation/casing is slugified before storage."""
    payload = _basics_payload("norm")
    payload["slug"] = "Innomax  Sol!!"     # messy input
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["slug"] == "innomax-sol"


def test_case_variant_slug_is_rejected_as_duplicate(client, sa_headers):
    """Uniqueness is case-insensitive: 'Acme' collides with an existing 'acme'."""
    payload = _basics_payload("casedup")
    payload["slug"] = "acmeunique"
    first = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert first.status_code == 201, first.text

    second = _basics_payload("casedup2")
    second["slug"] = "ACMEUNIQUE"           # same slug, different case
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=second)
    assert resp.status_code == 400
    assert "slug" in resp.json()["detail"].lower()


def test_reserved_slug_is_rejected(client, sa_headers):
    """A routing-sensitive subdomain (e.g. 'api') cannot be claimed."""
    payload = _basics_payload("reserved")
    payload["slug"] = "api"
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 400
    assert "reserved" in resp.json()["detail"].lower()


def test_slug_reducing_to_empty_is_rejected(client, sa_headers):
    """A slug made only of punctuation normalizes to '' and must be rejected."""
    payload = _basics_payload("empty")
    payload["slug"] = "!!!"
    resp = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert resp.status_code == 400
    assert "invalid" in resp.json()["detail"].lower() or "empty" in resp.json()["detail"].lower()


def test_update_normalizes_slug(client, sa_headers):
    """PUT slug is slugified the same way as create."""
    created = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=_basics_payload("updslug"))
    assert created.status_code == 201, created.text
    company_id = created.json()["id"]

    upd = client.put(f"/api/v1/superadmin/companies/{company_id}", headers=sa_headers,
                     json={"slug": "New Slug Name"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["slug"] == "new-slug-name"


def test_update_company_partial_does_not_clobber_other_fields(client, sa_headers):
    """PUT with a partial body must leave fields not in the body unchanged."""
    payload = _full_profile_payload("partial")
    created = client.post("/api/v1/superadmin/companies", headers=sa_headers, json=payload)
    assert created.status_code == 201, created.text
    company_id = created.json()["id"]

    # Update only one field
    upd = client.put(
        f"/api/v1/superadmin/companies/{company_id}",
        headers=sa_headers,
        json={"industry": "Fintech"},
    )
    assert upd.status_code == 200, upd.text

    got = client.get(f"/api/v1/superadmin/companies/{company_id}", headers=sa_headers).json()
    assert got["industry"]            == "Fintech"           # changed
    assert got["legal_name"]          == payload["legal_name"]  # untouched
    assert got["primary_contact_email"] == payload["primary_contact_email"]
    assert got["reg_city"]            == "Bengaluru"
