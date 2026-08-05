"""
src.tenancy.platform_company
============================
Single source of truth for the platform's own company — the **Innomax** org.

Innomax (slug ``innomax``, configurable via ``PLATFORM_COMPANY_SLUG``) is the
platform owner, NOT a client company. The superadmin, the platform admin, and
any "bare" HireFlow self-signup (no client portal key / tenant domain) all
belong to it.

Every place that needs "which company is the platform?" should call these
helpers instead of querying ``Company.slug == "innomax"`` inline — that keeps
the slug env-driven and consistent, and guarantees platform users never end up
with ``company_id = NULL`` (which the tenant auto-filter would hide).
"""
import os
import secrets
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def platform_slug() -> str:
    """Slug that identifies the platform owner company (default ``innomax``)."""
    return os.getenv("PLATFORM_COMPANY_SLUG", "innomax")


def platform_name() -> str:
    """Display name used when the platform company has to be created."""
    return os.getenv("PLATFORM_COMPANY_NAME", "Innomax IT Solutions")


def get_platform_company(db):
    """Return the platform (Innomax) company, or ``None`` if it doesn't exist yet."""
    from src.models.models import Company
    return db.query(Company).filter(Company.slug == platform_slug()).first()


def ensure_platform_company(db):
    """Return the platform (Innomax) company, creating it if missing.

    Never returns ``None`` — this is what callers use when they must hand out a
    non-null ``company_id`` (e.g. bare self-signups, the create_user fallback).
    The new row is flushed so ``.id`` is populated, but the caller owns the
    surrounding transaction / commit.
    """
    from src.models.models import Company

    company = get_platform_company(db)
    if company is None:
        company = Company(
            name=platform_name(),
            slug=platform_slug(),
            api_key="sk_live_" + secrets.token_urlsafe(32),
            is_active=True,
        )
        db.add(company)
        db.flush()  # populate company.id without committing the caller's tx
        logger.info("Created platform company '%s' (slug=%s, id=%s)",
                    company.name, company.slug, company.id)
    return company


def get_platform_company_id(db) -> Optional[str]:
    """Convenience: the platform company's id, or ``None`` if it doesn't exist."""
    company = get_platform_company(db)
    return company.id if company else None
