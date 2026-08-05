# HireFlow Test Suite

## Quick start

```bash
# All fast tests (no Ollama, no slow ML)
pytest -m "not slow"

# Single category
pytest -m auth
pytest -m hr
pytest -m candidate
pytest -m interviewer
pytest -m ats          # pure unit tests, no DB needed

# With coverage
pytest --cov=. --cov-report=term-missing -m "not slow"

# LLM-dependent tests (requires Ollama running)
pytest -m slow
```

## Test files

| File | Marker | What it covers |
|------|--------|----------------|
| `test_auth.py` | `auth` | Register, login, logout, `/me` |
| `test_auth_otp.py` | `auth` | OTP verify, resend, forgot/reset password, token refresh |
| `test_admin_workflow.py` | `admin` | User creation, departments, RBAC |
| `test_hr_workflow.py` | `hr` | Job create/approve, offers |
| `test_candidate_workflow.py` | `candidate` | Browse jobs, apply, data isolation |
| `test_interviewer_workflow.py` | `interviewer` | List/access interviews, feedback, RBAC |
| `test_security_bugs.py` | `security` | Auth bypass, company_id, SQL exposure regressions |
| `test_ats_scoring.py` | — | Section detection, keyword/skill normalisation, fallbacks |
| `test_ats_service.py` | — | Experience dedup, work-experience parsing, embedding cache |
| `test_ats_core_scoring.py` | `ats` | Semantic score, weighted skills, cluster match, composite formula |
| `test_hybrid_ats_pytest.py` | `slow` | 16-case end-to-end LLM scoring scenarios |

## Fixtures (conftest.py)

| Fixture | Scope | Description |
|---------|-------|-------------|
| `db_engine` | session | Creates all tables once; drops them after the session |
| `db` | function | Transaction-isolated session; auto-rollback after each test |
| `client` | function | FastAPI `TestClient` with `get_db` overridden to test session |
| `admin_headers` | function | Bearer token for a freshly-registered admin user |
| `hr_headers` | function | Bearer token for HR user with test company attached |
| `interviewer_headers` | function | Bearer token for interviewer user |
| `candidate_headers` | function | Bearer token for candidate user |
| `candidate2_headers` | function | Second candidate (for data-isolation tests) |

## Adding a new test

1. Put it in `test/test_<feature>.py`
2. Use the shared fixtures from `conftest.py` — do **not** create a new database session
3. Tag with a pytest marker: `@pytest.mark.<tag>`
4. OTP-dependent tests: call `_inject_otp(db, email)` to plant the code without sending email
5. LLM-dependent tests: use `@pytest.mark.slow` so CI can skip them

## CI

The CI pipeline runs `pytest -m "not slow"` — fast tests only.  
Add `slow` tests to a nightly/scheduled job against a full environment with Ollama.
