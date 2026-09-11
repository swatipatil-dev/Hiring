"""
🔐 AUTHENTICATION & AUTHORIZATION MODULE
Handles JWT tokens, password hashing, role-based access control
"""

import logging
import os
import random
import string

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import HTTPException, Request, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import bcrypt
from jose import JWTError, jwt
from passlib.context import CryptContext
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
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "1440"))  # 24 hours

# Password Hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse

# ==================== OTP HELPERS ====================

OTP_EXPIRE_MINUTES = 10

def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=6))

def set_user_otp(user: User, db: Session) -> str:
    otp = generate_otp()
    user.otp_code = otp
    user.otp_expires_at = datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES)
    db.commit()
    return otp

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

def verify_token(token: str) -> Optional[str]:
    """Verify JWT token and return user ID"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            return None
        return user_id
    except JWTError:
        return None

# ==================== USER AUTHENTICATION ====================

def authenticate_user(
    db: Session,
    email: str,
    password: str,
    company_id: Optional[str] = None,
) -> Optional[User]:
    """Authenticate user with email and password.

    When ``company_id`` is supplied (e.g. login arrived via /app?key=...), the
    lookup is scoped to that company so the same email used at multiple
    companies resolves unambiguously.
    """
    q = db.query(User).filter(User.email == email)
    if company_id is not None:
        q = q.filter(User.company_id == company_id)
    user = q.first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
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

def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """
    Get current authenticated user.

    Tenant guard: if the request arrives through a company domain
    (request.state.company_id is set by TenantDomainMiddleware), the JWT owner
    must belong to that same company.  Superadmin is exempt — they operate from
    the platform domain where company_id is always None.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Inactive user")

    # Tenant isolation: every authenticated request must belong to the domain's company.
    # Superadmin is excluded — they work from the platform domain (company_id = None on request).
    tenant_company_id = getattr(request.state, "company_id", None)
    if tenant_company_id and user.role != "superadmin":
        if user.company_id != tenant_company_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this company's portal",
            )

    # Bind the authenticated user's company_id to the tenant context.  The
    # SQLAlchemy before_compile hook (src/tenancy/events.py) reads this to
    # auto-filter every query against TenantScoped models — even routes that
    # forget the manual `.filter(Model.company_id == ...)`.
    #
    # Superadmin lands here with company_id = None, so the contextvar stays
    # empty and the auto-filter remains a no-op for them, preserving their
    # intentional cross-tenant access.
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
    otp = set_user_otp(db_user, db)
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
        created_at=user.created_at
    )

def create_token_response(user: User, db: Session = None) -> Token:
    """Create token response with user data and company branding"""
    token_data = {"sub": str(user.id), "role": user.role}

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

    access_token = create_access_token(data=token_data)
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

# ==================== TOKEN VALIDATION ====================

def validate_token_and_get_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Validate token and return user"""
    token = credentials.credentials
    user_id = verify_token(token)
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive"
        )
    
    return user
