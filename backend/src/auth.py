"""
🔐 AUTHENTICATION & AUTHORIZATION MODULE
Handles JWT tokens, password hashing, role-based access control
"""

import hashlib
import hmac
import logging
import os
import secrets
import time

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import HTTPException, Request, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from .config.database_config_mysql import get_db
from .models.models import User, Candidate, HRProfile, InterviewerProfile, Company
from sqlalchemy.orm import Session
from .tenancy import set_current_company

# Security Configuration
#
# SECRET_KEY signs every JWT. A default or well-known value means anyone who has
# read this repository can mint a valid admin token, so it is validated at import
# rather than defaulted: a deploy that forgets to set it must fail loudly at
# startup, not serve traffic with a public signing key. Tests and CI set it
# explicitly (see conftest.py); for local development put a real value in .env.
_PLACEHOLDER_SECRETS = {
    "your-super-secret-key-change-in-production",
    "your-super-secret-key-change-in-production-min-32-chars",
    "your-secret-key-here",
    "changeme",
    "secret",
}

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()

if not SECRET_KEY or SECRET_KEY.lower() in _PLACEHOLDER_SECRETS:
    raise RuntimeError(
        "SECRET_KEY is not set (or is still a placeholder). Refusing to start: "
        "JWTs would be signed with a publicly known key, letting anyone forge an "
        "admin token.\n"
        "Generate one with:\n"
        '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
        "then set it in the environment or in backend/.env as SECRET_KEY=<value>."
    )

if len(SECRET_KEY) < 32:
    logger.warning(
        "SECRET_KEY is only %d characters; 32+ is recommended for HS256.",
        len(SECRET_KEY),
    )

ALGORITHM = "HS256"
# Short-lived access tokens; the SPA refreshes them 10 minutes before expiry.
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
# Absolute ceiling on a session: /refresh refuses once this long has passed since the
# user last actually signed in (the `auth_time` claim), so a stolen token cannot be kept
# alive forever by refreshing it.
SESSION_MAX_HOURS = float(os.getenv("SESSION_MAX_HOURS", "12"))
# Lifetime of the intermediate token between password and MFA code.
MFA_TOKEN_EXPIRE_MINUTES = 5

# JWT Bearer
security = HTTPBearer()

# ==================== PYDANTIC MODELS ====================

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    first_name: str
    last_name: str
    phone: Optional[str]
    is_active: bool
    created_at: datetime
    mfa_enabled: bool = False

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

# ==================== OTP HELPERS ====================
#
# The emailed 6-digit code is a password for as long as it lives, so it is handled
# like one: generated from the OS CSPRNG, stored only as an HMAC, bound to a single
# purpose, and voided after OTP_MAX_ATTEMPTS wrong guesses. A million codes against
# five guesses is a 1-in-200,000 chance per issued code, however many IPs the
# guesser uses — the per-IP rate limit alone never bounded that.

OTP_EXPIRE_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
# Per account, across reissues: without it every /forgot-password call handed out
# five fresh guesses, so a guesser spread over many IPs got thousands an hour.
OTP_MAX_RECENT_FAILURES = 10
OTP_FAILURE_WINDOW = timedelta(hours=1)
OTP_PURPOSES = ("verify", "reset")


def generate_otp() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def _otp_digest(user: User, purpose: str, code: str) -> str:
    """HMAC of the code, keyed by SECRET_KEY and bound to this user and purpose."""
    msg = f"{user.id}:{purpose}:{code}".encode("utf-8")
    return hmac.new(SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def clear_user_otp(user: User) -> None:
    user.otp_code = None
    user.otp_hash = None
    user.otp_purpose = None
    user.otp_expires_at = None
    user.otp_attempts = 0


def set_user_otp(user: User, db: Session, purpose: str = "verify") -> str:
    """Issue a fresh code for `purpose`, replacing any earlier one. Returns the plaintext
    code for the email; only its HMAC is stored."""
    if purpose not in OTP_PURPOSES:
        raise ValueError(f"unknown OTP purpose {purpose!r}")
    otp = generate_otp()
    clear_user_otp(user)
    user.otp_hash = _otp_digest(user, purpose, otp)
    user.otp_purpose = purpose
    user.otp_expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)
    db.commit()
    return otp


def check_user_otp(user: User, db: Session, code: str, purpose: str) -> bool:
    """True if `code` is the live code issued to `user` for `purpose`.

    A wrong guess is counted and committed; the fifth voids the code. A success
    clears it (single use) without committing — the caller commits together with
    whatever the code unlocked. A missing expiry counts as expired.
    """
    if not user.otp_hash or user.otp_purpose != purpose:
        return False
    if not user.otp_expires_at or datetime.utcnow() > user.otp_expires_at:
        return False
    if (user.otp_attempts or 0) >= OTP_MAX_ATTEMPTS:
        return False
    now = datetime.utcnow()
    if user.otp_failures_since and now - user.otp_failures_since > OTP_FAILURE_WINDOW:
        user.otp_recent_failures = 0
        user.otp_failures_since = None
    if (user.otp_recent_failures or 0) >= OTP_MAX_RECENT_FAILURES:
        return False
    supplied = _otp_digest(user, purpose, (code or "").strip())
    if hmac.compare_digest(supplied, user.otp_hash):
        clear_user_otp(user)
        user.otp_recent_failures = 0
        user.otp_failures_since = None
        return True
    user.otp_recent_failures = (user.otp_recent_failures or 0) + 1
    user.otp_failures_since = user.otp_failures_since or now
    user.otp_attempts = (user.otp_attempts or 0) + 1
    if user.otp_attempts >= OTP_MAX_ATTEMPTS:
        clear_user_otp(user)
        user.otp_attempts = OTP_MAX_ATTEMPTS
    db.commit()
    return False

# ==================== AUTHENTICATION FUNCTIONS ====================

def _bcrypt_bytes(password: str) -> bytes:
    # bcrypt only hashes the first 72 bytes — truncate explicitly.
    return password.encode("utf-8")[:72]

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash.

    Uses bcrypt directly to sidestep the passlib + bcrypt 4.x version-detection
    bug. Returns False on unknown hash formats so insecure legacy hashes can't
    be treated as valid.
    """
    try:
        return bcrypt.checkpw(_bcrypt_bytes(plain_password), hashed_password.encode("utf-8"))
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt directly."""
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


# NIST SP 800-63B's floor. Length alone is weak, hence the class mix and the denylist.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128
# bcrypt reads only the first 72 bytes. A longer password would be silently truncated,
# so two passwords sharing those bytes would both work — refuse it instead.
MAX_PASSWORD_BYTES = 72

# The most-guessed passwords that would otherwise pass the length and character-class
# rules. Compared case-insensitively. A floor, not a breach corpus.
_COMMON_PASSWORDS = frozenset({
    "password1", "password!", "password@1", "passw0rd", "p@ssw0rd", "p@ssword",
    "password12", "qwerty123", "qwerty12", "iloveyou1", "admin123", "admin@123",
    "welcome1", "welcome@1", "abc12345", "abcd1234", "letmein1", "changeme1",
    "sunshine1", "princess1", "football1", "monkey123", "dragon123", "trustno1",
    "password1!", "password12", "password123", "password@123", "password#123",
    "p@ssw0rd123", "passw0rd!!", "qwerty1234", "qwerty@123", "qwertyuiop1",
    "1234567890a", "abc1234567", "abcd@12345", "admin@1234", "admin12345",
    "administrator1", "welcome@123", "welcome123", "welcome1234", "iloveyou123",
    "letmein1234", "changeme123", "hireflow123", "hireflow@123", "innomax@123",
    "india@12345", "test@12345", "test123456", "1q2w3e4r5t", "zaq12wsxcde",
})


def validate_password_strength(password: str) -> None:
    """Raise HTTPException(400) if the password is too weak.

    The single rule for every path that sets a password — registration, admin and
    superadmin account creation, bootstrap, the OTP reset and the authenticated
    change — so they can never diverge. At least MIN_PASSWORD_LENGTH characters,
    at least two of lower/upper/digit/symbol, and not on the common list.
    """
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    if len(password) > MAX_PASSWORD_LENGTH or len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at most {MAX_PASSWORD_BYTES} bytes "
                   f"({MAX_PASSWORD_BYTES} plain characters; fewer if it uses accented or other "
                   f"non-English characters)",
        )
    classes = sum((
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ))
    if classes < 2:
        raise HTTPException(
            status_code=400,
            detail="Password must mix at least two of: lowercase, uppercase, digits, symbols",
        )
    if password.lower() in _COMMON_PASSWORDS:
        raise HTTPException(
            status_code=400,
            detail="That password is too common. Choose another.",
        )


# ==================== LOGIN LOCKOUT ====================
#
# Per account, in the database, so it holds across gunicorn workers and survives a
# restart — the per-IP rate limit cannot stop a guesser spread over many addresses.

LOGIN_LOCKOUT_THRESHOLD = int(os.getenv("LOGIN_LOCKOUT_THRESHOLD", "10"))
LOGIN_LOCKOUT_WINDOW = timedelta(minutes=int(os.getenv("LOGIN_LOCKOUT_WINDOW_MINUTES", "15")))
LOGIN_LOCKOUT_DURATION = timedelta(minutes=int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15")))


def locked_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed sign-in attempts. Try again in a few minutes.",
    )


def is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > datetime.utcnow())


def register_login_failure(db: Session, user: User) -> None:
    """Count a wrong password or MFA code; lock the account at the threshold."""
    now = datetime.utcnow()
    if not user.last_failed_login_at or now - user.last_failed_login_at > LOGIN_LOCKOUT_WINDOW:
        user.failed_login_count = 0
    user.failed_login_count = (user.failed_login_count or 0) + 1
    user.last_failed_login_at = now
    if user.failed_login_count >= LOGIN_LOCKOUT_THRESHOLD:
        user.locked_until = now + LOGIN_LOCKOUT_DURATION
        user.failed_login_count = 0
        logger.warning("Account %s locked after repeated failed sign-ins", user.id)
    db.commit()


def clear_login_failures(db: Session, user: User) -> None:
    if user.failed_login_count or user.locked_until or user.last_failed_login_at:
        user.failed_login_count = 0
        user.last_failed_login_at = None
        user.locked_until = None
        db.commit()


# ==================== SESSIONS ====================

def revoke_user_sessions(user: User) -> None:
    """Invalidate every token issued to `user` so far. The caller commits.

    Call on logout, password change or reset, deactivation, role change and company
    suspension. Tokens carry the version as `tv`; _authenticate_request rejects a
    mismatch.
    """
    user.token_version = (user.token_version or 0) + 1


def mfa_required_roles() -> set:
    """Roles that must use MFA. MFA_REQUIRED_ROLES="" turns enforcement off (tests)."""
    raw = os.getenv("MFA_REQUIRED_ROLES", "superadmin,admin")
    return {r.strip().lower() for r in raw.split(",") if r.strip()}


def mfa_required_for(user: User) -> bool:
    return (user.role or "").lower() in mfa_required_roles()

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_mfa_token(user: User, kind: str) -> str:
    """A short-lived token that proves the password was right and nothing more.

    kind="mfa" is accepted only by /auth/login/mfa; kind="mfa_enroll" only by the
    enrolment endpoints. _authenticate_request rejects both, so neither opens any
    other route.
    """
    return create_access_token(
        {"sub": str(user.id), "type": kind, "tv": user.token_version or 0},
        expires_delta=timedelta(minutes=MFA_TOKEN_EXPIRE_MINUTES),
    )


def decode_mfa_token(db: Session, token: str, kind: str) -> User:
    """Resolve an MFA-step token of `kind` to its active user, or raise 400.

    400 rather than 401: the SPA treats any 401 as a dead session and hard-redirects
    to /login, which would throw away the code the user just typed.
    """
    expired = HTTPException(status_code=400, detail="Your sign-in step expired. Sign in again.")
    try:
        payload = jwt.decode(token or "", SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise expired
    if payload.get("type") != kind:
        raise expired
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if not user or not user.is_active or payload.get("tv", 0) != (user.token_version or 0):
        raise expired
    return user

# ==================== USER AUTHENTICATION ====================

_DUMMY_HASH: Optional[str] = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = get_password_hash(secrets.token_urlsafe(16))
    return _DUMMY_HASH


def authenticate_user(
    db: Session,
    email: str,
    password: str,
    company_id: Optional[str] = None,
) -> Optional[User]:
    """Authenticate user with email and password.

    When ``company_id`` is supplied (e.g. login arrived via /<company-slug>), the
    lookup is scoped to that company so the same email used at multiple
    companies resolves unambiguously.
    """
    q = db.query(User).filter(User.email == email)
    if company_id is not None:
        q = q.filter(User.company_id == company_id)
    user = q.first()
    if not user:
        # Spend the same bcrypt time as a real check, so response timing does not
        # reveal which emails have accounts.
        verify_password(password, _dummy_hash())
        return None
    if is_locked(user):
        # The same answer as a wrong password: a distinct "locked" reply would tell
        # anyone who tries eleven passwords whether the email has an account.
        verify_password(password, _dummy_hash())
        return None
    if not verify_password(password, user.password_hash):
        register_login_failure(db, user)
        return None
    # With MFA the password is only half the sign-in: failures are cleared after the
    # code (routes/auth_routes.login_mfa), or a phished password could reset the
    # counter between every batch of guessed codes.
    if not user.mfa_enabled:
        clear_login_failures(db, user)
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user"
        )
    if not user.email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="EMAIL_NOT_VERIFIED"
        )
    return user

def resolve_token_user(db: Session, token: str):
    """Check an access token and return ``(user, payload)``; raise 401/400 otherwise.

    Every check a session token must pass — not an MFA-step token, not revoked, MFA
    satisfied where required, user active, company not suspended. Routes that read a
    token themselves (WebSockets, shared-secret-or-token endpoints) must use this
    rather than a bare jwt.decode, which skips all of it.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(token or "", SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # MFA-step tokens only prove the password; they open nothing but the MFA routes.
    if payload.get("type") not in (None, "access"):
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    # Revocation: logout, a password change or reset, deactivation and company
    # suspension bump token_version, which kills every token minted before it.
    if payload.get("tv", 0) != (user.token_version or 0):
        raise credentials_exception

    # A token minted before MFA was required (or before this user enrolled) must not
    # outlive that requirement — otherwise a stolen pre-MFA token skips it.
    if (mfa_required_for(user) or user.mfa_enabled) and not payload.get("mfa"):
        raise credentials_exception

    if user.company_id:
        company = db.query(Company).filter(Company.id == user.company_id).first()
        if company is not None and not company.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Your company account has been suspended. Contact support@hireflow.io",
                headers={"WWW-Authenticate": "Bearer"},
            )
    return user, payload


def _authenticate_request(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve the JWT to an active user. Use get_current_user, not this.

    Tenant guard: if the request arrives through a company domain
    (request.state.company_id is set by TenantDomainMiddleware), the JWT owner
    must belong to that same company.  Superadmin is exempt — they operate from
    the platform domain where company_id is always None.
    """
    user, payload = resolve_token_user(db, credentials.credentials)
    request.state.jwt_payload = payload

    # Tenant isolation: every authenticated request must belong to the domain's company.
    # Superadmin is excluded — they work from the platform domain (company_id = None on request).
    tenant_company_id = getattr(request.state, "company_id", None)
    if tenant_company_id and user.role != "superadmin":
        if user.company_id != tenant_company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this company's portal",
            )

    return user


async def get_current_user(user: User = Depends(_authenticate_request)) -> User:
    """
    Get the current authenticated user and bind their company as the tenant.

    The SQLAlchemy tenant filter (src/tenancy/events.py) reads the bound
    company to scope every query against TenantScoped models, including routes
    that forget the manual `.filter(Model.company_id == ...)`.

    This must stay `async def`. FastAPI runs a sync dependency in a threadpool
    on a *copy* of the request context, so a contextvar set there is thrown
    away before the endpoint runs — which is how the filter sat dormant on
    every request until test_tenant_binding_reaches_endpoint caught it. The
    database work stays in the sync _authenticate_request, off the event loop.

    Superadmin binds None, so the filter stays off for their intentional
    cross-tenant access.
    """
    set_current_company(user.company_id if user.role != "superadmin" else None)
    return user

# ==================== ROLE-BASED ACCESS CONTROL ====================

def require_role(allowed_roles: List[str]):
    """Decorator to require specific user roles"""
    def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return role_checker

# ==================== USER MANAGEMENT ====================

def create_user(db: Session, user_data: UserCreate, company_id: Optional[str] = None) -> User:
    """Create a new user. company_id must be supplied server-side — never trust the client.

    Uniqueness is enforced per-company: the same email may register under
    different companies (one candidate, many client portals), but registering
    twice with the same email under the same company is rejected.

    Invariant: a user is never created with a NULL company_id. If no company is
    supplied (e.g. a bare HireFlow self-signup with no tenant context), the user
    defaults to the platform's own org (Innomax). This keeps platform users out
    of the NULL-company bucket that the tenant auto-filter would otherwise hide.
    """
    validate_password_strength(user_data.password)

    if company_id is None:
        from src.tenancy.platform_company import ensure_platform_company
        company_id = ensure_platform_company(db).id

    existing_q = db.query(User).filter(User.email == user_data.email)
    if company_id is None:
        existing_q = existing_q.filter(User.company_id.is_(None))
    else:
        existing_q = existing_q.filter(User.company_id == company_id)
    if existing_q.first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # superadmin is DB-seeded only — never created through the API
    valid_roles = ["admin", "hr", "interviewer", "candidate"]
    if user_data.role not in valid_roles:
        raise HTTPException(status_code=400, detail="Invalid role")

    # Create new user
    hashed_password = get_password_hash(user_data.password)
    db_user = User(
        email=user_data.email,
        password_hash=hashed_password,
        role=user_data.role,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        phone=user_data.phone,
        gender=user_data.gender,
        date_of_birth=datetime.strptime(user_data.date_of_birth, "%Y-%m-%d").date() if user_data.date_of_birth else None,
        company_id=company_id,
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)

    # Generate and send OTP using the company's SMTP if configured.
    otp = set_user_otp(db_user, db, purpose="verify")
    try:
        from .services.email_service import send_otp_email, resolve_auth_sender
        smtp_config = resolve_auth_sender(db, db_user.company_id)
        result = send_otp_email(db_user.email, db_user.first_name, otp, smtp_config=smtp_config)
        if isinstance(result, dict) and not result.get("sent"):
            # Surface the underlying SMTP error so ops can see why the user
            # didn't receive their code. OTP stays in DB; /resend-otp retries.
            logger.error(
                f"OTP email send failed for {db_user.email}: "
                f"{result.get('error_type')} — {result.get('error')}"
            )
    except Exception as e:
        logger.exception(f"OTP email dispatch crashed for {db_user.email}: {e}")

    # If role is candidate, also create a record in the candidates table
    if db_user.role == "candidate":
        db_candidate = Candidate(
            company_id=company_id,
            user_id=str(db_user.id),
            email=db_user.email,
            first_name=db_user.first_name,
            last_name=db_user.last_name,
            phone=db_user.phone,
            current_stage="registered"
        )
        db.add(db_candidate)
        db.commit()

        # Put them in the HR talent pool straight away, so HR can browse everyone who
        # has ever signed up without waiting for an import. Unscored and unranked here
        # (there may be no open roles yet, and scoring is expensive) — the pool's
        # Re-match and import fill that in. Never allowed to fail a registration.
        try:
            from src.services.resume_pool_import import import_registered_users
            if company_id:
                import_registered_users(
                    db,
                    company_id=company_id,
                    actor_user_id=None,
                    limit=1,
                    refresh_existing=True,
                    only_user_id=str(db_user.id),
                    prepared_jobs=[],   # explicit: None would load every active job
                    score=False,
                )
        except Exception as e:
            logger.warning(f"Talent pool sync skipped for new user {db_user.email}: {e}")

    return db_user

def login_user(db: Session, email: str, password: str, company_id: Optional[str] = None) -> Optional[User]:
    """Login user and return user object. company_id scopes the lookup when set."""
    return authenticate_user(db, email, password, company_id=company_id)

def create_user_response(user: User) -> UserResponse:
    """Create user response object"""
    return UserResponse(
        id=str(user.id),
        email=user.email,
        role=user.role,
        first_name=user.first_name,
        last_name=user.last_name,
        phone=user.phone,
        is_active=user.is_active,
        created_at=user.created_at,
        mfa_enabled=bool(getattr(user, "mfa_enabled", False)),
    )

def create_token_response(user: User, db: Session = None, *,
                          auth_time: Optional[int] = None, mfa: bool = False) -> Token:
    """Create token response with user data and company branding.

    `auth_time` is when the user last actually signed in (epoch seconds); a refresh
    passes the original through so SESSION_MAX_HOURS is measured from sign-in, not
    from the last refresh. `mfa` records that a second factor was verified.
    """
    now = int(time.time())
    auth_time = int(auth_time or now)
    token_data = {
        "sub": str(user.id),
        "role": user.role,
        "type": "access",
        "tv": user.token_version or 0,
        "auth_time": auth_time,
    }
    if mfa:
        token_data["mfa"] = True

    company_id = user.company_id

    # Candidates self-register without a company_id; resolve branding from their
    # most recent application so the sidebar shows the company they applied to.
    if not company_id and user.role == "candidate" and db:
        latest = (
            db.query(Candidate)
            .filter(Candidate.user_id == str(user.id), Candidate.company_id.isnot(None))
            .order_by(Candidate.created_at.desc())
            .first()
        )
        if latest:
            company_id = latest.company_id

    if company_id:
        token_data["company_id"] = company_id
        if db:
            company = db.query(Company).filter(Company.id == company_id).first()
            if company:
                token_data["company_logo"] = company.logo_url or ""
                token_data["company_favicon"] = company.favicon_url or ""
                token_data["company_color"] = company.primary_color or "#2b7cba"
                token_data["company_secondary_color"] = company.secondary_color or ""
                token_data["company_accent_color"] = company.accent_color or ""
                token_data["company_font"] = company.font_family or ""
                token_data["company_name"] = company.name or ""
                token_data["company_slug"] = company.slug or ""
                # Decided here, from the env-driven platform slug, so the frontend never
                # has to recognise the platform org by a hardcoded id or slug. Its id is
                # a random uuid per database: a copied dev id matched nothing in
                # production, so platform users were logged out to the platform's
                # careers page as if they were a client company's staff.
                from .tenancy.platform_company import platform_slug
                token_data["is_platform"] = (
                    (company.slug or "").strip().lower() == platform_slug().strip().lower())

    # Never outlive the session ceiling, even by a single token lifetime.
    lifetime = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    remaining = timedelta(seconds=auth_time + SESSION_MAX_HOURS * 3600 - now)
    access_token = create_access_token(data=token_data, expires_delta=min(lifetime, remaining))
    user_response = create_user_response(user)
    return Token(access_token=access_token, token_type="bearer", user=user_response)

# ==================== AUTHENTICATION MIDDLEWARE ====================

class AuthMiddleware:
    """Authentication middleware for protecting routes"""
    
    @staticmethod
    def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
        """Get current admin user — client company admins only, superadmin excluded"""
        if current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        return current_user
    
    @staticmethod
    def get_current_hr(current_user: User = Depends(get_current_user)) -> User:
        """Get current HR user"""
        if current_user.role not in ["hr", "admin"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="HR access required"
            )
        return current_user
    
    @staticmethod
    def get_current_interviewer(current_user: User = Depends(get_current_user)) -> User:
        """Get current interviewer user"""
        if current_user.role != "interviewer":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Interviewer access required"
            )
        return current_user
    
    @staticmethod
    def get_current_candidate(current_user: User = Depends(get_current_user)) -> User:
        """Get current candidate user"""
        if current_user.role != "candidate":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Candidate access required"
            )
        return current_user

# ==================== AUTHENTICATION HELPERS ====================

def is_authorized(user: User, required_roles: List[str]) -> bool:
    """Check if user is authorized for given roles"""
    return user.role in required_roles

def can_access_resource(user: User, resource_owner_id: str) -> bool:
    """Check if user can access a specific resource"""
    # Admins can access everything
    if user.role == "admin":
        return True
    
    # Users can access their own resources
    return str(user.id) == resource_owner_id

def get_user_permissions(user: User) -> List[str]:
    """Get list of user permissions based on role"""
    permissions = {
        "admin": [
            "create_users", "read_users", "update_users", "delete_users",
            "create_departments", "read_departments", "update_departments",
            "read_analytics", "system_config"
        ],
        "hr": [
            "create_jobs", "read_jobs", "update_jobs",
            "read_candidates", "update_candidates", "move_candidates",
            "create_interviews", "read_interviews", "update_interviews",
            "create_offers", "read_offers", "update_offers"
        ],
        "interviewer": [
            "read_interviews", "create_feedback", "read_feedback"
        ],
        "candidate": [
            "read_jobs", "create_applications", "read_applications",
            "read_offers", "update_offers"
        ]
    }
    
    return permissions.get(user.role, [])
