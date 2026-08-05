"""
src.tenancy
===========
Cross-company data isolation.

The tenant column on every isolated table is `company_id` (UUID FK to
`companies.id`). This package provides:

* `context.py`     — contextvar that carries the *current* company_id for a
                     request, plus the `tenant_bypass()` context manager.
* `mixin.py`       — `TenantScoped` marker class. Models that subclass it are
                     auto-filtered by the SQLAlchemy event hook.
* `events.py`      — `before_compile` event on `Query` that injects
                     `Model.company_id == <current_company_id>` on every read
                     of a `TenantScoped` model when a tenant is bound.

The auto-filter is a safety net.  Routes that scope explicitly remain correct;
routes that *forget* to scope no longer leak.

Bypass: superadmin endpoints and platform-wide jobs never bind a tenant
(contextvar is None) so the filter is a no-op for them. Code paths that
intentionally need to escape inside an in-tenant request can use::

    from src.tenancy import tenant_bypass
    with tenant_bypass():
        rows = db.query(JobPosting).all()    # returns every company's rows
"""

from .context import (
    set_current_company,
    get_current_company,
    reset_current_company,
    tenant_bypass,
)
from .mixin import TenantScoped

# Importing events registers the before_compile hook on sqlalchemy.orm.Query.
from . import events  # noqa: F401

__all__ = [
    "set_current_company",
    "get_current_company",
    "reset_current_company",
    "tenant_bypass",
    "TenantScoped",
]
