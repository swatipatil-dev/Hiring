"""
Public Routes — no authentication required
Used for iframe/widget embedding on client websites
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from fastapi import Depends

from src.config.database_config_mysql import get_db
from src.models.models import Company, JobPosting
from src.dependencies import require_request_company

router = APIRouter(prefix="/api/v1/public", tags=["Public"])


def _get_company_by_key(api_key: str, db: Session) -> Company:
    company = db.query(Company).filter(
        Company.api_key == api_key,
        Company.is_active == True
    ).first()
    if not company:
        raise HTTPException(status_code=401, detail="Invalid or inactive API key")
    return company


@router.get("/jobs")
async def get_public_jobs(
    key: str = Query(..., description="Company API key"),
    db: Session = Depends(get_db)
):
    """
    Returns active jobs for a company identified by API key.
    No authentication required — used for iframe embeds on client websites.

    Usage on client's website:
        <iframe src="https://hireflow.io/jobs?key=sk_live_xxx" width="100%" height="700"></iframe>
    """
    company = _get_company_by_key(key, db)
    jobs = db.query(JobPosting).filter(
        JobPosting.company_id == company.id,
        JobPosting.status == "active"
    ).order_by(JobPosting.created_at.desc()).all()

    return {
        "company": {
            "name": company.name,
            "logo_url": company.logo_url,
            "favicon_url": company.favicon_url,
            "primary_color": company.primary_color,
            "secondary_color": company.secondary_color,
            "accent_color": company.accent_color,
            "font_family": company.font_family,
            "tagline": company.tagline,
        },
        "jobs": [
            {
                "id": j.id,
                "title": j.title,
                "location": j.location,
                "employment_type": j.employment_type,
                "salary_min": float(j.salary_min) if j.salary_min else None,
                "salary_max": float(j.salary_max) if j.salary_max else None,
                "experience_years": j.experience_years,
                "description": (j.description or "")[:200],
                "required_skills": j.required_skills or [],
                "created_at": j.created_at,
            }
            for j in jobs
        ],
        "total": len(jobs),
    }


@router.get("/company-info")
async def get_company_info(
    key: str = Query(..., description="Company API key"),
    db: Session = Depends(get_db)
):
    """Returns company branding info for a given API key."""
    company = _get_company_by_key(key, db)
    return {
        "name": company.name,
        "logo_url": company.logo_url,
        "favicon_url": company.favicon_url,
        "primary_color": company.primary_color,
        "secondary_color": company.secondary_color,
        "accent_color": company.accent_color,
        "font_family": company.font_family,
        "tagline": company.tagline,
    }


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
    Returns active jobs for the company resolved from the request Host header.
    No API key needed — tenant is identified by subdomain or custom domain.
    """
    jobs = (
        db.query(JobPosting)
        .filter(JobPosting.company_id == company.id, JobPosting.status == "active")
        .order_by(JobPosting.created_at.desc())
        .all()
    )
    return {
        "company": {
            "name": company.name,
            "logo_url": company.logo_url,
            "favicon_url": company.favicon_url,
            "primary_color": company.primary_color,
            "secondary_color": company.secondary_color,
            "accent_color": company.accent_color,
            "font_family": company.font_family,
            "tagline": company.tagline,
            "slug": company.slug,
        },
        "jobs": [
            {
                "id": j.id,
                "title": j.title,
                "location": j.location,
                "employment_type": j.employment_type,
                "salary_min": float(j.salary_min) if j.salary_min else None,
                "salary_max": float(j.salary_max) if j.salary_max else None,
                "experience_years": j.experience_years,
                "description": (j.description or "")[:300],
                "required_skills": j.required_skills or [],
                "created_at": j.created_at,
            }
            for j in jobs
        ],
        "total": len(jobs),
    }


@router.get("/careers/{job_id}")
async def get_careers_job_detail(
    job_id: str,
    company: Company = Depends(require_request_company),
    db: Session = Depends(get_db),
):
    """
    Returns full detail for a single job, validated against the current tenant.
    """
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == company.id,
        JobPosting.status == "active",
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "company": {
            "name": company.name,
            "logo_url": company.logo_url,
            "primary_color": company.primary_color,
            "slug": company.slug,
        },
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
            "required_skills": job.required_skills or [],
            "created_at": job.created_at,
        },
    }
