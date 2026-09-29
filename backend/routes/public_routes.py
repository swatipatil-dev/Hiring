"""
Public Routes — no authentication required

Two ways to identify the tenant:

* ``?c=<slug>`` — the company's public slug. This is the one to use. A slug is an
  identifier, not a credential, so it is safe in a URL, an access log, a ``Referer``
  header, or an iframe ``src`` on a third party's website.
* ``?key=<api_key>`` — **deprecated**. ``Company.api_key`` is an ``sk_live_…`` secret and
  had no business being in a URL: it leaks into browser history, access logs and every
  shared link. Kept only so links already printed on job ads keep resolving; the SPA
  swaps it for the slug form on load. Remove this, and ``/resolve-key`` below, at the end
  of the deprecation window.

The ``/careers`` routes need neither — they resolve the tenant from the request Host,
which is what a tenant's own custom domain uses.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.config.database_config_mysql import get_db
from src.models.models import Company, JobPosting
from src.dependencies import require_request_company
from src.rate_limit import limiter
from src.tenancy.platform_company import platform_slug

logger = logging.getLogger(__name__)
from src.services.skills_parsing import parse_skills

router = APIRouter(prefix="/api/v1/public", tags=["Public"])


def _get_company_by_key(api_key: str, db: Session) -> Company:
    company = db.query(Company).filter(
        Company.api_key == api_key,
        Company.is_active == True
    ).first()
    if not company:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return company


def _get_company_by_slug(slug: str, db: Session, allow_platform: bool = False) -> Company:
    """Resolve a company from its public slug.

    404 rather than 401: a slug is an identifier, not a credential, so a slug that does
    not resolve means "no such company", not "authentication failed".

    Matched case-insensitively, the same rule create/update already enforce for
    uniqueness — a slug printed on a job ad gets typed back in whatever case the reader
    feels like.

    The platform company (Innomax) is unreachable here by default. It owns the platform
    rather than being a tenant on it, and exposing it as one would also hand it a
    slug-scoped login that sidesteps the superadmin host check in auth_routes.
    ``allow_platform`` is only for read-only listings (its public job board): nothing
    there signs anyone in.
    """
    normalised = (slug or "").strip().lower()
    if not normalised:
        raise HTTPException(status_code=404, detail="Company not found")
    if normalised == platform_slug().strip().lower() and not allow_platform:
        raise HTTPException(status_code=404, detail="Company not found")

    company = db.query(Company).filter(
        func.lower(Company.slug) == normalised,
        Company.is_active == True
    ).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


def _resolve_public_company(c: Optional[str], key: Optional[str], db: Session,
                            allow_platform: bool = False) -> Company:
    """Tenant for a public embed route: ``?c=<slug>`` preferred, ``?key=`` deprecated."""
    if c:
        return _get_company_by_slug(c, db, allow_platform=allow_platform)
    if key:
        # Logged so the deprecation window closes on evidence rather than on a guess.
        logger.warning("deprecated ?key= used on a public route; migrate to ?c=<slug>")
        return _get_company_by_key(key, db)
    raise HTTPException(
        status_code=422,
        detail="Missing company identifier. Pass ?c=<company-slug>.",
    )


def _company_brand(company: Company) -> dict:
    """Branding block shared by every public response."""
    return {
        "name": company.name,
        "slug": company.slug,
        "logo_url": company.logo_url,
        "favicon_url": company.favicon_url,
        "primary_color": company.primary_color,
        "secondary_color": company.secondary_color,
        "accent_color": company.accent_color,
        "font_family": company.font_family,
        "tagline": company.tagline,
    }


def _job_card(job: JobPosting, desc_len: int) -> dict:
    """One job in a list response.

    ``desc_len`` differs per route by history — the embed list truncates at 200, the
    careers page at 300. Parameterised rather than unified, so neither payload changes
    shape under callers that already depend on it.
    """
    return {
        "id": job.id,
        "title": job.title,
        "location": job.location,
        "employment_type": job.employment_type,
        "salary_min": float(job.salary_min) if job.salary_min else None,
        "salary_max": float(job.salary_max) if job.salary_max else None,
        "experience_years": job.experience_years,
        "description": (job.description or "")[:desc_len],
        "required_skills": parse_skills(job.required_skills),
        "created_at": job.created_at,
    }


def _active_jobs(company: Company, db: Session):
    return (
        db.query(JobPosting)
        .filter(JobPosting.company_id == company.id, JobPosting.status == "active")
        .order_by(JobPosting.created_at.desc())
        .all()
    )


@router.get("/jobs")
async def get_public_jobs(
    c: Optional[str] = Query(None, description="Company slug — the public identifier"),
    key: Optional[str] = Query(
        None, deprecated=True, description="Deprecated: company API key. Use ?c= instead."
    ),
    db: Session = Depends(get_db)
):
    """
    Active jobs for a company. No authentication required — used for iframe embeds on
    client websites.

    Usage on a client's website:
        <iframe src="https://career.innomaxskills.com/acme-corp/jobs"
                width="100%" height="700"></iframe>
    """
    # The platform company's own openings are listable too. Only the listing: its
    # company-info and slug login stay refused, so the board sends applicants to the
    # platform's own /login and /register instead of a slug portal (is_platform).
    company = _resolve_public_company(c, key, db, allow_platform=True)
    jobs = _active_jobs(company, db)
    is_platform = (company.slug or "").strip().lower() == platform_slug().strip().lower()
    return {
        "company": {**_company_brand(company), "is_platform": is_platform},
        "jobs": [_job_card(j, 200) for j in jobs],
        "total": len(jobs),
    }


@router.get("/company-info")
async def get_company_info(
    c: Optional[str] = Query(None, description="Company slug — the public identifier"),
    key: Optional[str] = Query(
        None, deprecated=True, description="Deprecated: company API key. Use ?c= instead."
    ),
    db: Session = Depends(get_db)
):
    """Branding for a company, used to theme the login and careers pages."""
    return _company_brand(_resolve_public_company(c, key, db))


class ResolveKeyRequest(BaseModel):
    key: str


@router.post("/resolve-key")
@limiter.limit("10/minute")
async def resolve_key(
    request: Request,
    data: ResolveKeyRequest,
    db: Session = Depends(get_db),
):
    """Exchange a legacy ``sk_live_`` key for the company's slug.

    Exists only so the SPA can clean up an old ``?key=`` link: resolve the key once, then
    rewrite the address bar to the slug URL so the secret stops travelling in ``Referer``
    headers and bookmarks.

    The key goes in the **body**, not the query string — a GET would write it into the
    access log a second time, which is the very thing this is here to stop.

    Rate-limited because it answers "is this key valid?". Delete alongside the ``?key=``
    parameters at the end of the deprecation window.
    """
    return {"slug": _get_company_by_key(data.key, db).slug}


# ==================== TENANT-AWARE CAREERS PAGE ====================
# These routes are resolved by TenantDomainMiddleware (Host header → Company).
# Access via:  https://acme.hireflow.io/api/v1/public/careers
#              https://careers.acmecorp.com/api/v1/public/careers


@router.get("/careers")
async def get_careers_page(
    company: Company = Depends(require_request_company),
    db: Session = Depends(get_db),
):
    """
    Active jobs for the company resolved from the request Host header.
    No slug or key needed — the tenant is identified by subdomain or custom domain.
    """
    jobs = _active_jobs(company, db)
    return {
        "company": _company_brand(company),
        "jobs": [_job_card(j, 300) for j in jobs],
        "total": len(jobs),
    }


@router.get("/careers/{job_id}")
async def get_careers_job_detail(
    job_id: str,
    company: Company = Depends(require_request_company),
    db: Session = Depends(get_db),
):
    """
    Full detail for a single job, validated against the current tenant.
    """
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == company.id,
        JobPosting.status == "active",
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "company": _company_brand(company),
        "job": {
            "id": job.id,
            "title": job.title,
            "description": job.description,
            "requirements": job.requirements,
            "location": job.location,
            "employment_type": job.employment_type,
            "salary_min": float(job.salary_min) if job.salary_min else None,
            "salary_max": float(job.salary_max) if job.salary_max else None,
            "experience_years": job.experience_years,
            "required_skills": parse_skills(job.required_skills),
            "created_at": job.created_at,
        },
    }
