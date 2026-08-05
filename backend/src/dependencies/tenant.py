"""
Tenant dependencies
===================
FastAPI dependencies that expose the company resolved by TenantDomainMiddleware.

Usage in a route:
    from src.dependencies import get_request_company

    @router.get("/careers")
    def careers(company = Depends(get_request_company), db = Depends(get_db)):
        ...
"""

from typing import Optional, Dict, Any

from fastapi import Request, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.config.database_config_mysql import get_db
from src.models.models import Company


def get_request_company_id(request: Request) -> Optional[str]:
    """Return the company_id attached by TenantDomainMiddleware, or None."""
    return getattr(request.state, "company_id", None)


def get_request_branding(request: Request) -> Optional[Dict[str, Any]]:
    """Return the branding dict attached by TenantDomainMiddleware, or None."""
    return getattr(request.state, "company_branding", None)


def get_request_company(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[Company]:
    """
    Return the full Company ORM object for the current request's tenant.
    Returns None when the request is not coming from a custom/slug domain.
    """
    company_id: Optional[str] = getattr(request.state, "company_id", None)
    if not company_id:
        return None
    return db.query(Company).filter(Company.id == company_id).first()


def require_request_company(
    company: Optional[Company] = Depends(get_request_company),
) -> Company:
    """
    Like get_request_company but raises 404 when no tenant is resolved.
    Use this on routes that ONLY make sense on a custom/slug domain.
    """
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No tenant found for this domain.",
        )
    return company
