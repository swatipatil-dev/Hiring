"""
🔐 AUTHENTICATION API ROUTES
Handles user registration, login, logout, and token management
"""

import json
import logging
import re
import time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.rate_limit import limiter

logger = logging.getLogger(__name__)
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import func
from sqlalchemy.orm import Session
from src.config.database_config_mysql import get_db
from src.auth import (
    UserCreate, UserLogin, Token, create_user, login_user,
    create_token_response, get_current_user, set_user_otp, check_user_otp,
    clear_user_otp, verify_password, get_password_hash, validate_password_strength,
    revoke_user_sessions, create_mfa_token, decode_mfa_token, mfa_required_for,
    is_locked, locked_exception, register_login_failure, clear_login_failures,
    _authenticate_request, SESSION_MAX_HOURS,
)
from src.models.models import User, Company
from src import mfa as totp
from src.security import encrypt, decrypt
from src.tenancy.platform_company import platform_slug
from src.services.permissions_catalog import get_permissions_for_role


def _resolve_company_id(
    db: Session,
    request: Request,
    api_key: Optional[str] = None,
    company_slug: Optional[str] = None,
) -> Optional[str]:
    """Resolve the company context for a public auth call.

    Preference order:
      1. ``company_slug`` from the request body — the /<slug> portal flow.
      2. ``api_key`` — deprecated, for links still carrying ?key=.
      3. Tenant subdomain / custom domain, set by ``TenantDomainMiddleware``.

    **Fails closed.** An identifier that is *supplied but does not resolve* raises 404
    rather than falling through. Falling through used to mean a typo'd or stale portal
    link silently dropped tenant scoping: ``_scoped_user_by_email`` then did a global
    lookup, so a user from one company could sign in through another company's URL. Only
    an *absent* identifier falls back to the host.
    """
    if company_slug:
        normalised = company_slug.strip().lower()
        # The platform org is not a tenant, so it is not addressable by slug — the same
        # refusal public_routes makes. Without it here, a slug-scoped login against the
        # platform org still resolved, which is exactly what that guard claims to stop.
        if normalised == platform_slug().strip().lower():
            raise HTTPException(status_code=404, detail="Company not found")
        company = db.query(Company).filter(
            func.lower(Company.slug) == normalised,
            Company.is_active == True,
        ).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        return company.id

    if api_key:
        company = db.query(Company).filter(
            Company.api_key == api_key, Company.is_active == True
        ).first()
        if not company:
            raise HTTPException(status_code=404, detail="Company not found")
        return company.id

    return getattr(request.state, "company_id", None)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


# ``company_slug`` is the public company identifier (career.innomaxskills.com/<slug>).
# ``api_key`` is the deprecated ``sk_live_`` secret the old ?key= links carried; it is
# accepted only for the deprecation window and should be deleted with them.

class OTPVerifyRequest(BaseModel):
    email: EmailStr
    otp: str
    company_slug: Optional[str] = None
    api_key: Optional[str] = None

class ResendOTPRequest(BaseModel):
    email: EmailStr
    company_slug: Optional[str] = None
    api_key: Optional[str] = None

class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    company_slug: Optional[str] = None
    api_key: Optional[str] = None

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    otp: str
    new_password: str
    company_slug: Optional[str] = None
    api_key: Optional[str] = None


class RegisterRequest(UserCreate):
    company_slug: Optional[str] = None
    api_key: Optional[str] = None


class LoginRequest(UserLogin):
    company_slug: Optional[str] = None
    api_key: Optional[str] = None
    # Set by the Innomax platform login page. When true, only superadmin and
    # the platform's own (Innomax) users may sign in here — client-company users
    # are sent to their own company login page.
    platform_login: Optional[bool] = None


def _scoped_user_by_email(
    db: Session, email: str, company_id: Optional[str]
) -> Optional[User]:
    """Look up a user by email for the unauthenticated OTP flows (verify, resend,
    forgot/reset password). Never an arbitrary pick:

    * With a company context (portal slug, legacy key, tenant domain) the lookup is
      that company's user — never the superadmin, who cannot be reset or verified
      through a client portal.
    * Without one, the email must identify exactly one account. The same address at
      several companies is ambiguous, and an ambiguous lookup matches nothing rather
      than whichever row MySQL returns first. The superadmin is reachable only here,
      on the platform flow.
    """
    q = db.query(User).filter(User.email == email)
    if company_id is not None:
        return q.filter(User.company_id == company_id, User.role != "superadmin").first()
    matches = q.limit(2).all()
    return matches[0] if len(matches) == 1 else None


def _issue_login(user: User, db: Session):
    """The response to a correct password: the token, or the MFA step before it.

    * MFA enrolled            → {"mfa_required": true, "mfa_token": ...}; the token
                                 is exchanged at /auth/login/mfa with a code.
    * MFA required, not set up → {"mfa_enrollment_required": true, "mfa_token": ...};
                                 the token opens only the enrolment endpoints.
    * otherwise               → the access token.
    """
    if user.mfa_enabled:
        return {
            "mfa_required": True,
            "mfa_token": create_mfa_token(user, "mfa"),
            "methods": ["totp", "recovery_code"],
        }
    if mfa_required_for(user):
        return {
            "mfa_enrollment_required": True,
            "mfa_token": create_mfa_token(user, "mfa_enroll"),
        }
    return create_token_response(user, db)


@router.post("/register", status_code=status.HTTP_201_CREATED)
# Sized for a 300+ candidate campus drive, all behind one NAT'd IP. The original 10/hour
# was removed precisely because a per-IP cap rejected the whole batch; do not "tidy" this
# back down without knowing the largest drive it has to clear.
#
# There is a cap at all because a company is now addressed by a guessable slug rather than
# a 43-character key, and this endpoint sends OTP mail through the *tenant's own* SMTP
# sender — unthrottled, that is an email amplifier for any company name someone guesses.
# Treat it as a guard rail against runaway loops, not a security control: the limiter is
# in-process across 3 gunicorn workers (so the real ceiling is 1-3x this), and it keys on
# a client-supplied X-Forwarded-For.
@limiter.limit("500/hour")
async def register(request: Request, user_data: RegisterRequest, db: Session = Depends(get_db)):
    """
    Public self-registration — candidates only.
    Role is locked to 'candidate'. HR/Admin/Interviewer are invited by their company admin.
    Superadmin is seeded directly in the database — never created through this endpoint.

    The candidate is bound to a company when the request includes the company's
    ``company_slug`` (from a company portal at /<slug>), a legacy ``api_key``, or
    arrives on a tenant subdomain. A given email may register at multiple companies,
    but only once per company.
    """
    # Only candidates may self-register
    if user_data.role != "candidate":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Self-registration is only available for candidates. "
                   "HR, Admin, and Interviewer accounts are created by your company admin.",
        )

    company_id = _resolve_company_id(db, request, user_data.api_key, user_data.company_slug)
    if company_id is None:
        # Bare HireFlow sign-up (no client portal key / tenant domain) belongs to
        # the platform's own org (Innomax) — so HireFlow candidates share the same
        # company as the superadmin/admin. Client-portal sign-ups keep their client id.
        # ensure_* creates Innomax if missing, so company_id can never stay None.
        from src.tenancy.platform_company import ensure_platform_company
        company_id = ensure_platform_company(db).id

    try:
        # Strip the tenant selectors before forwarding so create_user only sees fields
        # the User model knows about.
        core = UserCreate(**user_data.model_dump(exclude={"api_key", "company_slug"}))
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


@router.post("/verify-otp")
# High, because a campus batch verifies in one burst right after registering — see the
# note on /register. Still bounded, so a scripted guess of 6-digit codes is not free.
@limiter.limit("120/minute")
async def verify_otp(request: Request, data: OTPVerifyRequest, db: Session = Depends(get_db)):
    """Verify the OTP sent to the user's email and return an access token.

    Every failure returns the same 400. Distinguishing "no such user" from "already
    verified" from "wrong code" told an unauthenticated caller which emails are
    registered with a company — and a company is now addressed by a guessable slug, so
    that oracle is cheap to point anywhere.
    """
    company_id = _resolve_company_id(db, request, data.api_key, data.company_slug)
    user = _scoped_user_by_email(db, data.email, company_id)
    _INVALID_CODE = HTTPException(status_code=400, detail="Invalid or expired code.")
    if not user:
        raise _INVALID_CODE
    if user.email_verified:
        raise _INVALID_CODE
    if not check_user_otp(user, db, data.otp, "verify"):
        raise _INVALID_CODE
    user.email_verified = True
    db.commit()
    db.refresh(user)
    return _issue_login(user, db)


@router.post("/resend-otp")
@limiter.limit("3/minute")
async def resend_otp(request: Request, data: ResendOTPRequest, db: Session = Depends(get_db)):
    """Resend a fresh OTP, if the address belongs to an unverified account.

    Always returns the same 200. The old 404 / 400 / 200 split told an unauthenticated
    caller whether an email was registered with a company and whether it was verified
    yet — an enumeration oracle, now reachable for any company whose slug can be guessed.
    """
    company_id = _resolve_company_id(db, request, data.api_key, data.company_slug)
    user = _scoped_user_by_email(db, data.email, company_id)
    SENT = {"message": "If that account exists and is unverified, a code has been sent."}
    if not user or user.email_verified:
        return SENT
    otp = set_user_otp(user, db, purpose="verify")
    try:
        from src.services.email_service import send_otp_email, resolve_auth_sender
        smtp_config = resolve_auth_sender(db, user.company_id)
        send_otp_email(user.email, user.first_name, otp, smtp_config=smtp_config)
    except Exception as e:
        # Logged, not returned: whether the mail left the building is not the caller's
        # business, and reporting it re-opens the oracle this function just closed.
        logger.warning(f"resend OTP email to {user.email} failed: {e}")
    return SENT

@router.post("/login")
@limiter.limit("5/minute")
async def login(
    request: Request,
    user_credentials: LoginRequest,
    db: Session = Depends(get_db),
):
    """
    Authenticate user and return access token.

    Rules enforced:
    - When the request supplies a ``company_slug`` (the /<slug> portal) or a legacy
      ``api_key``, login is scoped to that company so the same email at multiple
      companies resolves unambiguously.
    - On a tenant domain (acme.hireflow.io) only that company's users can log in.
    - Superadmin can only log in from the platform domain (no tenant_company_id on request).
    - Suspended company users are rejected with a clear message.
    - Ten wrong passwords in 15 minutes lock the account for 15 minutes
      (src/auth.py), on top of the per-IP limit above.
    - MFA users get an intermediate token instead of a session (see _issue_login).

    Every credential failure is the same generic 401, whether the email is unknown,
    registered at another company, or the password is wrong.
    """
    try:
        scope_company_id = _resolve_company_id(db, request, user_credentials.api_key, user_credentials.company_slug)
        user = login_user(
            db,
            user_credentials.email,
            user_credentials.password,
            company_id=scope_company_id,
        )
        if not user:
            # Deliberately generic. This used to answer "You're not registered with
            # this company" when the email existed at a *different* company, which
            # told anyone which addresses have accounts somewhere on the platform.
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

        return _issue_login(user, db)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )

@router.post("/refresh", response_model=Token)
@limiter.limit("30/minute")
async def refresh_token(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Issue a fresh access token for the currently authenticated user.

    Refused once SESSION_MAX_HOURS have passed since the user actually signed in
    (the `auth_time` claim, carried through every refresh), so a stolen token can
    be kept alive only until then — not forever.
    """
    payload = getattr(request.state, "jwt_payload", {}) or {}
    now = int(time.time())
    # Tokens minted before auth_time existed start their ceiling now.
    auth_time = int(payload.get("auth_time") or now)
    if now - auth_time > SESSION_MAX_HOURS * 3600:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return create_token_response(current_user, db, auth_time=auth_time,
                                 mfa=bool(payload.get("mfa")))

@router.post("/logout")
async def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sign out: revokes every token this user holds, on every device.

    Tokens are stateless JWTs, so revocation works by bumping the user's
    token_version, which every token carries as `tv`. That ends all of the
    user's sessions, not just this one — the safe reading of "sign out" when
    the reason may be a lost laptop.
    """
    revoke_user_sessions(current_user)
    db.commit()
    return {"message": "Successfully logged out"}


# ==================== MULTI-FACTOR AUTH ====================
#
# TOTP (src/mfa.py). Required for the roles in MFA_REQUIRED_ROLES (default
# superadmin, admin): they are sent through enrolment at their next sign-in.
# Optional for everyone else, from the profile page.

_optional_bearer = HTTPBearer(auto_error=False)
_BAD_CODE = HTTPException(status_code=400, detail="That code is not valid. Try again.")


class MfaLoginRequest(BaseModel):
    mfa_token: str
    code: str = Field(..., min_length=6, max_length=32)


class MfaEnrollRequest(BaseModel):
    mfa_token: Optional[str] = None     # the enrolment token from /login, if any


class MfaConfirmRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=10)
    mfa_token: Optional[str] = None


class MfaDisableRequest(BaseModel):
    password: str
    code: str = Field(..., min_length=6, max_length=32)


class MfaCodeRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=32)


def _check_second_factor(db: Session, user: User, code: str) -> bool:
    """A TOTP code (replay-guarded) or an unused recovery code. Commits on success."""
    secret = decrypt(user.mfa_secret or "")
    if secret:
        step = totp.verify_totp(secret, code, last_step=user.mfa_last_step)
        if step is not None:
            user.mfa_last_step = step
            db.commit()
            return True
    ok, remaining = totp.consume_recovery_code(user.mfa_recovery_codes, code)
    if ok:
        user.mfa_recovery_codes = remaining
        db.commit()
        logger.warning("MFA recovery code used by user %s", user.id)
        return True
    return False


def _enrolment_user(request: Request, body_token: Optional[str],
                    creds: Optional[HTTPAuthorizationCredentials], db: Session) -> User:
    """The user enrolling: from the /login enrolment token, or a full session."""
    if body_token:
        return decode_mfa_token(db, body_token, "mfa_enroll")
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return _authenticate_request(request, creds, db)


@router.post("/login/mfa", response_model=Token)
@limiter.limit("10/minute")
async def login_mfa(request: Request, data: MfaLoginRequest, db: Session = Depends(get_db)):
    """Second sign-in step: exchange the /login MFA token and a code for a session.

    Wrong codes count toward the same per-account lockout as wrong passwords.
    """
    user = decode_mfa_token(db, data.mfa_token, "mfa")
    if not user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Your sign-in step expired. Sign in again.")
    if is_locked(user):
        raise locked_exception()
    if not _check_second_factor(db, user, data.code):
        register_login_failure(db, user)
        raise _BAD_CODE
    clear_login_failures(db, user)
    return create_token_response(user, db, mfa=True)


@router.get("/mfa/status")
async def mfa_status(current_user: User = Depends(get_current_user)):
    return {
        "enabled": bool(current_user.mfa_enabled),
        "required": mfa_required_for(current_user),
        "recovery_codes_remaining": totp.recovery_codes_remaining(current_user.mfa_recovery_codes)
        if current_user.mfa_enabled else 0,
    }


@router.post("/mfa/enroll/start")
@limiter.limit("10/minute")
async def mfa_enroll_start(
    request: Request,
    data: MfaEnrollRequest,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: Session = Depends(get_db),
):
    """Begin enrolment: a new secret for the authenticator app. Not active until confirmed."""
    user = _enrolment_user(request, data.mfa_token, creds, db)
    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already on.")
    secret = totp.generate_secret()
    user.mfa_secret = encrypt(secret)
    user.mfa_last_step = None
    db.commit()
    return {
        "secret": secret,
        "otpauth_uri": totp.provisioning_uri(secret, user.email),
    }


@router.post("/mfa/enroll/confirm")
@limiter.limit("10/minute")
async def mfa_enroll_confirm(
    request: Request,
    data: MfaConfirmRequest,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    db: Session = Depends(get_db),
):
    """Finish enrolment with a code from the app. Returns one-time recovery codes and a
    fresh session; every other session this user had is signed out."""
    user = _enrolment_user(request, data.mfa_token, creds, db)
    if user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already on.")
    secret = decrypt(user.mfa_secret or "")
    if not secret:
        raise HTTPException(status_code=400, detail="Start enrolment first.")
    step = totp.verify_totp(secret, data.code)
    if step is None:
        raise _BAD_CODE
    codes, stored = totp.generate_recovery_codes()
    user.mfa_enabled = True
    user.mfa_last_step = step
    user.mfa_recovery_codes = stored
    revoke_user_sessions(user)
    db.commit()
    db.refresh(user)
    token = create_token_response(user, db, mfa=True)
    return {"recovery_codes": codes, **token.model_dump()}


@router.post("/mfa/recovery-codes")
@limiter.limit("5/minute")
async def mfa_regenerate_recovery_codes(
    request: Request,
    data: MfaCodeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Replace the recovery codes (the old ones stop working). Needs a current code."""
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is off.")
    if not _check_second_factor(db, current_user, data.code):
        raise _BAD_CODE
    codes, stored = totp.generate_recovery_codes()
    current_user.mfa_recovery_codes = stored
    db.commit()
    return {"recovery_codes": codes}


@router.post("/mfa/disable")
@limiter.limit("5/minute")
async def mfa_disable(
    request: Request,
    data: MfaDisableRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Turn MFA off (optional roles only). Needs the password and a current code."""
    if mfa_required_for(current_user):
        raise HTTPException(status_code=400,
                            detail="Two-factor authentication is required for your role.")
    if not current_user.mfa_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is off.")
    if not verify_password(data.password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Password is incorrect.")
    if not _check_second_factor(db, current_user, data.code):
        raise _BAD_CODE
    current_user.mfa_enabled = False
    current_user.mfa_secret = None
    current_user.mfa_recovery_codes = None
    current_user.mfa_last_step = None
    revoke_user_sessions(current_user)
    db.commit()
    db.refresh(current_user)
    return create_token_response(current_user, db)

def _iso(value):
    """Serialise a date/datetime, or None. Keeps the JSON shape stable rather
    than leaving it to FastAPI's encoder."""
    return value.isoformat() if value else None


def _role_profile(current_user: User) -> Optional[dict]:
    """
    The per-role profile stub for hr / interviewer, or None.

    Reached through the ``uselist=False`` relationships, never a bare query:
    a superadmin has no tenant bound (``src/auth.py`` calls
    ``set_current_company(None)`` for them), so the tenancy auto-filter is a
    no-op and ``db.query(HRProfile).first()`` would hand them an arbitrary
    profile from an arbitrary company. The relationship's join is on
    ``user_id`` and cannot be forgotten.
    """
    role = (current_user.role or "").lower()
    try:
        if role == "hr":
            profile, kind = current_user.hr_profile, "hr"
        elif role == "interviewer":
            profile, kind = current_user.interviewer_profile, "interviewer"
        else:
            return None
        if not profile:
            return None

        data = {
            "kind": kind,
            "employee_id": profile.employee_id,
            "department_id": profile.department_id,
            "department_name": profile.department.name if profile.department else None,
        }
        if kind == "interviewer":
            data["years_experience"] = profile.years_experience
            data["expertise_areas"] = _parse_expertise(profile.expertise_areas)
        return data
    except Exception as exc:  # a missing FK row must not 500 the profile page
        logger.warning(f"Could not load role profile for user {current_user.id}: {exc}")
        return None


def _parse_expertise(raw) -> list:
    """expertise_areas is a Text column holding JSON; legacy rows hold CSV."""
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else [str(parsed)]
    except (ValueError, TypeError):
        return [part.strip() for part in str(raw).split(",") if part.strip()]


def _serialise_me(db: Session, user: User) -> dict:
    """
    The /auth/me payload. GET and PUT both return this so their shapes can
    never drift apart.

    Built field-by-field on purpose — password_hash, the OTP columns and the
    MFA secret live on the same model and must never be serialised.
    """
    company_name = None
    if user.company_id:
        try:
            # Company is not TenantScoped, so this is a plain lookup and is
            # safe for every role including superadmin.
            company = db.query(Company).filter(Company.id == user.company_id).first()
            company_name = company.name if company else None
        except Exception as exc:
            logger.warning(f"Could not resolve company for user {user.id}: {exc}")

    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone": user.phone,
        "gender": user.gender,
        "date_of_birth": _iso(user.date_of_birth),
        "is_active": user.is_active,
        "email_verified": user.email_verified,
        "created_at": _iso(user.created_at),
        "updated_at": _iso(user.updated_at),
        "company_id": user.company_id,
        "company_name": company_name,
        "mfa_enabled": bool(user.mfa_enabled),
        "profile": _role_profile(user),
    }


class UpdateMeRequest(BaseModel):
    """
    The only fields a user may change about themselves.

    `extra="forbid"` is the privilege-escalation defence, and it is structural
    rather than a blocklist: {"role": "admin"} is rejected with a 422 before any
    handler code runs, and a column added to User later cannot quietly become
    writable without first being added here. Pydantic's default ("ignore")
    would accept-and-drop and return 200, which tells someone probing for
    escalation that the request "worked".

    Deliberately absent: role, email, is_active, company_id, email_verified,
    password, id. Those are administrative decisions or the login identity.
    """
    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    # String(20) in the DB — without this cap a longer value is a MySQL
    # DataError at commit time, i.e. a 500 instead of a 422.
    phone: Optional[str] = Field(None, max_length=20)

    @field_validator("first_name", "last_name")
    @classmethod
    def _non_blank(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("cannot be blank")  # the columns are NOT NULL
        return v

    @field_validator("phone")
    @classmethod
    def _clean_phone(cls, v):
        if v is None:
            return v
        v = v.strip()
        if not v:
            return None  # let the field be cleared
        # Light on purpose: registration validates phone not at all, so a
        # stricter rule would reject numbers users already have on file.
        if not re.fullmatch(r"[0-9+\-() ]+", v):
            raise ValueError("may contain only digits, spaces and + - ( )")
        return v


@router.get("/me")
async def get_current_user_info(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get current authenticated user information."""
    return _serialise_me(db, current_user)


@router.put("/me")
async def update_current_user_info(
    data: UpdateMeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update your own name and phone. Every other field is immutable here —
    see UpdateMeRequest.
    """
    changes = data.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="No changes supplied.")

    for field, value in changes.items():
        setattr(current_user, field, value)

    db.commit()
    db.refresh(current_user)
    return _serialise_me(db, current_user)


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str
    # max_length because bcrypt only hashes the first 72 bytes (src/auth.py
    # _bcrypt_bytes) — better to reject a novel than silently truncate it. The full
    # rules are validate_password_strength; min_length here rejects the obvious as 422.
    new_password: str = Field(..., min_length=8, max_length=128)


@router.post("/change-password")
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Change your own password, proving you know the current one.

    Returns 400 — never 401 — when the current password is wrong. The frontend's
    response interceptor (frontend/src/api/client.js) treats any 401 as a dead
    session: it clears localStorage and hard-redirects to /login. A 401 here
    would sign the user out for a typo and throw away the form.

    No enumeration concern: the identity comes from the JWT, and the body
    carries no email or user id.

    Signs out every other session: the change bumps token_version, which voids
    every token issued before it. The response carries a fresh token for this
    session so the user stays signed in here.
    """
    if not verify_password(data.current_password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")

    # Compare against the hash, not the two plaintexts — the hash is what
    # actually decides whether the password changed.
    if verify_password(data.new_password, current_user.password_hash):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from your current password.",
        )

    validate_password_strength(data.new_password)

    current_user.password_hash = get_password_hash(data.new_password)
    # A reset OTP still sitting on the row must not outlive an authenticated
    # change — otherwise an old emailed code could undo this.
    clear_user_otp(current_user)
    revoke_user_sessions(current_user)
    db.commit()
    db.refresh(current_user)

    payload = getattr(request.state, "jwt_payload", {}) or {}
    token = create_token_response(current_user, db, auth_time=payload.get("auth_time"),
                                  mfa=bool(payload.get("mfa")))
    return {"message": "Password changed successfully.",
            "access_token": token.access_token, "token_type": "bearer"}


@router.get("/my-permissions")
async def get_my_permissions(current_user: User = Depends(get_current_user)):
    """
    What the caller's role is allowed to do, grouped, from the bundled RBAC matrix.

    Descriptive only — the server re-checks every action regardless of what
    this returns. Reachable by all five roles (no role gate), and returns the
    full catalog with a ``granted`` flag on each entry so the UI can show what
    the role *cannot* do as well.
    """
    return get_permissions_for_role(current_user.role)


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
    """Send a password-reset OTP to the user's registered email.

    Both branches return an identical body. The intent was always non-enumeration, but
    the two responses differed in *shape* — the hit carried an ``email`` key the miss did
    not — so the endpoint enumerated users anyway. The SPA renders the address the user
    just typed, so it never needed the echo.
    """
    company_id = _resolve_company_id(db, request, data.api_key, data.company_slug)
    user = _scoped_user_by_email(db, data.email, company_id)
    OTP_SENT = {"message": "If that email is registered, an OTP has been sent."}
    if not user:
        return OTP_SENT
    otp = set_user_otp(user, db, purpose="reset")
    try:
        from src.services.email_service import send_password_reset_otp, resolve_auth_sender
        smtp_config = resolve_auth_sender(db, user.company_id)
        send_password_reset_otp(user.email, user.first_name, otp, smtp_config=smtp_config)
    except Exception as _e:
        logger.warning(f"password-reset OTP email to {user.email} failed: {_e}")
    return OTP_SENT


@router.post("/reset-password")
@limiter.limit("20/minute")
async def reset_password(request: Request, data: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Verify reset OTP and set a new password.

    Every failure returns the same 400, for the same reason as /verify-otp: a 404 for an
    unknown address told an unauthenticated caller which emails are registered with a
    company, and a company is now addressed by a guessable slug.

    The code must have been issued for a reset (a signup verification code does not
    work here), and five wrong guesses void it. A successful reset signs out every
    existing session and lifts any login lockout.
    """
    # Checked first: before the lookup, so a weak password gets the same answer for a
    # known and an unknown email; and before the code is checked, so it doesn't burn it.
    validate_password_strength(data.new_password)
    company_id = _resolve_company_id(db, request, data.api_key, data.company_slug)
    user = _scoped_user_by_email(db, data.email, company_id)
    _INVALID_CODE = HTTPException(status_code=400, detail="Invalid or expired code.")
    if not user:
        raise _INVALID_CODE
    if not check_user_otp(user, db, data.otp, "reset"):
        raise _INVALID_CODE
    user.password_hash = get_password_hash(data.new_password)
    revoke_user_sessions(user)
    user.failed_login_count = 0
    user.last_failed_login_at = None
    user.locked_until = None
    db.commit()
    return {"message": "Password reset successfully. You can now log in."}
