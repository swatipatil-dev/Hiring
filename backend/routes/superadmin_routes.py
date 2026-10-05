"""
Super Admin Routes — manage all client companies
Only accessible to users with role = 'superadmin'
"""

import os
import secrets
import uuid
from datetime import datetime, date
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request, status, Depends, UploadFile, File
from pydantic import BaseModel, EmailStr
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.exc import DataError, IntegrityError

from src.config.database_config_mysql import get_db
from src.auth import get_current_user, get_password_hash, validate_password_strength
from src.models.models import (
    Company, User, JobPosting, Candidate, Department, SubscriptionPlan, AuditLog, Notification,
)
from src.schemas.company_profile import COMPANY_PROFILE_FIELDS, serialize_company
from src.services.audit_log import audit_superadmin_request, active_grant, record_audit
from src.tenancy.slug import slugify, is_reserved_slug

# Every request a superadmin makes here is written to audit_log before it runs.
router = APIRouter(
    prefix="/api/v1/superadmin",
    tags=["Super Admin"],
    dependencies=[Depends(audit_superadmin_request)],
)


# ── Guards ────────────────────────────────────────────────────────────────────

def require_superadmin(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Guards all superadmin endpoints with three checks:
    1. Role must be 'superadmin'  (only one such account — seeded in DB for HireFlow owner)
    2. Request must come from the platform domain, not a client tenant domain
    3. Superadmin must belong to the HireFlow platform company (PLATFORM_SLUG)
    """
    if current_user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    # Block superadmin API calls originating from a tenant subdomain / custom domain
    if getattr(request.state, "company_id", None):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin API is only accessible from the HireFlow platform domain",
        )

    # Verify this superadmin belongs to the platform company (not a rogue row)
    platform = db.query(Company).filter(Company.slug == PLATFORM_SLUG).first()
    if platform and current_user.company_id and current_user.company_id != platform.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    return current_user


LOGOS_DIR = "logos"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}


@router.post("/upload-logo")
async def upload_company_logo(
    file: UploadFile = File(...),
    _: User = Depends(require_superadmin)
):
    """Upload a company logo image; returns the served URL."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    ext = ""
    if file.filename and "." in file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = "png"

    filename = f"{uuid.uuid4()}.{ext}"
    key = f"{LOGOS_DIR}/{filename}"

    contents = await file.read()
    from src.services.storage import storage
    storage.save_bytes(contents, key)

    return {"logo_url": f"/logos/{filename}"}


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class CreateCompanyRequest(BaseModel):
    # ── Required: company basics + first admin user ──
    company_name: str
    slug: str                        # e.g. "acme" → acme.hireflow.io
    admin_email: EmailStr
    admin_password: str
    admin_first_name: str
    admin_last_name: str

    # ── Branding (optional) ──
    logo_url: Optional[str] = None
    logo_dark_url: Optional[str] = None
    favicon_url: Optional[str] = None
    primary_color: Optional[str] = "#2b7cba"
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    font_family: Optional[str] = None
    tagline: Optional[str] = None
    support_email: Optional[str] = None

    # ── Legal & tax identity ──
    legal_name: Optional[str] = None
    brand_name: Optional[str] = None
    company_type: Optional[str] = None
    cin: Optional[str] = None
    incorporation_date: Optional[date] = None
    authorized_signatory_name: Optional[str] = None
    authorized_signatory_designation: Optional[str] = None

    # ── Role-based contacts ──
    primary_contact_name: Optional[str] = None
    primary_contact_email: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    primary_contact_designation: Optional[str] = None

    billing_contact_name: Optional[str] = None
    billing_contact_email: Optional[str] = None
    billing_contact_phone: Optional[str] = None
    billing_contact_designation: Optional[str] = None

    technical_contact_name: Optional[str] = None
    technical_contact_email: Optional[str] = None
    technical_contact_phone: Optional[str] = None
    technical_contact_designation: Optional[str] = None

    dpo_contact_name: Optional[str] = None
    dpo_contact_email: Optional[str] = None
    dpo_contact_phone: Optional[str] = None
    dpo_contact_designation: Optional[str] = None

    escalation_contact_name: Optional[str] = None
    escalation_contact_email: Optional[str] = None
    escalation_contact_phone: Optional[str] = None
    escalation_contact_designation: Optional[str] = None

    # ── Addresses ──
    reg_address_line1: Optional[str] = None
    reg_address_line2: Optional[str] = None
    reg_city: Optional[str] = None
    reg_state: Optional[str] = None
    reg_country: Optional[str] = None
    reg_postal_code: Optional[str] = None

    billing_same_as_registered: Optional[bool] = True
    billing_address_line1: Optional[str] = None
    billing_address_line2: Optional[str] = None
    billing_city: Optional[str] = None
    billing_state: Optional[str] = None
    billing_country: Optional[str] = None
    billing_postal_code: Optional[str] = None

    # ── Business profile ──
    industry: Optional[str] = None
    company_size_band: Optional[str] = None
    years_in_business: Optional[int] = None
    website_url: Optional[str] = None
    description: Optional[str] = None

    # ── Hiring footprint ──
    expected_hires_per_year: Optional[int] = None
    recruiter_count: Optional[int] = None
    hiring_locations: Optional[str] = None
    existing_ats: Optional[str] = None
    role_categories: Optional[str] = None

    # ── Commercial (writes to subscription_plans, not companies) ──
    plan_name: Optional[str] = None         # starter | growth | enterprise
    billing_cycle: Optional[str] = None      # monthly | annual
    price: Optional[float] = None
    currency: Optional[str] = None           # default INR if plan_name given
    payment_method: Optional[str] = None     # bank_transfer | card | upi | other
    po_required: Optional[bool] = None
    po_number: Optional[str] = None

    # ── Departments to seed alongside the company ──
    departments: Optional[List[dict]] = None  # [{name, description}]


class CompanyResponse(BaseModel):
    id: str
    name: str
    slug: str
    logo_url: Optional[str]
    primary_color: str
    api_key: Optional[str]
    is_active: bool
    created_at: datetime
    total_users: int = 0
    total_jobs: int = 0
    total_candidates: int = 0
    total_departments: int = 0


# ── Endpoints ─────────────────────────────────────────────────────────────────

from src.tenancy.platform_company import platform_slug
PLATFORM_SLUG = platform_slug()   # identifies the platform owner company (env-driven)

@router.get("/own-company")
async def get_own_company(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Return the platform's own company (Innomax) including its departments."""
    company = db.query(Company).filter(Company.slug == PLATFORM_SLUG).first()
    if not company:
        raise HTTPException(status_code=404, detail="Own company not found. Create it first with slug 'innomax'.")
    depts = db.query(Department).filter(Department.company_id == company.id).order_by(Department.name).all()
    return {
        "id": company.id,
        "name": company.name,
        "slug": company.slug,
        "logo_url": company.logo_url,
        "primary_color": company.primary_color or "#2b7cba",
        "departments": [{"id": d.id, "name": d.name, "description": d.description} for d in depts],
    }


@router.post("/companies", status_code=status.HTTP_201_CREATED)
async def create_company(
    data: CreateCompanyRequest,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Create a new client company and their first admin user."""
    validate_password_strength(data.admin_password)
    # Normalize the slug into a safe subdomain (source of truth — mirrors the
    # frontend slugify so a direct API caller gets the same rules as the UI).
    slug = slugify(data.slug)
    if not slug:
        raise HTTPException(status_code=400, detail="Slug is empty or invalid. Use letters, digits and hyphens.")
    if is_reserved_slug(slug):
        raise HTTPException(status_code=400, detail=f"Slug '{slug}' is reserved and cannot be used.")
    # Uniqueness — case-insensitive (the subdomain is case-insensitive in practice)
    if db.query(Company).filter(func.lower(Company.slug) == slug).first():
        raise HTTPException(status_code=400, detail="Slug already taken")
    if db.query(User).filter(User.email == data.admin_email).first():
        raise HTTPException(status_code=400, detail="Admin email already registered")

    # Create company with generated API key
    api_key = "sk_live_" + secrets.token_urlsafe(32)
    company = Company(
        id=str(uuid.uuid4()),
        name=data.company_name,
        slug=slug,
        primary_color=data.primary_color or "#2b7cba",
        api_key=api_key,
        is_active=True,
    )
    # Apply every optional onboarding-profile field that was provided
    for field in COMPANY_PROFILE_FIELDS:
        value = getattr(data, field, None)
        if value is not None and field != "primary_color":  # primary_color already handled with default
            setattr(company, field, value)
    db.add(company)
    db.flush()

    # Create admin user linked to this company
    admin = User(
        id=str(uuid.uuid4()),
        company_id=company.id,
        email=data.admin_email,
        password_hash=get_password_hash(data.admin_password),
        role="admin",
        first_name=data.admin_first_name,
        last_name=data.admin_last_name,
        is_active=True,
        email_verified=True,
    )
    db.add(admin)

    # Seed departments (if any queued during onboarding)
    if data.departments:
        for d in data.departments:
            dept_name = (d.get("name") or "").strip()
            if not dept_name:
                continue
            db.add(Department(
                id=str(uuid.uuid4()),
                company_id=company.id,
                name=dept_name,
                description=d.get("description"),
            ))

    # Create subscription row if any commercial field was provided
    has_commercial = any(v is not None for v in (
        data.plan_name, data.billing_cycle, data.price, data.currency,
        data.payment_method, data.po_required, data.po_number,
    ))
    if has_commercial:
        db.add(SubscriptionPlan(
            id=str(uuid.uuid4()),
            company_id=company.id,
            plan_name=data.plan_name or "starter",
            billing_cycle=data.billing_cycle or "monthly",
            price=data.price if data.price is not None else 0,
            currency=data.currency or "INR",
            payment_method=data.payment_method,
            po_required=bool(data.po_required) if data.po_required is not None else False,
            po_number=data.po_number,
            status="trial",
        ))

    try:
        db.commit()
    except DataError as exc:
        db.rollback()
        # Most common: a value (often browser-autofilled) exceeds a column limit.
        raise HTTPException(
            status_code=400,
            detail="One of the fields is too long or invalid (check Slug and PIN/ZIP — "
                   "they may have been auto-filled with your email). Please correct it and try again.",
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Could not create company — the slug or admin email may already be in use.",
        ) from exc
    db.refresh(company)

    # Welcome email — fire-and-log; never fail the create on SMTP issues.
    # This is platform→client "official" comms: the PLATFORM company's
    # platform.tenant_welcome route (seeded from INNOMAX_OFFICIAL_*), not the
    # brand-new client company, which has no sender yet.
    try:
        from src.services.email_service import send_admin_welcome_email, resolve_sender
        from src.tenancy.platform_company import get_platform_company
        _platform = get_platform_company(db)
        smtp_config = resolve_sender(db, _platform.id if _platform else None,
                                     event_type="platform.tenant_welcome")
        login_url = f"{str(request.base_url).rstrip('/')}/admin/login"
        send_admin_welcome_email(
            to=data.admin_email,
            name=data.admin_first_name,
            company_name=company.name,
            login_url=login_url,
            branding={"company_name": company.name, "primary_color": company.primary_color,
                      "support_email": company.support_email},
            smtp_config=smtp_config,
        )
    except Exception as exc:  # noqa: BLE001 — email failures must not block onboarding
        import logging
        logging.getLogger(__name__).warning(
            "Welcome email failed for %s (%s): %s", data.admin_email, company.id, exc
        )

    return {
        "id": company.id,
        "name": company.name,
        "slug": company.slug,
        "api_key": company.api_key,
        "admin_email": data.admin_email,
        "message": "Company created successfully"
    }


@router.get("/companies")
async def list_companies(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """List all client companies with usage stats (excludes the platform's own org)."""
    companies = (
        db.query(Company)
        .filter(Company.slug != PLATFORM_SLUG)
        .order_by(Company.created_at.desc())
        .all()
    )
    result = []
    for c in companies:
        user_count = db.query(User).filter(User.company_id == c.id).count()
        job_count = db.query(JobPosting).filter(JobPosting.company_id == c.id).count()
        candidate_count = db.query(Candidate).filter(Candidate.company_id == c.id).count()
        dept_count = db.query(Department).filter(Department.company_id == c.id).count()
        result.append({
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "logo_url": c.logo_url,
            "primary_color": c.primary_color,
            "api_key": c.api_key,
            "is_active": c.is_active,
            "created_at": c.created_at,
            "total_users": user_count,
            "total_jobs": job_count,
            "total_candidates": candidate_count,
            "total_departments": dept_count,
        })
    return result


@router.get("/companies/{company_id}")
async def get_company(
    company_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Get a single company's full onboarding profile.

    The company's admins are always listed — the superadmin manages those accounts.
    Everyone else (HR, interviewers, candidates) is the client's people data and is
    listed only while the company has granted support access.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    grant = active_grant(db, company_id)
    users_q = db.query(User).filter(User.company_id == company_id)
    if grant is None:
        users_q = users_q.filter(User.role == "admin")
    else:
        from src.services.audit_log import _audit_or_deny
        _audit_or_deny(db, action="superadmin.read", actor=_, company_id=company_id,
                       resource=f"company {company_id} user list", request=request,
                       detail={"grant_id": grant.id})
    users = users_q.all()
    sub = db.query(SubscriptionPlan).filter(SubscriptionPlan.company_id == company_id).first()
    payload = serialize_company(company)
    payload["users"] = [
        {"id": u.id, "email": u.email, "role": u.role, "is_active": u.is_active}
        for u in users
    ]
    payload["support_access"] = {
        "active": grant is not None,
        "expires_at": grant.expires_at.isoformat() if grant else None,
    }
    payload["subscription"] = None if not sub else {
        "plan_name": sub.plan_name,
        "billing_cycle": sub.billing_cycle,
        "price": float(sub.price) if sub.price is not None else None,
        "currency": sub.currency,
        "payment_method": sub.payment_method,
        "po_required": sub.po_required,
        "po_number": sub.po_number,
        "status": sub.status,
    }
    return payload


@router.put("/companies/{company_id}/toggle")
async def toggle_company(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Enable or disable a client company.

    Suspending also revokes every session its users hold (token_version bump), so
    nobody stays signed in on a token minted before the suspension.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.is_active = not company.is_active
    if not company.is_active:
        db.query(User).filter(User.company_id == company_id).update(
            {User.token_version: func.coalesce(User.token_version, 0) + 1},
            synchronize_session=False,
        )
    db.commit()
    return {"id": company.id, "is_active": company.is_active}


@router.post("/companies/{company_id}/regenerate-key")
async def regenerate_api_key(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Generate a new API key for a company (invalidates the old one)."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    company.api_key = "sk_live_" + secrets.token_urlsafe(32)
    db.commit()
    return {"id": company.id, "api_key": company.api_key}


class CreateAdminUserRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str


@router.post("/companies/{company_id}/admin", status_code=status.HTTP_201_CREATED)
async def create_company_admin(
    company_id: str,
    data: CreateAdminUserRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    """Add an admin user to an existing client company.

    An admin account opens everything a company holds, so this is support access in
    its strongest form. Without a grant it is allowed only for a company that has no
    active admin (onboarding, or recovering a company whose admins are all gone).
    Once a company has an admin, that admin must grant support access first — and is
    told, in-app, that an admin was added. Either way the creation is audited, in the
    same transaction: no audit row, no account.
    """
    from src.services.audit_log import require_support_access
    validate_password_strength(data.password)
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    existing_admins = db.query(User).filter(
        User.company_id == company_id, User.role == "admin", User.is_active.is_(True),
    ).all()
    grant = None
    if existing_admins:
        grant = require_support_access(db, request, current_user, company_id,
                                       f"create admin account in company {company_id}")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    admin = User(
        id=str(uuid.uuid4()),
        company_id=company_id,
        email=data.email,
        password_hash=get_password_hash(data.password),
        role="admin",
        first_name=data.first_name,
        last_name=data.last_name,
        is_active=True,
        email_verified=True,
    )
    db.add(admin)
    db.flush()
    for existing in existing_admins:
        db.add(Notification(
            user_id=existing.id, company_id=company_id,
            title="A new admin was added by platform support",
            message=f"Platform support ({current_user.email}) added {admin.email} as an admin "
                    f"of your company. If you did not expect this, revoke support access and "
                    f"deactivate the account.",
            type="security", reference_type="user", reference_id=admin.id,
        ))
    record_audit(db, action="superadmin.create_admin", actor=current_user, company_id=company_id,
                 resource=f"users/{admin.id}", request=request,
                 detail={"new_admin_email": admin.email, "grant_id": grant.id if grant else None,
                         "company_had_admin": bool(existing_admins)},
                 commit=False, required=True)
    db.commit()
    db.refresh(admin)
    return {"id": admin.id, "email": admin.email, "role": "admin", "message": "Admin created successfully"}


class UpdateCompanyRequest(BaseModel):
    company_name: Optional[str] = None
    slug: Optional[str] = None
    # Branding
    logo_url: Optional[str] = None
    logo_dark_url: Optional[str] = None
    favicon_url: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    accent_color: Optional[str] = None
    font_family: Optional[str] = None
    tagline: Optional[str] = None
    support_email: Optional[str] = None
    # Legal & tax identity
    legal_name: Optional[str] = None
    brand_name: Optional[str] = None
    company_type: Optional[str] = None
    cin: Optional[str] = None
    incorporation_date: Optional[date] = None
    authorized_signatory_name: Optional[str] = None
    authorized_signatory_designation: Optional[str] = None
    # Role-based contacts
    primary_contact_name: Optional[str] = None
    primary_contact_email: Optional[str] = None
    primary_contact_phone: Optional[str] = None
    primary_contact_designation: Optional[str] = None
    billing_contact_name: Optional[str] = None
    billing_contact_email: Optional[str] = None
    billing_contact_phone: Optional[str] = None
    billing_contact_designation: Optional[str] = None
    technical_contact_name: Optional[str] = None
    technical_contact_email: Optional[str] = None
    technical_contact_phone: Optional[str] = None
    technical_contact_designation: Optional[str] = None
    dpo_contact_name: Optional[str] = None
    dpo_contact_email: Optional[str] = None
    dpo_contact_phone: Optional[str] = None
    dpo_contact_designation: Optional[str] = None
    escalation_contact_name: Optional[str] = None
    escalation_contact_email: Optional[str] = None
    escalation_contact_phone: Optional[str] = None
    escalation_contact_designation: Optional[str] = None
    # Addresses
    reg_address_line1: Optional[str] = None
    reg_address_line2: Optional[str] = None
    reg_city: Optional[str] = None
    reg_state: Optional[str] = None
    reg_country: Optional[str] = None
    reg_postal_code: Optional[str] = None
    billing_same_as_registered: Optional[bool] = None
    billing_address_line1: Optional[str] = None
    billing_address_line2: Optional[str] = None
    billing_city: Optional[str] = None
    billing_state: Optional[str] = None
    billing_country: Optional[str] = None
    billing_postal_code: Optional[str] = None
    # Business profile
    industry: Optional[str] = None
    company_size_band: Optional[str] = None
    years_in_business: Optional[int] = None
    website_url: Optional[str] = None
    description: Optional[str] = None
    # Hiring footprint
    expected_hires_per_year: Optional[int] = None
    recruiter_count: Optional[int] = None
    hiring_locations: Optional[str] = None
    existing_ats: Optional[str] = None
    role_categories: Optional[str] = None


class CreateDepartmentRequest(BaseModel):
    name: str
    description: Optional[str] = None


@router.get("/companies/{company_id}/departments")
async def list_company_departments(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """List all departments for a company."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    depts = db.query(Department).filter(Department.company_id == company_id).order_by(Department.name).all()
    return [{"id": d.id, "name": d.name, "description": d.description, "created_at": d.created_at} for d in depts]


@router.post("/companies/{company_id}/departments", status_code=status.HTTP_201_CREATED)
async def create_company_department(
    company_id: str,
    data: CreateDepartmentRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Add a department to a company."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    existing = db.query(Department).filter(
        Department.name == data.name,
        Department.company_id == company_id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Department already exists")
    dept = Department(
        id=str(uuid.uuid4()),
        name=data.name,
        description=data.description,
        company_id=company_id
    )
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return {"id": dept.id, "name": dept.name, "description": dept.description, "created_at": dept.created_at}


@router.delete("/companies/{company_id}/departments/{dept_id}", status_code=status.HTTP_200_OK)
async def delete_company_department(
    company_id: str,
    dept_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Remove a department from a company."""
    dept = db.query(Department).filter(
        Department.id == dept_id,
        Department.company_id == company_id
    ).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    db.delete(dept)
    db.commit()
    return {"message": "Department deleted"}


@router.put("/companies/{company_id}")
async def update_company(
    company_id: str,
    data: UpdateCompanyRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin)
):
    """Update any company onboarding-profile field."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    if data.company_name is not None:
        company.name = data.company_name
    if data.slug is not None:
        slug = slugify(data.slug)
        if not slug:
            raise HTTPException(status_code=400, detail="Slug is empty or invalid. Use letters, digits and hyphens.")
        if is_reserved_slug(slug):
            raise HTTPException(status_code=400, detail=f"Slug '{slug}' is reserved and cannot be used.")
        existing = db.query(Company).filter(
            func.lower(Company.slug) == slug, Company.id != company_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Slug already taken")
        company.slug = slug
    for field in COMPANY_PROFILE_FIELDS:
        value = getattr(data, field, None)
        if value is not None:
            setattr(company, field, value)
    db.commit()
    db.refresh(company)
    return serialize_company(company)


# ══════════════════════════════════════════════════════════════════════════════
# CUSTOM DOMAIN MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

class CustomDomainRequest(BaseModel):
    domain: str  # e.g. "careers.acmecorp.com"


def _check_dns_txt(domain: str, expected_token: str) -> bool:
    """
    Look for a TXT record  _hireflow.<domain>  containing  hireflow-verify=<token>.
    Returns True on success, False on any failure (DNS error, record absent, wrong value).
    Uses dnspython when available; falls back to socket for a basic lookup attempt.
    """
    txt_host = f"_hireflow.{domain}"
    needle = f"hireflow-verify={expected_token}"

    # ── dnspython (preferred) ──────────────────────────────────────────────
    try:
        import dns.resolver  # type: ignore
        answers = dns.resolver.resolve(txt_host, "TXT")
        for rdata in answers:
            for txt_string in rdata.strings:
                decoded = txt_string.decode("utf-8") if isinstance(txt_string, bytes) else txt_string
                if decoded.strip() == needle:
                    return True
        return False
    except ImportError:
        pass  # dnspython not installed — fall through to socket fallback
    except Exception:
        return False

    # ── socket fallback (best-effort, platform-dependent) ─────────────────
    import socket
    try:
        results = socket.getaddrinfo(txt_host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        # socket cannot fetch TXT records — we cannot verify without dnspython
        return False
    except Exception:
        return False


@router.post("/companies/{company_id}/custom-domain")
async def initiate_custom_domain(
    company_id: str,
    data: CustomDomainRequest,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """
    Step 1 of custom-domain setup.

    Stores the requested domain and generates a DNS verification token.
    The company must add a DNS TXT record before calling the /verify endpoint.

    Returns the TXT record details the company needs to configure with their
    DNS provider.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    domain = data.domain.lower().strip().strip("/")

    # Reject domains that are already live and verified for another company
    conflict = (
        db.query(Company)
        .filter(
            Company.custom_domain == domain,
            Company.domain_verified == True,
            Company.id != company_id,
        )
        .first()
    )
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"Domain '{domain}' is already in use by another company.",
        )

    # Generate a fresh verification token
    token = secrets.token_urlsafe(32)

    company.domain_pending = domain
    company.domain_verify_token = token
    company.domain_verified = False
    # Clear any previously live custom domain while verification is in progress
    company.custom_domain = None
    db.commit()

    return {
        "status": "pending_verification",
        "domain": domain,
        "instructions": (
            f"Add the following DNS TXT record at your DNS provider, "
            f"then call POST /api/v1/superadmin/companies/{company_id}/custom-domain/verify"
        ),
        "dns_record": {
            "type": "TXT",
            "host": f"_hireflow.{domain}",
            "value": f"hireflow-verify={token}",
            "ttl": 300,
        },
    }


@router.post("/companies/{company_id}/custom-domain/verify")
async def verify_custom_domain(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """
    Step 2 of custom-domain setup.

    Performs a live DNS TXT lookup for  _hireflow.<pending_domain>  and checks
    that the value matches  hireflow-verify=<token>.

    On success, promotes domain_pending → custom_domain and marks it verified.
    Requires dnspython  (pip install dnspython)  for reliable TXT resolution.
    """
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    if not company.domain_pending or not company.domain_verify_token:
        raise HTTPException(
            status_code=400,
            detail="No pending domain verification. Call POST /custom-domain first.",
        )

    domain = company.domain_pending
    token = company.domain_verify_token

    verified = _check_dns_txt(domain, token)

    if not verified:
        # Check whether dnspython is available so we give a useful error
        try:
            import dns.resolver  # noqa: F401
            dns_available = True
        except ImportError:
            dns_available = False

        if not dns_available:
            raise HTTPException(
                status_code=503,
                detail=(
                    "DNS verification requires dnspython. "
                    "Install it with: pip install dnspython"
                ),
            )
        raise HTTPException(
            status_code=400,
            detail=(
                f"DNS TXT record not found or value mismatch. "
                f"Expected  _hireflow.{domain}  →  hireflow-verify={token}. "
                f"DNS changes can take up to 48 hours to propagate."
            ),
        )

    # Promote pending → live
    company.custom_domain = domain
    company.domain_verified = True
    company.domain_pending = None
    company.domain_verify_token = None
    db.commit()

    return {
        "status": "verified",
        "custom_domain": domain,
        "message": (
            f"Domain '{domain}' is now active. "
            f"Point your DNS A/CNAME record to this server's IP address."
        ),
    }


@router.delete("/companies/{company_id}/custom-domain", status_code=200)
async def remove_custom_domain(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Remove a verified custom domain and any pending verification state."""
    company = db.query(Company).filter(Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    removed_domain = company.custom_domain or company.domain_pending
    company.custom_domain = None
    company.domain_verified = False
    company.domain_pending = None
    company.domain_verify_token = None
    db.commit()

    return {
        "status": "removed",
        "removed_domain": removed_domain,
        "message": "Custom domain removed. The company will be accessible via its slug subdomain only.",
    }


# ── Audit log & support access ────────────────────────────────────────────────

@router.get("/audit-log")
async def get_audit_log(
    company_id: Optional[str] = None,
    actor_user_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Read the privileged-access audit trail, newest first. Read-only: there is no
    endpoint that edits or deletes an entry."""
    limit = max(1, min(limit, 500))
    q = db.query(AuditLog)
    if company_id:
        q = q.filter(AuditLog.company_id == company_id)
    if actor_user_id:
        q = q.filter(AuditLog.actor_user_id == actor_user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    total = q.count()
    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(max(0, offset)).limit(limit).all()
    return {"total": total, "entries": [_audit_row(r) for r in rows]}


@router.get("/companies/{company_id}/support-access")
async def get_support_access(
    company_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Whether this company currently allows support to read its candidate data."""
    grant = active_grant(db, company_id)
    return {
        "active": grant is not None,
        "expires_at": grant.expires_at.isoformat() if grant else None,
        "reason": grant.reason if grant else None,
    }


def _audit_row(r: AuditLog) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "company_id": r.company_id,
        "actor_user_id": r.actor_user_id,
        "actor_email": r.actor_email,
        "actor_role": r.actor_role,
        "action": r.action,
        "resource": r.resource,
        "method": r.method,
        "path": r.path,
        "ip": r.ip,
        "detail": r.detail,
    }
