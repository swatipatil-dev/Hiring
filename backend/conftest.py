"""
Shared pytest fixtures for HireFlow test suite.
Uses a dedicated MySQL test database (recruitment_test_db) with per-test
transaction rollback so every test runs in full isolation.
"""

import os
import pytest
from sqlalchemy import create_engine, text, event
from sqlalchemy.orm import sessionmaker, Session
from fastapi.testclient import TestClient

# src.auth refuses to import without a real SECRET_KEY (a default signing key
# would let anyone forge an admin token). Set a fixed test value before anything
# imports the app. CI overrides this with its own secret.
os.environ.setdefault("SECRET_KEY", "pytest-only-secret-key-not-for-any-real-deployment")

# Point at the test DB — CI sets TEST_DB env var; local default works too
_db_host = os.getenv("DB_HOST", "127.0.0.1")
_db_pass = os.getenv("DB_PASSWORD", "1234")
_test_db = os.getenv("TEST_DB", "recruitment_test_db")
TEST_DATABASE_URL = f"mysql+pymysql://root:{_db_pass}@{_db_host}:3306/{_test_db}"

# Lazy imports so heavy ML models are not loaded at collection time
def _get_app_and_deps():
    from main import app
    from src.config.database_config_mysql import get_db
    from src.models.models import Base
    return app, get_db, Base


@pytest.fixture(scope="session", autouse=True)
def _disable_rate_limiter_for_suite():
    """slowapi Limiter counters live on the module-level instances and persist
    across every test in the same Python process. After ~10 registers or ~5
    logins the whole suite starts seeing 429s. Flip both limiters off for the
    duration of the test session."""
    import main as main_mod
    import routes.auth_routes as auth_mod
    orig_main = main_mod.limiter.enabled
    orig_auth = auth_mod.limiter.enabled
    main_mod.limiter.enabled = False
    auth_mod.limiter.enabled = False
    yield
    main_mod.limiter.enabled = orig_main
    auth_mod.limiter.enabled = orig_auth


@pytest.fixture(scope="session")
def db_engine():
    """Create test engine and build all tables once per session."""
    engine = create_engine(
        TEST_DATABASE_URL,
        pool_pre_ping=True,
        connect_args={"charset": "utf8mb4"},
    )
    _, _, Base = _get_app_and_deps()
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db(db_engine) -> Session:
    """
    Function-scoped session with automatic rollback.

    Uses the SQLAlchemy "join an external transaction" recipe:
    - An outer Transaction is opened on a dedicated Connection.
    - The Session is bound to that Connection inside a nested SAVEPOINT.
    - An after_transaction_end listener restarts the SAVEPOINT whenever
      the session/code under test calls commit(), so endpoint commits
      never escape the outer Transaction.
    - At teardown the outer Transaction is rolled back, discarding all
      data — even committed-by-the-endpoint rows — so tests are truly
      isolated even though endpoints freely call db.commit().
    """
    connection = db_engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection, autocommit=False, autoflush=False)
    session = TestSession()

    # Open the first SAVEPOINT that the session "joins".
    nested = connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, trans):
        nonlocal nested
        if not nested.is_active:
            nested = connection.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db) -> TestClient:
    """FastAPI test client with DB dependency overridden to use test session."""
    app, get_db, _ = _get_app_and_deps()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


# ── User / auth helpers ────────────────────────────────────────────────────

def _register_and_login(client: TestClient, email: str, password: str,
                         role: str, first: str = "Test", last: str = "User",
                         db=None) -> dict:
    """Provision a user and return auth headers.

    Self-registration is candidate-only (admin/HR/interviewer accounts must be
    created by an existing admin in production). Candidate role goes through
    the public register endpoint; all other roles are inserted directly into
    the test DB session so we can still exercise the role-protected endpoints
    without standing up a full admin-bootstrap flow per test.
    """
    import uuid as _uuid
    from src.models.models import User
    from src.auth import get_password_hash

    if role == "candidate":
        reg = client.post("/api/v1/auth/register", json={
            "email": email,
            "password": password,
            "role": role,
            "first_name": first,
            "last_name": last,
        })
        assert reg.status_code in (200, 201), f"Register failed for {role}: {reg.text}"
        if db is not None:
            user = db.query(User).filter(User.email == email).first()
            if user:
                user.email_verified = True
                db.flush()
    else:
        assert db is not None, f"db fixture required to seed {role} users directly"
        db.add(User(
            id=str(_uuid.uuid4()),
            email=email,
            password_hash=get_password_hash(password),
            role=role,
            first_name=first,
            last_name=last,
            is_active=True,
            email_verified=True,
        ))
        db.flush()

    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed for {role}: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def admin_headers(client, db):
    headers = _register_and_login(client, "admin_test@test.com", "Pass@1234", "admin", "Admin", "User", db=db)

    # Non-candidate roles are seeded straight into the DB (see _register_and_login),
    # which leaves company_id NULL. Every admin endpoint scopes queries by
    # current_user.company_id, and SQLAlchemy renders `column == None` as
    # `IS NULL` — so a company-less admin matches NO users and every lookup 404s.
    #
    # Candidates registered through the public endpoint resolve to the platform
    # company, and routes/auth_routes.py states the intent directly: "HireFlow
    # candidates share the same company as the superadmin/admin". Putting the
    # admin there is what makes this fixture reflect production.
    from src.models.models import User
    from src.tenancy.platform_company import ensure_platform_company

    company = ensure_platform_company(db)
    admin = db.query(User).filter(User.email == "admin_test@test.com").first()
    if admin:
        admin.company_id = company.id
        db.flush()
    return headers


@pytest.fixture()
def hr_headers(client, db):
    headers = _register_and_login(client, "hr_test@test.com", "Pass@1234", "hr", "HR", "User", db=db)
    # Attach a test company + default department to the HR user so
    # company-scoped multi-tenant tests and job creation work.
    import uuid as _uuid
    from src.models.models import User, Company, Department
    company = Company(id=str(_uuid.uuid4()), name="Test Company", slug=f"test-co-{_uuid.uuid4().hex[:8]}")
    db.add(company)
    db.flush()
    department = Department(id=str(_uuid.uuid4()), company_id=company.id, name="Engineering")
    db.add(department)
    db.flush()
    hr_user = db.query(User).filter(User.email == "hr_test@test.com").first()
    if hr_user:
        hr_user.company_id = company.id
        db.flush()
    return headers


@pytest.fixture()
def interviewer_headers(client, db):
    return _register_and_login(client, "interviewer_test@test.com", "Pass@1234", "interviewer", "Int", "User", db=db)


@pytest.fixture()
def candidate_headers(client, db):
    return _register_and_login(client, "candidate_test@test.com", "Pass@1234", "candidate", "Cand", "User", db=db)


@pytest.fixture()
def candidate2_headers(client, db):
    return _register_and_login(client, "candidate2_test@test.com", "Pass@1234", "candidate", "Cand2", "User", db=db)
