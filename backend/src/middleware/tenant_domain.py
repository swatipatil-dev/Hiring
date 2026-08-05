"""
TenantDomainMiddleware
======================
Resolves every incoming request's Host header to a Company tenant and
attaches the result to request.state so any downstream route or dependency
can access it without hitting the database a second time.

Resolution order
----------------
1. Exact match against Company.custom_domain  (e.g. careers.acmecorp.com)
2. Slug-based subdomain against HIREFLOW_BASE_DOMAIN
   (e.g. acme.hireflow.io → slug = "acme")

If neither matches, request.state.company_id is left None and the request
continues normally — all existing routes keep working unchanged.
"""

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Base domain for slug subdomains.  Set HIREFLOW_BASE_DOMAIN=hireflow.io in .env.
BASE_DOMAIN: str = os.getenv("HIREFLOW_BASE_DOMAIN", "hireflow.io").lower().strip()

# Hosts that are never tenant domains (dev / test / internal)
_SKIP_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "testserver"})


def _bare_host(request: Request) -> str:
    """Return lowercase hostname without port from the Host header."""
    return request.headers.get("host", "").split(":")[0].lower().strip()


class TenantDomainMiddleware(BaseHTTPMiddleware):
    """
    Attaches three attributes to every request.state:
        company_id       str | None   — Company.id of the resolved tenant
        company_slug     str | None   — Company.slug (e.g. "acme")
        company_branding dict | None  — branding fields needed for white-label emails / UI
    """

    async def dispatch(self, request: Request, call_next: callable) -> Response:
        # Reset the tenant contextvar at request entry. Each request runs in its
        # own asyncio task, so ContextVar state is naturally isolated — but
        # explicit reset guards against threadpool reuse paths (e.g. sync deps
        # off a worker thread).
        from src.tenancy import set_current_company
        set_current_company(None)

        # Initialise state so downstream code can always read these keys safely
        request.state.company_id = None
        request.state.company_slug = None
        request.state.company_branding = None

        host = _bare_host(request)

        if host and host not in _SKIP_HOSTS:
            try:
                company = _resolve_company(host)
                if company:
                    request.state.company_id = company.id
                    request.state.company_slug = company.slug
                    request.state.company_branding = {
                        "company_name":      company.name,
                        "color":             company.primary_color or "#2b7cba",
                        "secondary_color":   company.secondary_color,
                        "accent_color":      company.accent_color,
                        "logo_url":          company.logo_url,
                        "favicon_url":       company.favicon_url,
                        "font_family":       company.font_family,
                        "support_email":     company.support_email,
                    }
                    logger.debug(
                        "TenantDomainMiddleware: host=%s → company=%s (%s)",
                        host, company.slug, company.id,
                    )
            except Exception as exc:
                # Never block the request due to a tenant-lookup failure
                logger.warning(
                    "TenantDomainMiddleware: lookup failed for host=%s — %s: %s",
                    host, type(exc).__name__, exc,
                )

        return await call_next(request)


# ---------------------------------------------------------------------------
# Internal helpers (sync — called from async dispatch via direct call)
# ---------------------------------------------------------------------------

def _resolve_company(host: str):
    """
    Look up a Company by custom_domain or slug subdomain.
    Opens its own short-lived DB session and closes it immediately.
    Returns a Company ORM object or None.
    """
    from src.config.database_config_mysql import db_manager
    from src.models.models import Company

    session = db_manager.get_db_session()
    try:
        # 1. Exact custom domain match (must be verified)
        company = (
            session.query(Company)
            .filter(
                Company.custom_domain == host,
                Company.domain_verified == True,
                Company.is_active == True,
            )
            .first()
        )
        if company:
            return company

        # 2. Slug-based subdomain  e.g.  acme.hireflow.io
        if BASE_DOMAIN and host.endswith(f".{BASE_DOMAIN}"):
            slug = host[: -(len(BASE_DOMAIN) + 1)]  # strip ".hireflow.io"
            if slug:
                company = (
                    session.query(Company)
                    .filter(
                        Company.slug == slug,
                        Company.is_active == True,
                    )
                    .first()
                )
                return company

        return None
    finally:
        session.close()
