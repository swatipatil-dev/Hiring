"""
FastAPI dependency that binds the authenticated user's company_id to the
tenant contextvar for the duration of the request, so the SQLAlchemy
before_compile hook (src.tenancy.events) auto-filters all queries.

Wire this dep onto the *authenticated* routers in main.py via
`dependencies=[Depends(bind_tenant_scope)]`. Do NOT attach it to:
  * superadmin_router  (intentionally cross-tenant)
  * public_router      (anonymous; tenant resolved per-request from API key /
                        request.state.company_id, scoping happens in handler)

Superadmin users land here with `company_id = None`, which leaves the
contextvar empty and the auto-filter dormant — exactly what we want.
"""
from typing import Optional

from fastapi import Depends, Request

from src.auth import get_current_user
from src.models.models import User
from src.tenancy.context import set_current_company, reset_current_company


def bind_tenant_scope(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """
    Set the contextvar from `current_user.company_id` and reset on teardown.

    Superadmin: company_id is None, auto-filter stays a no-op. Their explicit
    cross-tenant access is governed by superadmin_routes' own guard.
    """
    company_id: Optional[str] = current_user.company_id
    token = set_current_company(company_id)
    try:
        yield current_user
    finally:
        reset_current_company(token)
