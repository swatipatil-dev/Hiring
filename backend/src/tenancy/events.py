"""
SQLAlchemy tenant-isolation hooks. Two complementary safety nets:

READ (`do_orm_execute` on Session) — adds `with_loader_criteria(TenantScoped,
company_id == <current_company>)` to every ORM SELECT, UPDATE and DELETE while
a tenant is bound in the contextvar. Loader criteria reach every way the ORM
touches a row: legacy `db.query(...)`, 2.0-style `db.execute(select(...))`,
`db.get()` / `query.get()`, relationship lazy loads, joined and aliased
entities, and bulk `query.update()` / `query.delete()`. The previous
`before_compile` hook on Query covered only the first of those — `db.get()`
and a bulk `.delete()` both reached other companies' rows.

WRITE (`before_flush` on Session) — auto-stamps `company_id =
<current_company>` onto every NEW TenantScoped row that doesn't already have
one, provided a tenant is bound. This is the write-side mirror of the read
filter: it stops new rows being inserted with a NULL `company_id`, which the
read filter would otherwise make permanently invisible (`NULL = X` is never
true in SQL). Forgetting to set company_id on insert was the #1 cause of
"data is in the DB but the dashboard is blank" — see
docs/DASHBOARD_DATA_PITFALLS.md.

Bypasses:
1. No tenant bound (contextvar is None) — superadmin, public, startup work,
   registration before a company exists, and `tenant_bypass()` blocks.
2. (Read only) Statement carries execution_option `_tenant_bypass=True`.
3. (Read only) Refreshing columns of an object already in the session
   (`is_column_load`). The object was authorised when it was loaded; filtering
   the refresh would turn a row loaded under `tenant_bypass()` into an
   ObjectDeletedError the moment it expires.
4. (Write only) The row already has an explicit company_id — explicit wins.

An explicit `.filter(Model.company_id == other)` does NOT escape the filter:
reaching another company's rows inside a tenant request must go through
`tenant_bypass()`, where it is visible in review.
"""
from __future__ import annotations

import logging

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from .context import get_current_company
from .mixin import TenantScoped

logger = logging.getLogger(__name__)


@event.listens_for(Session, "do_orm_execute")
def _inject_tenant_filter(state: ORMExecuteState) -> None:
    company_id = get_current_company()
    if not company_id:
        return
    if state.execution_options.get("_tenant_bypass"):
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load:
        return

    # The lambda is cached by SQLAlchemy as a lambda statement; company_id is a
    # closure variable, which SQLAlchemy extracts as a bound parameter rather
    # than baking into the cached SQL (test_tenant_filter_switches_company).
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantScoped,
            lambda cls: cls.company_id == company_id,
            include_aliases=True,
        )
    )


@event.listens_for(Session, "before_flush")
def _stamp_tenant_on_insert(session: Session, flush_context, instances) -> None:
    """
    Stamp `company_id` onto every NEW TenantScoped row that doesn't have one,
    using the company bound in the contextvar.

    Runs just before INSERTs are emitted, so it catches rows created by any
    code path — even ones that forgot to set company_id. An explicitly set
    company_id is never overwritten (explicit wins), and when no tenant is
    bound nothing is stamped (superadmin / public / registration / bypass).
    """
    company_id = get_current_company()
    if not company_id:
        return

    for obj in session.new:
        try:
            if not isinstance(obj, TenantScoped):
                continue
            if not hasattr(obj, "company_id"):
                continue
            if getattr(obj, "company_id", None):
                continue  # explicit value wins — never overwrite
            obj.company_id = company_id
        except Exception as exc:
            # Never break a flush because of the auto-stamp — log and move on.
            logger.warning(
                "tenant auto-stamp: failed for %s: %s: %s",
                type(obj).__name__, type(exc).__name__, exc,
            )
