"""
SQLAlchemy tenant-isolation hooks. Two complementary safety nets:

READ (`before_compile` on Query) — auto-injects
`Model.company_id == <current_company>` on every Query that selects a
TenantScoped model, provided a tenant is bound in the contextvar.

WRITE (`before_flush` on Session) — auto-stamps `company_id =
<current_company>` onto every NEW TenantScoped row that doesn't already have
one, provided a tenant is bound. This is the write-side mirror of the read
filter: it stops new rows being inserted with a NULL `company_id`, which the
read filter would otherwise make permanently invisible (`NULL = X` is never
true in SQL). Forgetting to set company_id on insert was the #1 cause of
"data is in the DB but the dashboard is blank" — see
docs/DASHBOARD_DATA_PITFALLS.md.

Bypasses (both hooks):
1. No tenant bound (contextvar is None) — superadmin, public, startup work,
   registration before a company exists, and `tenant_bypass()` blocks.
2. (Read only) Query carries execution_option `_tenant_bypass=True`.
3. (Read only) Query already filters company_id — caller knows what they're doing.
4. (Write only) The row already has an explicit company_id — explicit wins.

Bulk update/delete (`Query.update()` / `Query.delete()`) is also covered by the
read hook because SQLAlchemy invokes before_compile for them too.
"""
from __future__ import annotations

import logging
from typing import Iterable

from sqlalchemy import event
from sqlalchemy.orm import Query, Session

from .context import get_current_company
from .mixin import TenantScoped

logger = logging.getLogger(__name__)


def _entity_classes(query: Query) -> Iterable[type]:
    """Yield mapped classes referenced by this query (top-level entities only)."""
    for desc in query.column_descriptions:
        entity = desc.get("entity")
        if entity is None:
            continue
        # entity is the mapped class
        yield entity


def _query_already_filters_company(query: Query, model: type) -> bool:
    """
    Best-effort check: does this query already constrain `model.company_id`?
    We render the WHERE clause as a string and search for the column name.
    Cheap and conservative — if our heuristic returns False, we add the filter,
    which is the safe default.
    """
    crit = query.whereclause
    if crit is None:
        return False
    try:
        text = str(crit.compile(compile_kwargs={"literal_binds": False}))
    except Exception:
        return False
    tablename = getattr(model, "__tablename__", None)
    if not tablename:
        return False
    return f"{tablename}.company_id" in text


@event.listens_for(Query, "before_compile", retval=True)
def _inject_tenant_filter(query: Query) -> Query:
    company_id = get_current_company()
    if not company_id:
        return query

    # Explicit opt-out via execution option.
    opts = getattr(query, "_execution_options", None)
    if opts is not None:
        try:
            if opts.get("_tenant_bypass"):
                return query
        except Exception:
            pass

    for entity in _entity_classes(query):
        try:
            if not isinstance(entity, type):
                continue
            if not issubclass(entity, TenantScoped):
                continue
            if not hasattr(entity, "company_id"):
                continue
            if _query_already_filters_company(query, entity):
                continue
            query = query.enable_assertions(False).filter(
                entity.company_id == company_id
            )
        except Exception as exc:
            # Never break a query because of the auto-filter — log and move on.
            logger.warning(
                "tenant auto-filter: failed for %s: %s: %s",
                entity, type(exc).__name__, exc,
            )
    return query


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
