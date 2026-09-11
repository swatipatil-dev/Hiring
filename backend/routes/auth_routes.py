"""
🔐 AUTHENTICATION API ROUTES
Handles user registration, login, logout, and token management
"""

import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, status, Depends
from src.rate_limit import limiter

logger = logging.getLogger(__name__)
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from src.config.database_config_mysql import get_db
from src.auth import (
    UserCreate, UserLogin, Token, create_user, login_user,
    create_token_response, get_current_user, set_user_otp
)
from src.models.models import User, Company


def _resolve_company_id(db: Session, request: Request, api_key: Optional[str]) -> Optional[str]:
    """Resolve the company context for a public auth call.

    Preference order:
      1. ``api_key`` from the request body (e.g. /app?key=... portal flow).
      2. Tenant subdomain set by ``TenantDomainMiddleware``.
    """
    if api_key:
        company = db.query(Company).filter(
            Company.api_key == api_key, Company.is_active == True
        ).first()
        if company:
            return company.id
    return getattr(request.state, "company_id", None)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str
    api_key: Optional[str] = None

class ResendOTPRequest(BaseModel):
    email: EmailStr
    api_key: Optional[str] = None

class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    api_key: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str
    api_key: Optional[str] = None


class RegisterRequest(UserCreate):
    api_key: Optional[str] = None


class LoginRequest(UserLogin):
    api_key: Optional[str] = None
    # Set by the Innomax platform login page. When true, only superadmin and
    # the platform's own (Innomax) users may sign in here — client-company users
    # are sent to their own company login page.
    platform_login: Optional[bool] = None


def _scoped_user_by_email(
    db: Session, email: str, company_id: Optional[str]
) -> Optional[User]:
    """Look up a user by email, scoped to a company when provided.

    With same-email-different-company now allowed, all by-email lookups
    (OTP verify/resend, password reset, etc.) must be scoped or they'll pick an
    arbitrary row. Falls back to a global lookup only when no company context
    was given (e.g. legacy HireFlow domain login).
    """
    q = db.query(User).filter(User.email == email)
    if company_id is not None:
        q = q.filter(User.company_id == company_id)
    return q.first()


@router.post("/register", status_code=status.HTTP_201_CREATED)
# @limiter.limit("10/hour")  # disabled: students register in batches from one
# shared campus IP, so a per-IP cap rejects the whole batch. Re-enable if abuse appears.
async def register(request: Request, user_data: RegisterRequest, db: Session = Depends(get_db)):
    """
    Public self-registration — candidates only.
    Role is locked to 'candidate'. HR/Admin/Interviewer are invited by their company admin.
    Superadmin is seeded directly in the database — never created through this endpoint.

    The candidate is bound to a company when the request includes the company's
    ``api_key`` (from a company portal at /app?key=...) or arrives on a tenant
    subdomain. A given email may register at multiple companies, but only once
    per company.
    """
    # Only candidates may self-register
    if user_data.role != "candidate":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Self-registration is only available for candidates. "
                   "HR, Admin, and Interviewer accounts are created by your company admin.",
        )

    company_id = _resolve_company_id(db, request, user_data.api_key)
    if company_id is None:
        # Bare HireFlow sign-up (no client portal key / tenant domain) belongs to
        # the platform's own org (Innomax) — so HireFlow candidates share the same
        # company as the superadmin/admin. Client-portal sign-ups keep their client id.
        # ensure_* creates Innomax if missing, so company_id can never stay None.
        from src.tenancy.platform_company import ensure_platform_company
        company_id = ensure_platform_company(db).id

    try:
        # Strip api_key before forwarding so create_user only sees fields the
        # User model knows about.
        core = UserCreate(**user_data.model_dump(exclude={"api_key"}))
        user = create_user(db, core, company_id=company_id)
        return {
            "message": "Account created. A 6-digit OTP has been sent to your email.",
            "email": user.email,
            "requires_verification": True,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}"
        )


@router.post("/verify-otp", response_model=Token)
async def verify_otp(request: Request, data: OTPVerifyRequest, db: Session = Depends(get_db)):
    """Verify the OTP sent to the user's email and return an access token."""
    company_id = _resolve_company_id(db, request, data.api_key)
    user = _scoped_user_by_email(db, data.email, company_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Email already verified. Please log in.")
    if not user.otp_code or user.otp_code != data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    if user.otp_expires_at and datetime.utcnow() > user.otp_expires_at:
        raise HTTPException(status_code=400, detail="OTP has expired. Request a new one.")
    user.email_verified = True
    user.otp_code = None
    user.otp_expires_at = None
    db.commit()
    db.refresh(user)
    return create_token_response(user, db)


@router.post("/resend-otp")
@limiter.limit("3/minute")
async def resend_otp(request: Request, data: ResendOTPRequest, db: Session = Depends(get_db)):
    """Resend a fresh OTP to the given email (rate-limit: only if not already verified)."""
    company_id = _resolve_company_id(db, request, data.api_key)
    user = _scoped_user_by_email(db, data.email, company_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email_verified:
        raise HTTPException(status_code=400, detail="Email already verified")
    otp = set_user_otp(user, db)
    try:
        from src.services.email_service import send_otp_email, resolve_auth_sender
        smtp_config = resolve_auth_sender(db, user.company_id)
        result = send_otp_email(user.email, user.first_name, otp, smtp_config=smtp_config)
    except Exception as e:
        result = {"sent": False, "error": str(e)}
    return {"message": "OTP resent", "email": user.email, "email_sent": result.get("sent", False) if isinstance(result, dict) else True}

@router.post("/login", response_model=Token)
@limiter.limit("5/minute")
async def login(
    request: Request,
    user_credentials: LoginRequest,
    db: Session = Depends(get_db),
):
    """
    Authenticate user and return access token.

    Rules enforced:
    - When the request supplies a company ``api_key`` (e.g. /app?key=...), login
      is scoped to that company so the same email at multiple companies
      resolves unambiguously.
    - On a tenant domain (acme.hireflow.io) only that company's users can log in.
    - Superadmin can only log in from the platform domain (no tenant_company_id on request).
    - Suspended company users are rejected with a clear message.
    """
    try:
        scope_company_id = _resolve_company_id(db, request, user_credentials.api_key)
        user = login_user(
            db,
            user_credentials.email,
            user_credentials.password,
            company_id=scope_company_id,
        )
        if not user:
            # A user may only sign in through their own company's login page.
            # When the login is scoped to a company (api_key / tenant) and this
            # email is not registered with that company — but IS registered with
            # another — guide them to register here instead of a vague
            # "wrong password". Wrong password for a user that *does* belong to
            # this company stays generic so we don't reveal which emails exist.
            if scope_company_id:
                here = db.query(User).filter(
                    User.email == user_credentials.email,
                    User.company_id == scope_company_id,
                ).first()
                if not here:
                    elsewhere = db.query(User).filter(
                        User.email == user_credentials.email,
                        User.company_id != scope_company_id,
                    ).first()
                    if elsewhere:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="You're not registered with this company. Please register first to continue.",
                        )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        tenant_company_id = getattr(request.state, "company_id", None)

        # Superadmin may only log in from the platform domain
        if user.role == "superadmin" and tenant_company_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Client company users may not log in from a different tenant's domain
        if tenant_company_id and user.company_id != tenant_company_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Reject login if the company has been suspended
        if user.company_id:
            company = db.query(Company).filter(Company.id == user.company_id).first()
            if company and not company.is_active:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Your company account has been suspended. Contact support@hireflow.io",
                )

        # Platform login page: only superadmin and the platform's own (Innomax)
        # users may sign in here. A client-company user (e.g. an avanionline
        # candidate) must use their own company's login page.
        if user_credentials.platform_login and user.role != "superadmin":
            from src.tenancy.platform_company import get_platform_company
            platform = get_platform_company(db)
            platform_id = platform.id if platform else None
            if user.company_id and user.company_id != platform_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You're not a registered user for this company. Please complete your registration to continue.",
                )

        return create_token_response(user, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )

@router.post("/refresh", response_model=Token)
async def refresh_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Issue a fresh access token for the currently authenticated user."""
    return create_token_response(current_user, db)

@router.post("/logout")
async def logout(current_user: User = Depends(get_current_user)):
    """
    Logout user (client-side token removal)

    Note: In a stateless JWT system, logout is typically handled client-side
    by removing the token. This endpoint is provided for completeness and
    potential future server-side token blacklisting.
    """
    return {"message": "Successfully logged out"}

@router.get("/me")
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    Get current authenticated user information
    """
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "role": current_user.role,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "phone": current_user.phone,
        "is_active": current_user.is_active,
        "created_at": current_user.created_at
    }

@router.get("/verify-token")
async def verify_token(current_user: User = Depends(get_current_user)):
    """
    Verify if the current token is valid
    """
    return {
        "valid": True,
        "user_id": str(current_user.id),
        "role": current_user.role
    }


@router.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(request: Request, data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Send a password-reset OTP to the user's registered email."""
    company_id = _resolve_company_id(db, request, data.api_key)
    user = _scoped_user_by_email(db, data.email, company_id)
    # Always return success to avoid user enumeration
    if not user:
        return {"message": "If that email is registered, an OTP has been sent."}
    otp = set_user_otp(user, db)
    try:
        from src.services.email_service import send_password_reset_otp, resolve_auth_sender
        smtp_config = resolve_auth_sender(db, user.company_id)
        send_password_reset_otp(user.email, user.first_name, otp, smtp_config=smtp_config)
    except Exception as _e:
        logger.warning(f"password-reset OTP email to {user.email} failed: {_e}")
    return {"message": "If that email is registered, an OTP has been sent.", "email": user.email}


@router.post("/reset-password")
async def reset_password(request: Request, data: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Verify reset OTP and set a new password."""
    company_id = _resolve_company_id(db, request, data.api_key)
    user = _scoped_user_by_email(db, data.email, company_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.otp_code or user.otp_code != data.otp:
        raise HTTPException(status_code=400, detail="Invalid OTP")
    if user.otp_expires_at and datetime.utcnow() > user.otp_expires_at:
        raise HTTPException(status_code=400, detail="OTP has expired. Request a new one.")
    if len(data.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    from src.auth import get_password_hash
    user.password_hash = get_password_hash(data.new_password)
    user.otp_code = None
    user.otp_expires_at = None
    db.commit()
    return {"message": "Password reset successfully. You can now log in."}
