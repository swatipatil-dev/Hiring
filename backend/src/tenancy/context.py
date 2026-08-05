"""
Per-request tenant context, backed by a ContextVar so it survives async hops
within one FastAPI request and is naturally isolated between concurrent
requests.

`get_current_company()` returns the company_id (str) of the active tenant, or
None when no tenant is bound — e.g. on superadmin routes, public routes, or
inside a `tenant_bypass()` block.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

_current_company: ContextVar[Optional[str]] = ContextVar(
    "hireflow_current_company_id", default=None
)


def set_current_company(company_id: Optional[str]):
    """Bind the active company for this request. Returns the reset token."""
    return _current_company.set(company_id)


def get_current_company() -> Optional[str]:
    """Return the currently bound company_id, or None if no tenant is active."""
    return _current_company.get()


def reset_current_company(token) -> None:
    """Restore the previous contextvar state using the token from set_current_company."""
    _current_company.reset(token)


@contextmanager
def tenant_bypass():
    """
    Temporarily clear the bound tenant so queries run unscoped.

    Use sparingly — only when a code path inside a tenant-bound request
    legitimately needs cross-tenant access (e.g. resolving a candidate's user
    record before their company association is known).
    """
    token = _current_company.set(None)
    try:
        yield
    finally:
        _current_company.reset(token)
