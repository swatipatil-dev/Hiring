"""
🛡️ ADMIN API ROUTES
Handles user management, department creation, system configuration, and analytics
"""

from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, case, extract, or_
from datetime import date
from typing import List, Optional
import json
import logging
from src.config.database_config_mysql import get_db
from src.auth import get_current_user, AuthMiddleware, create_user, create_user_response, require_role, get_password_hash
from src.models.models import (
    User, Department, HRProfile, InterviewerProfile, JobPosting,
    Candidate, ATSResult, Interview, InterviewFeedback, Offer, SystemConfig,
    TenantConfig, CompanyMailbox, PipelineStepConfig
)
from src.security import encrypt, decrypt
from src.services.interviewer_deactivation import deactivate_interviewer_assignments
from pydantic import BaseModel, EmailStr, field_validator
from datetime import datetime, timedelta
import uuid

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["Admin"])

# ==================== PYDANTIC MODELS ====================

class HRUserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    department_id: Optional[str] = None
    employee_id: Optional[str] = None

class InterviewerUserCreate(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    department_id: Optional[str] = None
    employee_id: Optional[str] = None
    expertise_areas: Optional[List[str]] = None
    years_experience: Optional[int] = None

class UserUpdate(BaseModel):
    # All optional — only supplied fields are changed. A blank/omitted password
    # keeps the current one; a non-empty password resets it.
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None
    department_id: Optional[str] = None
    employee_id: Optional[str] = None
    expertise_areas: Optional[List[str]] = None
    years_experience: Optional[int] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _name_not_blank(cls, v):
        """A name may be left out of the payload, but never emptied.

        first_name is NOT NULL on the model, but "" satisfies that in MySQL, so an edit
        form that posted an empty field permanently erased the person's name. Whitespace
        is stripped first so "   " is not a loophole.
        """
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank")
        return v

class DepartmentCreate(BaseModel):
    name: str
    description: Optional[str] = None

class JobPostingCreate(BaseModel):
    title: str
    description: str
    requirements: str
    department_id: str
    location: str
    employment_type: str
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    experience_years: int
    required_skills: List[str]

class JobPostingResponse(BaseModel):
    id: str
    title: str
    description: str
    requirements: str
    department: str
    location: str
    employment_type: str
    salary_min: Optional[float]
    salary_max: Optional[float]
    experience_years: int
    required_skills: List[str]
    status: str
    created_at: datetime

class UserResponse(BaseModel):
    id: str
    email: str
    role: str
    first_name: str
    last_name: str
    phone: Optional[str]
    is_active: bool
    created_at: datetime

class UserListResponse(BaseModel):
    """Paged envelope for the admin user list.

    `total` is the count *before* skip/limit, so the UI can render "showing
    X–Y of Z" and page without guessing whether another page exists.
    """
    users: List[UserResponse]
    total: int


class RoleCounts(BaseModel):
    all: int
    admin: int
    hr: int
    interviewer: int
    candidate: int


class DepartmentResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    logo_url: Optional[str] = None
    created_at: datetime

class SystemAnalytics(BaseModel):
    total_users: int
    total_jobs: int
    active_jobs: int
    # The overview card for departments used to be departments.length over
    # GET /departments, which caps at limit=50. Counted here instead so the card
    # describes the company rather than the first page of it.
    total_departments: int
    total_candidates: int
    candidates_by_stage: List[dict]
    recent_applications: List[dict]

class JobAnalytics(BaseModel):
    job_id: str
    job_title: str
    total_applications: int
    average_ats_score: Optional[float]
    stage_distribution: List[dict]
    applications_by_date: List[dict]

# ==================== USER MANAGEMENT ENDPOINTS ====================

@router.post("/users/hr", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_hr_user(
    hr_data: HRUserCreate,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new HR user"""
    # Resolve department — use provided or fall back to first company department
    dept_id = hr_data.department_id
    if dept_id:
        department = db.query(Department).filter(
            Department.id == dept_id,
            Department.company_id == current_user.company_id
        ).first()
        if not department:
            raise HTTPException(status_code=404, detail="Department not found")
    else:
        department = db.query(Department).filter(
            Department.company_id == current_user.company_id
        ).first()
        dept_id = str(department.id) if department else None

    # Auto-generate employee_id if not provided
    employee_id = hr_data.employee_id or f"HR-{uuid.uuid4().hex[:8]}"
    if db.query(HRProfile).filter(HRProfile.employee_id == employee_id).first():
        employee_id = f"HR-{uuid.uuid4().hex[:8]}"

    # Create user
    from src.auth import UserCreate
    user_create = UserCreate(
        email=hr_data.email,
        password=hr_data.password,
        role="hr",
        first_name=hr_data.first_name,
        last_name=hr_data.last_name,
        phone=hr_data.phone
    )

    # Pass the admin's company_id so the user is created in the admin's company
    # and the per-company email-uniqueness check is scoped correctly (avoids
    # orphan NULL-company rows from the old post-hoc assignment).
    user = create_user(db, user_create, company_id=current_user.company_id)
    user.email_verified = True

    # Create HR profile (tenant-scoped to the admin's company)
    hr_profile = HRProfile(
        user_id=user.id,
        company_id=current_user.company_id,
        department_id=dept_id,
        employee_id=employee_id
    )
    
    db.add(hr_profile)
    db.commit()
    
    return create_user_response(user)

@router.post("/users/interviewer", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_interviewer_user(
    interviewer_data: InterviewerUserCreate,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new interviewer user"""
    # Resolve department — use provided or fall back to first company department
    dept_id = interviewer_data.department_id
    if dept_id:
        department = db.query(Department).filter(
            Department.id == dept_id,
            Department.company_id == current_user.company_id
        ).first()
        if not department:
            raise HTTPException(status_code=404, detail="Department not found")
    else:
        department = db.query(Department).filter(
            Department.company_id == current_user.company_id
        ).first()
        dept_id = str(department.id) if department else None

    # Auto-generate employee_id if not provided
    employee_id = interviewer_data.employee_id or f"IV-{uuid.uuid4().hex[:8]}"
    if db.query(InterviewerProfile).filter(InterviewerProfile.employee_id == employee_id).first():
        employee_id = f"IV-{uuid.uuid4().hex[:8]}"

    # Create user
    from src.auth import UserCreate
    user_create = UserCreate(
        email=interviewer_data.email,
        password=interviewer_data.password,
        role="interviewer",
        first_name=interviewer_data.first_name,
        last_name=interviewer_data.last_name,
        phone=interviewer_data.phone
    )

    # Pass the admin's company_id so the user is created in the admin's company
    # and the per-company email-uniqueness check is scoped correctly (avoids
    # orphan NULL-company rows from the old post-hoc assignment).
    user = create_user(db, user_create, company_id=current_user.company_id)
    user.email_verified = True

    # Create interviewer profile (tenant-scoped to the admin's company)
    interviewer_profile = InterviewerProfile(
        user_id=user.id,
        company_id=current_user.company_id,
        department_id=dept_id,
        employee_id=employee_id,
        expertise_areas=json.dumps(interviewer_data.expertise_areas) if interviewer_data.expertise_areas else None,
        years_experience=interviewer_data.years_experience
    )
    
    db.add(interviewer_profile)
    db.commit()
    
    return create_user_response(user)

USER_ROLES = ["admin", "hr", "interviewer", "candidate"]


def _visible_users_query(db: Session, company_id: Optional[str]):
    """Base query for users this admin may see.

    Everyone in their company, *plus* candidates who applied to it from another
    portal: a bare HireFlow sign-up lands in the platform org (see
    `src/auth.py` create_user and `routes/auth_routes.py` register), so their
    `users.company_id` is Innomax even though the application row they later
    created is ours. Matching on `candidates.company_id` as well is what makes
    those applicants visible under the Candidate category.

    Tenancy note: this is a deliberate cross-company read, so it opts out of
    the tenant auto-filter (`src/tenancy/events.py`), which would otherwise add
    `AND users.company_id = me` and silently drop the outside applicants. With
    the filter off, the explicit or_ below is the ONLY scoping — both arms must
    stay: company members, or users with an application row in this company.
    """
    query = (
        db.query(User)
          .execution_options(_tenant_bypass=True)
          .filter(User.role != "superadmin")
    )
    if not company_id:
        # Never unscoped for a tenant user; only a company-less caller lands here.
        return query.filter(False)

    applicant_ids = (
        db.query(Candidate.user_id)
          .filter(Candidate.company_id == company_id, Candidate.user_id.isnot(None))
    )
    return query.filter(
        or_(User.company_id == company_id, User.id.in_(applicant_ids))
    )


@router.get("/users/counts", response_model=RoleCounts)
async def get_user_counts(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Per-role user counts for the admin Users tab bar.

    Scoped identically to GET /users so a tab's badge always matches the number
    of rows that tab actually lists.
    """
    base = _visible_users_query(db, current_user.company_id)
    rows = base.with_entities(User.role, func.count(User.id)).group_by(User.role).all()

    counts = {role: 0 for role in USER_ROLES}
    for role, count in rows:
        if role in counts:
            counts[role] = count
    counts["all"] = sum(counts[role] for role in USER_ROLES)
    return RoleCounts(**counts)


@router.get("/users", response_model=UserListResponse)
async def get_users(
    skip: int = 0,
    limit: int = 50,
    role: Optional[str] = None,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get users with optional role / status / text filtering, paged.

    Search runs server-side so it spans every page, not just the rows the client
    happens to have loaded.
    """
    query = _visible_users_query(db, current_user.company_id)

    if role:
        query = query.filter(User.role == role)

    if is_active is not None:
        query = query.filter(User.is_active == is_active)

    if search and search.strip():
        term = f"%{search.strip()}%"
        query = query.filter(or_(
            User.first_name.ilike(term),
            User.last_name.ilike(term),
            User.email.ilike(term),
        ))

    total = query.count()
    # `id` is the tiebreaker, not decoration: created_at is a second-resolution
    # DATETIME, so users created in the same second sort arbitrarily and
    # skip/limit would drop or repeat rows between pages.
    users = (
        query.order_by(User.created_at.desc(), User.id.desc())
             .offset(skip).limit(limit).all()
    )

    # Returned as a dict so FastAPI coerces each row against UserResponse.
    # create_user_response() builds src.auth.UserResponse — a distinct class with
    # the same fields — which pydantic would reject if constructed directly here.
    return {
        "users": [create_user_response(user) for user in users],
        "total": total,
    }

@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    is_active: bool,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Update user active status"""
    user = db.query(User).filter(
        User.id == user_id,
        User.company_id == current_user.company_id
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot change your own status")

    was_active = bool(user.is_active)
    user.is_active = is_active
    user.updated_at = datetime.utcnow()
    if was_active and not is_active:
        from src.auth import revoke_user_sessions
        revoke_user_sessions(user)   # reactivation must not revive old tokens

    # Deactivation must detach the user from active work, not just flip the flag.
    # src/auth.py refuses their login from here on, so anything still pointing at them
    # (job defaults, upcoming panels) would route work to someone who cannot act on it.
    summary = None
    if was_active and not is_active:
        summary = deactivate_interviewer_assignments(db, user, current_user.id)

    db.commit()

    response = {"message": f"User {'activated' if is_active else 'deactivated'} successfully"}
    if summary:
        response.update(summary)
    return response

@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Edit an HR/Interviewer user's details and optionally reset their password.

    Tenant-scoped: an admin may only edit users in their own company. A blank/omitted
    password keeps the current one; a non-empty password resets it (same hasher as
    account creation). Role is immutable here.
    """
    user = db.query(User).filter(
        User.id == user_id,
        User.company_id == current_user.company_id,
        User.role.in_(["hr", "interviewer"])
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # ── Identity fields ──
    if payload.first_name is not None:
        user.first_name = payload.first_name
    if payload.last_name is not None:
        user.last_name = payload.last_name
    if payload.phone is not None:
        user.phone = payload.phone
    if payload.email is not None and payload.email != user.email:
        clash = db.query(User).filter(
            User.email == payload.email,
            User.company_id == current_user.company_id,
            User.id != user.id
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail="Another user already uses that email")
        user.email = payload.email

    # ── Password reset (only when a non-empty password is supplied) ──
    if payload.password:
        from src.auth import validate_password_strength, revoke_user_sessions
        validate_password_strength(payload.password)
        user.password_hash = get_password_hash(payload.password)
        revoke_user_sessions(user)   # an admin reset signs the user out everywhere

    # ── Department (company-scoped) ──
    dept_id = None
    if payload.department_id:
        dept = db.query(Department).filter(
            Department.id == payload.department_id,
            Department.company_id == current_user.company_id
        ).first()
        if not dept:
            raise HTTPException(status_code=404, detail="Department not found")
        dept_id = str(dept.id)

    # ── Linked profile ──
    if user.role == "hr":
        profile = db.query(HRProfile).filter(HRProfile.user_id == user.id).first()
        if profile:
            if dept_id is not None:
                profile.department_id = dept_id
            if payload.employee_id:
                profile.employee_id = payload.employee_id
    else:  # interviewer
        profile = db.query(InterviewerProfile).filter(InterviewerProfile.user_id == user.id).first()
        if profile:
            if dept_id is not None:
                profile.department_id = dept_id
            if payload.employee_id:
                profile.employee_id = payload.employee_id
            if payload.expertise_areas is not None:
                profile.expertise_areas = json.dumps(payload.expertise_areas)
            if payload.years_experience is not None:
                profile.years_experience = payload.years_experience

    user.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    return create_user_response(user)

# ==================== DEPARTMENT CHATBOT ENDPOINT ====================

class ChatRequest(BaseModel):
    message: str

@router.post("/department-chat")
async def department_chat(
    req: ChatRequest,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Department chatbot — answers questions about departments, users, jobs, and candidates
    using live database data. No external AI needed.
    """
    msg = req.message.lower().strip()

    # Fetch all departments for this company
    departments = db.query(Department).filter(
        Department.company_id == current_user.company_id
    ).order_by(Department.name).all()
    dept_names = [d.name for d in departments]
    dept_map = {d.name.lower(): d for d in departments}

    # Try to find which department the user is asking about
    target_dept = None
    for name, dept in dept_map.items():
        if name in msg:
            target_dept = dept
            break

    # ---- Handlers ----

    # List all departments
    if any(kw in msg for kw in ['list department', 'all department', 'show department', 'how many department', 'departments list']):
        lines = [f"We have **{len(departments)}** department(s):\n"]
        for d in departments:
            emp_count = db.query(func.count(User.id)).join(
                HRProfile, HRProfile.user_id == User.id, isouter=True
            ).join(
                InterviewerProfile, InterviewerProfile.user_id == User.id, isouter=True
            ).filter(
                (HRProfile.department_id == str(d.id)) | (InterviewerProfile.department_id == str(d.id))
            ).scalar() or 0
            job_count = db.query(func.count(JobPosting.id)).filter(JobPosting.department_id == str(d.id)).scalar() or 0
            lines.append(f"- **{d.name}**: {emp_count} employee(s), {job_count} open job(s)")
        return {"reply": "\n".join(lines)}

    # Department detail
    if target_dept:
        d = target_dept
        dept_id = str(d.id)

        # Employees in this department
        hr_users = db.query(User).join(HRProfile).filter(
            HRProfile.department_id == dept_id, User.is_active == True  # noqa: E712
        ).all()
        interviewer_users = db.query(User).join(InterviewerProfile).filter(
            InterviewerProfile.department_id == dept_id, User.is_active == True  # noqa: E712
        ).all()
        all_employees = hr_users + interviewer_users
        emp_count = len(all_employees)

        # Jobs
        jobs = db.query(JobPosting).filter(JobPosting.department_id == dept_id).all()
        active_jobs = [j for j in jobs if j.status == 'open']

        # Candidates across jobs
        candidate_count = db.query(func.count(Candidate.id)).filter(
            Candidate.job_id.in_([str(j.id) for j in jobs])
        ).scalar() or 0 if jobs else 0

        # Interviews
        interview_count = db.query(func.count(Interview.id)).filter(
            Interview.job_id.in_([str(j.id) for j in jobs])
        ).scalar() or 0 if jobs else 0

        # Offers
        offer_count = db.query(func.count(Offer.id)).filter(
            Offer.job_id.in_([str(j.id) for j in jobs])
        ).scalar() or 0 if jobs else 0

        if any(kw in msg for kw in ['employee', 'staff', 'member', 'people', 'who work', 'team']):
            if all_employees:
                lines = [f"**{d.name}** has **{emp_count}** employee(s):\n"]
                for u in all_employees:
                    lines.append(f"- {u.first_name} {u.last_name} ({u.email}) — {u.role}")
                return {"reply": "\n".join(lines)}
            return {"reply": f"**{d.name}** currently has no employees assigned."}

        if any(kw in msg for kw in ['job', 'position', 'opening', 'posting', 'vacancy', 'hiring']):
            if jobs:
                lines = [f"**{d.name}** has **{len(jobs)}** job(s) ({len(active_jobs)} open):\n"]
                for j in jobs:
                    lines.append(f"- **{j.title}** — {j.status} | {j.location or 'N/A'} | {j.employment_type or 'N/A'}")
                return {"reply": "\n".join(lines)}
            return {"reply": f"**{d.name}** has no job postings yet."}

        if any(kw in msg for kw in ['candidate', 'applicant', 'application']):
            return {"reply": f"**{d.name}** has **{candidate_count}** candidate(s) across {len(jobs)} job(s)."}

        if any(kw in msg for kw in ['interview']):
            return {"reply": f"**{d.name}** has **{interview_count}** interview(s) scheduled across its job postings."}

        if any(kw in msg for kw in ['offer']):
            return {"reply": f"**{d.name}** has **{offer_count}** offer(s) made across its job postings."}

        # General summary for department
        lines = [
            f"**{d.name}** Department Summary:\n",
            f"- Description: {d.description or 'N/A'}",
            f"- Employees: {emp_count}",
            f"- Total Jobs: {len(jobs)} ({len(active_jobs)} open)",
            f"- Candidates: {candidate_count}",
            f"- Interviews: {interview_count}",
            f"- Offers: {offer_count}",
        ]
        return {"reply": "\n".join(lines)}

    # Total stats
    if any(kw in msg for kw in ['total', 'overview', 'summary', 'stats', 'statistic', 'count']):
        # This company's totals — these counted every tenant on the platform.
        cid = current_user.company_id
        total_users = db.query(func.count(User.id)).filter(User.company_id == cid).scalar() or 0
        total_jobs = db.query(func.count(JobPosting.id)).filter(JobPosting.company_id == cid).scalar() or 0
        total_candidates = db.query(func.count(Candidate.id)).filter(Candidate.company_id == cid).scalar() or 0
        total_interviews = db.query(func.count(Interview.id)).filter(Interview.company_id == cid).scalar() or 0
        total_offers = db.query(func.count(Offer.id)).filter(Offer.company_id == cid).scalar() or 0
        return {"reply": (
            f"**System Overview:**\n\n"
            f"- Departments: {len(departments)}\n"
            f"- Users: {total_users}\n"
            f"- Job Postings: {total_jobs}\n"
            f"- Candidates: {total_candidates}\n"
            f"- Interviews: {total_interviews}\n"
            f"- Offers: {total_offers}"
        )}

    # Help / fallback
    if any(kw in msg for kw in ['help', 'what can you', 'how to', 'what do you']):
        return {"reply": (
            "I can help you with department information! Try asking:\n\n"
            "- **List all departments**\n"
            "- **Tell me about Engineering** (or any department name)\n"
            "- **Who works in Sales?**\n"
            "- **Jobs in Marketing**\n"
            "- **Candidates in Engineering**\n"
            "- **Show system overview**\n"
            "- **How many interviews in HR?**"
        )}

    # Greeting
    if any(kw in msg for kw in ['hi', 'hello', 'hey', 'good morning', 'good afternoon']):
        return {"reply": f"Hello! I'm the Department Assistant. I can help you explore department data. Available departments: **{', '.join(dept_names) or 'None yet'}**. Ask me anything!"}

    return {"reply": (
        f"I'm not sure I understand. Try asking about a specific department like **{dept_names[0] if dept_names else 'Engineering'}**, "
        f"or type **help** to see what I can do.\n\n"
        f"Available departments: {', '.join(dept_names) or 'None yet'}"
    )}

# ==================== DEPARTMENT STATS ENDPOINT ====================

@router.get("/department-stats")
async def get_department_stats(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get stats for each department — employees, jobs, candidates"""
    ds_q = db.query(Department)
    if current_user.company_id:
        ds_q = ds_q.filter(Department.company_id == current_user.company_id)
    departments = ds_q.order_by(Department.name).all()
    result = []
    for d in departments:
        dept_id = str(d.id)
        hr_count = db.query(func.count(HRProfile.id)).filter(HRProfile.department_id == dept_id).scalar() or 0
        int_count = db.query(func.count(InterviewerProfile.id)).join(
            User, InterviewerProfile.user_id == User.id
        ).filter(
            InterviewerProfile.department_id == dept_id, User.is_active == True  # noqa: E712
        ).scalar() or 0
        job_count = db.query(func.count(JobPosting.id)).filter(JobPosting.department_id == dept_id).scalar() or 0
        job_ids = [str(j.id) for j in db.query(JobPosting.id).filter(JobPosting.department_id == dept_id).all()]
        cand_count = db.query(func.count(Candidate.id)).filter(Candidate.job_id.in_(job_ids)).scalar() if job_ids else 0
        result.append({
            "name": d.name,
            "employees": hr_count + int_count,
            "jobs": job_count,
            "candidates": cand_count,
        })
    return result

# ==================== DEPARTMENT MANAGEMENT ENDPOINTS ====================

@router.post("/departments", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    department_data: DepartmentCreate,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Create a new department"""
    # Check if department already exists within this company
    dup_check = db.query(Department).filter(Department.name == department_data.name)
    if current_user.company_id:
        dup_check = dup_check.filter(Department.company_id == current_user.company_id)
    existing_dept = dup_check.first()
    if existing_dept:
        raise HTTPException(status_code=400, detail="Department already exists")

    department = Department(
        name=department_data.name,
        description=department_data.description,
        company_id=current_user.company_id
    )
    
    db.add(department)
    db.commit()
    db.refresh(department)
    
    return DepartmentResponse(
        id=str(department.id),
        name=department.name,
        description=department.description,
        logo_url=department.logo_url,
        created_at=department.created_at
    )

@router.get("/departments", response_model=List[DepartmentResponse])
async def get_departments(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all departments"""
    dq = db.query(Department)
    if current_user.company_id:
        dq = dq.filter(Department.company_id == current_user.company_id)
    departments = dq.order_by(Department.name).offset(skip).limit(limit).all()
    
    return [
        DepartmentResponse(
            id=str(dept.id),
            name=dept.name,
            description=dept.description,
            logo_url=dept.logo_url,
            created_at=dept.created_at
        )
        for dept in departments
    ]

# ==================== JOB MANAGEMENT ENDPOINTS ====================

@router.post("/jobs", response_model=JobPostingResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_job(
    job_data: JobPostingCreate,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Create a new job posting (Admin only)
    
    - **title**: Job title
    - **description**: Job description
    - **requirements**: Job requirements
    - **department_id**: Department ID
    - **location**: Job location
    - **employment_type**: Type of employment
    - **salary_min**: Minimum salary (optional)
    - **salary_max**: Maximum salary (optional)
    - **experience_years**: Required years of experience
    - **required_skills**: List of required skills
    """
    # Validate department — the admin's own company only
    department = db.query(Department).filter(
        Department.id == job_data.department_id,
        Department.company_id == current_user.company_id,
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    # Create job posting with admin as creator
    db_job = JobPosting(
        title=job_data.title,
        description=job_data.description,
        requirements=job_data.requirements,
        department_id=job_data.department_id,
        created_by_id=current_user.id,
        hr_id=None,
        location=job_data.location,
        employment_type=job_data.employment_type,
        salary_min=job_data.salary_min,
        salary_max=job_data.salary_max,
        experience_years=job_data.experience_years,
        # Text column: a raw list would be stored as a Python repr that no
        # reader parses back into skills. See src/services/skills_parsing.py.
        required_skills=json.dumps(job_data.required_skills or []),
        company_id=current_user.company_id,
        status="active"
    )
    
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    return JobPostingResponse(
        id=str(db_job.id),
        title=db_job.title,
        description=db_job.description,
        requirements=db_job.requirements,
        department=department.name,
        location=db_job.location,
        employment_type=db_job.employment_type,
        salary_min=float(db_job.salary_min) if db_job.salary_min else None,
        salary_max=float(db_job.salary_max) if db_job.salary_max else None,
        experience_years=db_job.experience_years,
        required_skills=db_job.required_skills or [],
        status=db_job.status,
        created_at=db_job.created_at
    )

@router.get("/jobs", response_model=List[JobPostingResponse])
async def admin_get_all_jobs(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get all job postings (Admin only)"""
    query = db.query(JobPosting)
    if current_user.company_id:
        query = query.filter(JobPosting.company_id == current_user.company_id)

    if status:
        query = query.filter(JobPosting.status == status)
    
    jobs = query.order_by(JobPosting.created_at.desc()).offset(skip).limit(limit).all()
    
    return [
        JobPostingResponse(
            id=str(job.id),
            title=job.title,
            description=job.description,
            requirements=job.requirements,
            department=job.department.name if job.department else "",
            location=job.location,
            employment_type=job.employment_type,
            salary_min=float(job.salary_min) if job.salary_min else None,
            salary_max=float(job.salary_max) if job.salary_max else None,
            experience_years=job.experience_years,
            required_skills=job.required_skills or [],
            status=job.status,
            created_at=job.created_at
        )
        for job in jobs
    ]

@router.put("/departments/{department_id}")
async def update_department(
    department_id: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Update department information"""
    department = db.query(Department).filter(
        Department.id == department_id,
        Department.company_id == current_user.company_id,
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")

    if name:
        # Check if name is already taken by another department in this company
        existing_dept = db.query(Department).filter(
            Department.name == name,
            Department.company_id == current_user.company_id,
            Department.id != department_id
        ).first()
        if existing_dept:
            raise HTTPException(status_code=400, detail="Department name already exists")
        department.name = name
    
    if description is not None:
        department.description = description
    
    db.commit()
    
    return {"message": "Department updated successfully"}


DEPT_LOGOS_DIR = "logos"
DEPT_LOGO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}
DEPT_LOGO_MAX_BYTES = 2 * 1024 * 1024


def _owned_department(db: Session, department_id: str, company_id: Optional[str]) -> Department:
    dept = db.query(Department).filter(
        Department.id == department_id,
        Department.company_id == company_id,
    ).first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
    return dept


@router.post("/departments/{department_id}/logo")
async def upload_department_logo(
    department_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    """Upload a department logo. Replaces (and deletes) any existing one.

    Served by the static/GCS file route already registered for the `logos` prefix
    in main.py, so nothing new is needed to read these back.
    """
    dept = _owned_department(db, department_id, current_user.company_id)

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    ext = ""
    if file.filename and "." in file.filename:
        ext = file.filename.rsplit(".", 1)[-1].lower()
    if ext not in DEPT_LOGO_EXTENSIONS:
        ext = "png"

    contents = await file.read()
    # Bounded read: these are inlined into the departments list, and the upload is
    # reachable by any company admin.
    if len(contents) > DEPT_LOGO_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Logo must be 2 MB or smaller")
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    from src.services.storage import storage

    old_url = dept.logo_url
    filename = f"{uuid.uuid4()}.{ext}"
    key = f"{DEPT_LOGOS_DIR}/{filename}"
    storage.save_bytes(contents, key)

    dept.logo_url = f"/{DEPT_LOGOS_DIR}/{filename}"
    db.commit()
    db.refresh(dept)

    # Only after the new one is committed — a failed save must not lose the old logo.
    if old_url:
        storage.delete(old_url.lstrip("/"))

    return {"id": dept.id, "logo_url": dept.logo_url}


@router.delete("/departments/{department_id}/logo")
async def delete_department_logo(
    department_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    """Remove a department's logo."""
    dept = _owned_department(db, department_id, current_user.company_id)
    old_url = dept.logo_url
    dept.logo_url = None
    db.commit()

    if old_url:
        from src.services.storage import storage
        storage.delete(old_url.lstrip("/"))

    return {"id": department_id, "logo_url": None}


@router.delete("/departments/{department_id}")
async def delete_department(
    department_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Delete a department"""
    department = db.query(Department).filter(
        Department.id == department_id,
        Department.company_id == current_user.company_id,
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    db.delete(department)
    db.commit()
    return {"message": "Department deleted successfully"}


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Deactivate a user. NOTHING is deleted, despite the HTTP verb.

    The path is a DELETE because that is what the admin UI has always called; the row is
    never removed. The user, their name, their email and their HR/InterviewerProfile all
    survive, which is what makes Activate work and what keeps completed interviews and
    submitted feedback attributed to a real person. ``deleted: false`` is in the response
    so the contract says so too, not just this docstring.
    """
    user = db.query(User).filter(
        User.id == user_id,
        User.company_id == current_user.company_id,
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    was_active = bool(user.is_active)
    user.is_active = False
    summary = deactivate_interviewer_assignments(db, user, current_user.id) if was_active else None
    db.commit()
    response = {
        "message": "User deactivated successfully — the account is kept and can be reactivated",
        "deleted": False,
        "is_active": False,
    }
    if summary:
        response.update(summary)
    return response


# ==================== HR COMPARISON ANALYTICS ====================

@router.get("/analytics/hr-comparison")
async def get_hr_comparison(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Compare HR personnel — jobs created, candidates handled, interviews, offers"""
    hr_q = db.query(User).filter(User.role == "hr")
    if current_user.company_id:
        hr_q = hr_q.filter(User.company_id == current_user.company_id)
    hr_users = hr_q.all()
    result = []
    for hr in hr_users:
        hr_id = str(hr.id)
        name = f"{hr.first_name or ''} {hr.last_name or ''}".strip() or hr.email
        hr_job_ids = [str(j.id) for j in db.query(JobPosting.id).filter(JobPosting.created_by_id == hr_id).all()]
        jobs_created = len(hr_job_ids)
        candidates_handled = db.query(func.count(Candidate.id)).filter(Candidate.job_id.in_(hr_job_ids)).scalar() if hr_job_ids else 0
        interviews_scheduled = db.query(func.count(Interview.id)).filter(Interview.job_id.in_(hr_job_ids)).scalar() if hr_job_ids else 0
        offers_made = db.query(func.count(Offer.id)).filter(Offer.job_id.in_(hr_job_ids)).scalar() if hr_job_ids else 0
        avg_ats = db.query(func.avg(Candidate.ats_score)).filter(Candidate.job_id.in_(hr_job_ids), Candidate.ats_score.isnot(None)).scalar() if hr_job_ids else None
        result.append({
            "name": name,
            "jobs": jobs_created,
            "candidates": candidates_handled,
            "interviews": interviews_scheduled,
            "offers": offers_made,
            "avg_ats": round(float(avg_ats), 1) if avg_ats else 0,
        })
    return result

# ==================== SYSTEM ANALYTICS ENDPOINTS ====================

@router.get("/analytics/overview", response_model=SystemAnalytics)
async def get_system_analytics(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get system-wide analytics overview"""
    cid = current_user.company_id
    # Get counts
    total_users = db.query(User).filter(User.company_id == cid).count()
    total_jobs = db.query(JobPosting).filter(JobPosting.company_id == cid).count()
    active_jobs = db.query(JobPosting).filter(JobPosting.company_id == cid, JobPosting.status == "active").count()
    # Scoped exactly as GET /departments is, so the card matches the list it opens.
    total_departments = db.query(Department).filter(Department.company_id == cid).count()
    total_candidates = db.query(Candidate).filter(Candidate.company_id == cid).count()

    # Get candidates by stage
    candidates_by_stage = db.query(
        Candidate.current_stage,
        func.count(Candidate.id).label('count')
    ).filter(Candidate.company_id == cid).group_by(Candidate.current_stage).all()

    # Get recent applications with job eagerly loaded
    recent_applications = db.query(Candidate).options(
        joinedload(Candidate.job)
    ).filter(Candidate.company_id == cid).order_by(
        Candidate.created_at.desc()
    ).limit(10).all()

    return SystemAnalytics(
        total_users=total_users,
        total_jobs=total_jobs,
        active_jobs=active_jobs,
        total_departments=total_departments,
        total_candidates=total_candidates,
        candidates_by_stage=[
            {"stage": stage or "unknown", "count": count} for stage, count in candidates_by_stage
        ],
        recent_applications=[
            {
                "name": f"{c.first_name} {c.last_name}",
                "job_title": c.job.title if c.job else "N/A",
                "stage": c.current_stage or "applied",
                "ats_score": float(c.ats_score) if c.ats_score else None,
                "applied_at": c.created_at
            }
            for c in recent_applications
        ]
    )

@router.get("/analytics/gender-distribution")
async def get_gender_distribution(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get gender distribution of candidates"""
    gender_counts = db.query(
        User.gender,
        func.count(User.id).label('count')
    ).filter(
        User.role == "candidate",
        User.company_id == current_user.company_id
    ).group_by(User.gender).all()

    result = []
    for gender, count in gender_counts:
        label = (gender or "not_specified").capitalize()
        result.append({"gender": label, "count": count})

    return result

@router.get("/analytics/age-distribution")
async def get_age_distribution(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get age range distribution of candidates"""
    today = date.today()

    candidates = db.query(User.date_of_birth).filter(
        User.role == "candidate",
        User.date_of_birth.isnot(None),
        User.company_id == current_user.company_id
    ).all()

    ranges = {
        "18-24": 0, "25-30": 0, "31-35": 0,
        "36-40": 0, "41-45": 0, "46-50": 0, "50+": 0
    }

    for (dob,) in candidates:
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 18:
            continue
        elif age <= 24:
            ranges["18-24"] += 1
        elif age <= 30:
            ranges["25-30"] += 1
        elif age <= 35:
            ranges["31-35"] += 1
        elif age <= 40:
            ranges["36-40"] += 1
        elif age <= 45:
            ranges["41-45"] += 1
        elif age <= 50:
            ranges["46-50"] += 1
        else:
            ranges["50+"] += 1

    return [{"range": k, "count": v} for k, v in ranges.items()]

@router.get("/analytics/jobs/{job_id}", response_model=JobAnalytics)
async def get_job_analytics(
    job_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get detailed analytics for a specific job"""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get total applications and average ATS score
    candidates_query = db.query(Candidate).filter(Candidate.job_id == job_id)
    total_applications = candidates_query.count()
    
    avg_ats_score = db.query(func.avg(Candidate.ats_score)).filter(
        Candidate.job_id == job_id,
        Candidate.ats_score.isnot(None)
    ).scalar()
    
    # Get stage distribution
    stage_distribution = db.query(
        Candidate.current_stage,
        func.count(Candidate.id).label('count')
    ).filter(Candidate.job_id == job_id).group_by(Candidate.current_stage).all()
    
    # Get applications by date (last 30 days)
    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    applications_by_date = db.query(
        func.date(Candidate.created_at).label('date'),
        func.count(Candidate.id).label('count')
    ).filter(
        Candidate.job_id == job_id,
        Candidate.created_at >= thirty_days_ago
    ).group_by(func.date(Candidate.created_at)).all()
    
    return JobAnalytics(
        job_id=job_id,
        job_title=job.title,
        total_applications=total_applications,
        average_ats_score=float(avg_ats_score) if avg_ats_score else None,
        stage_distribution=[
            {"stage": stage, "count": count} for stage, count in stage_distribution
        ],
        applications_by_date=[
            {"date": date.isoformat(), "count": count} for date, count in applications_by_date
        ]
    )

@router.get("/analytics/hr/{hr_id}")
async def get_hr_analytics(
    hr_id: str,
    days: int = 30,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get analytics for a specific HR user"""
    hr_profile = db.query(HRProfile).filter(HRProfile.id == hr_id).first()
    if not hr_profile:
        raise HTTPException(status_code=404, detail="HR profile not found")
    if hr_profile.user and hr_profile.user.company_id != current_user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")
    
    start_date = datetime.utcnow() - timedelta(days=days)
    
    # Get jobs created by HR
    jobs_created = db.query(JobPosting).filter(
        JobPosting.hr_id == hr_id,
        JobPosting.created_at >= start_date
    ).count()
    
    # Get total candidates for HR's jobs
    total_candidates = db.query(Candidate).join(JobPosting).filter(
        JobPosting.hr_id == hr_id
    ).count()
    
    # Get interviews scheduled
    interviews_scheduled = db.query(Interview).join(Candidate).join(JobPosting).filter(
        JobPosting.hr_id == hr_id,
        Interview.created_at >= start_date
    ).count()
    
    # Get offers sent
    offers_sent = db.query(Offer).join(Candidate).join(JobPosting).filter(
        JobPosting.hr_id == hr_id,
        Offer.created_at >= start_date
    ).count()
    
    return {
        "hr_info": {
            "name": f"{hr_profile.user.first_name} {hr_profile.user.last_name}",
            "email": hr_profile.user.email,
            "department": hr_profile.department.name if hr_profile.department else None
        },
        "period": {
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": datetime.utcnow().isoformat()
        },
        "metrics": {
            "jobs_created": jobs_created,
            "total_candidates": total_candidates,
            "interviews_scheduled": interviews_scheduled,
            "offers_sent": offers_sent
        }
    }

# ==================== SYSTEM CONFIGURATION ENDPOINTS ====================

@router.get("/config")
async def get_system_config(
    current_user: User = Depends(require_role(["superadmin"])),
    db: Session = Depends(get_db)
):
    """Get platform configuration (superadmin only).

    SystemConfig is platform-wide — it has no company column — so a client company's
    admin must not read it, and above all must not overwrite it for every tenant.
    """
    configs = db.query(SystemConfig).all()
    
    return {
        config.config_key: config.config_value
        for config in configs
    }

@router.put("/config/{config_key}")
async def update_system_config(
    config_key: str,
    config_value: dict,
    current_user: User = Depends(require_role(["superadmin"])),
    db: Session = Depends(get_db)
):
    """Update platform configuration (superadmin only; see get_system_config)."""
    config = db.query(SystemConfig).filter(SystemConfig.config_key == config_key).first()
    
    if config:
        config.config_value = config_value
        config.updated_at = datetime.utcnow()
    else:
        config = SystemConfig(
            config_key=config_key,
            config_value=config_value
        )
        db.add(config)
    
    db.commit()
    
    return {"message": "Configuration updated successfully"}

# ==================== HEALTH AND SYSTEM ENDPOINTS ====================

@router.get("/system/health")
async def get_system_health(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db)
):
    """Get detailed system health information"""
    try:
        # Test database connection
        db.execute("SELECT 1")
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
    
    # Statistics for the admin's own company — these used to count every tenant.
    cid = current_user.company_id
    total_users = db.query(User).filter(User.company_id == cid).count()
    active_users = db.query(User).filter(User.company_id == cid, User.is_active == True).count()

    # Get recent activity (last 24 hours)
    yesterday = datetime.utcnow() - timedelta(days=1)
    recent_logins = 0  # This would require login tracking table
    recent_applications = db.query(Candidate).filter(
        Candidate.company_id == cid,
        Candidate.created_at >= yesterday
    ).count()
    
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {
            "database": db_status,
            "api": "healthy"
        },
        "statistics": {
            "total_users": total_users,
            "active_users": active_users,
            "recent_applications_24h": recent_applications
        }
    }


# ==================== EMAIL TEST ====================

class EmailTestRequest(BaseModel):
    to: EmailStr
    subject: Optional[str] = "HireFlow SMTP Test"
    body: Optional[str] = "<p>This is a test email from HireFlow.</p>"


@router.post("/email/test")
async def send_test_email(
    payload: EmailTestRequest,
    current_user: User = Depends(require_role(["admin", "hr"])),
    db: Session = Depends(get_db),
):
    """Admin/HR: send a real SMTP test email through the company's own SMTP and return
    the actual delivery result.

    A company with no SMTP configured gets a 400, not a send through the platform's
    mailbox: the fallback let any tenant send as the platform and echoed the platform's
    SMTP host and account back to them.
    """
    from src.services import email_service
    smtp_config = email_service.resolve_smtp_for_company(db, current_user.company_id)
    if not smtp_config:
        raise HTTPException(
            status_code=400,
            detail="Your company has no SMTP configured. Save it under Email settings first.",
        )
    result = email_service._send(payload.to, payload.subject, payload.body, smtp_config=smtp_config)
    return {
        "using_company_smtp": True,
        "config": {
            "smtp_host": smtp_config.get("host"),
            "smtp_port": smtp_config.get("port"),
            "smtp_user": smtp_config.get("user"),
            "from_email": smtp_config.get("from_email"),
            "email_enabled": email_service.EMAIL_ENABLED,
        },
        **result,
    }


# ==================== PER-COMPANY EMAIL CONFIG ====================

class EmailConfigPayload(BaseModel):
    smtp_host:       Optional[str] = "smtp.gmail.com"
    smtp_port:       Optional[int] = 587
    smtp_user:       EmailStr           # the FROM email / Gmail account
    smtp_pass:       Optional[str] = None    # app password — omit to keep existing
    smtp_from_email: Optional[EmailStr] = None  # defaults to smtp_user if not set


@router.get("/email-config")
async def get_email_config(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Return this company's saved SMTP config. Password is never returned —
    only a boolean ``has_password`` indicating whether one is stored."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")
    tc = db.query(TenantConfig).filter(TenantConfig.company_id == current_user.company_id).first()
    if not tc:
        return {
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_user": "",
            "smtp_from_email": "",
            "has_password": False,
            "configured": False,
        }
    return {
        "smtp_host":       tc.smtp_host or "smtp.gmail.com",
        "smtp_port":       tc.smtp_port or 587,
        "smtp_user":       tc.smtp_user or "",
        "smtp_from_email": tc.smtp_from_email or "",
        "has_password":    bool(tc.smtp_pass),
        "configured":      bool(tc.smtp_user and tc.smtp_pass),
    }


@router.put("/email-config")
async def update_email_config(
    payload: EmailConfigPayload,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Save / update this company's SMTP credentials. Encrypts the app password
    at rest. Omit ``smtp_pass`` to keep the existing stored password."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")

    tc = db.query(TenantConfig).filter(TenantConfig.company_id == current_user.company_id).first()
    if not tc:
        tc = TenantConfig(company_id=current_user.company_id)
        db.add(tc)

    tc.smtp_host       = payload.smtp_host or "smtp.gmail.com"
    tc.smtp_port       = int(payload.smtp_port or 587)
    tc.smtp_user       = payload.smtp_user
    tc.smtp_from_email = payload.smtp_from_email or payload.smtp_user
    if payload.smtp_pass:  # only overwrite if a fresh password is supplied
        tc.smtp_pass = encrypt(payload.smtp_pass)
    tc.updated_at = datetime.utcnow()

    try:
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to save email config: {e}")

    return {
        "saved": True,
        "smtp_host":       tc.smtp_host,
        "smtp_port":       tc.smtp_port,
        "smtp_user":       tc.smtp_user,
        "smtp_from_email": tc.smtp_from_email,
        "has_password":    bool(tc.smtp_pass),
        "configured":      bool(tc.smtp_user and tc.smtp_pass),
    }


@router.delete("/email-config")
async def clear_email_config(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Remove this company's SMTP override so it falls back to the global default."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")
    tc = db.query(TenantConfig).filter(TenantConfig.company_id == current_user.company_id).first()
    if tc:
        tc.smtp_host = None
        tc.smtp_user = None
        tc.smtp_pass = None
        tc.smtp_from_email = None
        tc.updated_at = datetime.utcnow()
        db.commit()
    return {"cleared": True}


# ==================== NAMED SENDER MAILBOXES ====================
# Admin defines multiple named mailboxes; HR selects one per pipeline step.

class MailboxPayload(BaseModel):
    name:            str
    smtp_host:       Optional[str] = "smtp.gmail.com"
    smtp_port:       Optional[int] = 587
    smtp_user:       EmailStr
    smtp_pass:       Optional[str] = None        # omit to keep existing
    smtp_from_email: Optional[EmailStr] = None
    hourly_limit:    Optional[int] = None        # max sends/hour; null = env default
    is_active:       bool = True


def _serialize_mailbox(mb: CompanyMailbox) -> dict:
    return {
        "id":              mb.id,
        "name":            mb.name,
        "smtp_host":       mb.smtp_host or "smtp.gmail.com",
        "smtp_port":       mb.smtp_port or 587,
        "smtp_user":       mb.smtp_user or "",
        "smtp_from_email": mb.smtp_from_email or "",
        "hourly_limit":    mb.hourly_limit,
        "has_password":    bool(mb.smtp_pass),
        "is_active":       bool(mb.is_active),
    }


@router.get("/mailboxes")
async def list_mailboxes(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """List this company's named sender mailboxes (passwords never returned)."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")
    rows = (
        db.query(CompanyMailbox)
        .filter(CompanyMailbox.company_id == current_user.company_id)
        .order_by(CompanyMailbox.name)
        .all()
    )
    return [_serialize_mailbox(m) for m in rows]


@router.post("/mailboxes")
async def create_mailbox(
    payload: MailboxPayload,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Create a named sender mailbox. The app password is encrypted at rest."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Mailbox name is required")
    exists = (
        db.query(CompanyMailbox)
        .filter(CompanyMailbox.company_id == current_user.company_id,
                CompanyMailbox.name == name)
        .first()
    )
    if exists:
        raise HTTPException(status_code=409, detail="A mailbox with that name already exists")

    mb = CompanyMailbox(
        company_id=current_user.company_id,
        name=name,
        smtp_host=payload.smtp_host or "smtp.gmail.com",
        smtp_port=int(payload.smtp_port or 587),
        smtp_user=payload.smtp_user,
        smtp_from_email=payload.smtp_from_email or payload.smtp_user,
        hourly_limit=payload.hourly_limit,
        is_active=payload.is_active,
    )
    if payload.smtp_pass:
        mb.smtp_pass = encrypt(payload.smtp_pass)
    db.add(mb)
    try:
        db.commit()
        db.refresh(mb)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create mailbox: {e}")
    return _serialize_mailbox(mb)


@router.put("/mailboxes/{mailbox_id}")
async def update_mailbox(
    mailbox_id: str,
    payload: MailboxPayload,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Update a mailbox. Omit ``smtp_pass`` to keep the stored password."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")
    mb = (
        db.query(CompanyMailbox)
        .filter(CompanyMailbox.id == mailbox_id,
                CompanyMailbox.company_id == current_user.company_id)
        .first()
    )
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Mailbox name is required")
    clash = (
        db.query(CompanyMailbox)
        .filter(CompanyMailbox.company_id == current_user.company_id,
                CompanyMailbox.name == name, CompanyMailbox.id != mailbox_id)
        .first()
    )
    if clash:
        raise HTTPException(status_code=409, detail="A mailbox with that name already exists")

    mb.name            = name
    mb.smtp_host       = payload.smtp_host or "smtp.gmail.com"
    mb.smtp_port       = int(payload.smtp_port or 587)
    mb.smtp_user       = payload.smtp_user
    mb.smtp_from_email = payload.smtp_from_email or payload.smtp_user
    mb.hourly_limit    = payload.hourly_limit
    mb.is_active       = payload.is_active
    if payload.smtp_pass:
        mb.smtp_pass = encrypt(payload.smtp_pass)
    mb.updated_at = datetime.utcnow()
    try:
        db.commit()
        db.refresh(mb)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update mailbox: {e}")
    return _serialize_mailbox(mb)


@router.delete("/mailboxes/{mailbox_id}")
async def delete_mailbox(
    mailbox_id: str,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Delete a mailbox. Any pipeline steps using it revert to the company default."""
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")
    mb = (
        db.query(CompanyMailbox)
        .filter(CompanyMailbox.id == mailbox_id,
                CompanyMailbox.company_id == current_user.company_id)
        .first()
    )
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found")
    # Detach any steps pointing at this mailbox so they fall back to the default.
    db.query(PipelineStepConfig).filter(
        PipelineStepConfig.company_id == current_user.company_id,
        PipelineStepConfig.mailbox_id == mailbox_id,
    ).update({"mailbox_id": None})
    db.delete(mb)
    db.commit()
    return {"deleted": True}


@router.post("/mailboxes/{mailbox_id}/test")
async def test_mailbox(
    mailbox_id: str,
    payload: EmailTestRequest,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Send a real message using THIS mailbox's own credentials.

    Saving a mailbox does not verify anything — create/update just encrypt
    whatever password was typed. Without this, a typo is only discovered when a
    candidate-facing send fails later, and the mailbox quietly falls back to the
    company default in the meantime. This is the one place that proves the
    credential actually works.

    Deliberately synchronous (``_send``, not ``dispatch``): a queued send returns
    "accepted onto the queue" and the auth failure surfaces in the worker log
    minutes later, which is exactly the false confidence this endpoint exists to
    remove.
    """
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")

    from src.services import email_service
    from src.services.pipeline_notifications import mailbox_smtp_config, mailbox_unusable_reason

    mb = (
        db.query(CompanyMailbox)
        .filter(CompanyMailbox.id == mailbox_id,
                CompanyMailbox.company_id == current_user.company_id)
        .first()
    )
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found")

    summary = {"id": mb.id, "name": mb.name,
               "from_email": mb.smtp_from_email or mb.smtp_user}

    # A missing password is a configuration answer, not an SMTP round trip.
    # Sharing mailbox_unusable_reason keeps this wording identical to the routing
    # panel and to what the real send path decides.
    reason = mailbox_unusable_reason(mb)
    if reason:
        return {"mailbox": summary, "config": None, "sent": False,
                "error": reason, "error_type": "unusable"}

    # Company-scoped lookup: never the id-only _mailbox_smtp_config, which would
    # let a guessed id reach another tenant's SMTP credentials.
    smtp_config = mailbox_smtp_config(db, current_user.company_id, mailbox_id)
    if not smtp_config:
        return {"mailbox": summary, "config": None, "sent": False,
                "error": "Mailbox could not be resolved for sending",
                "error_type": "unusable"}

    result = email_service._send(
        payload.to, payload.subject, payload.body,
        smtp_config=smtp_config, db=db,
    )
    return {
        "mailbox": summary,
        # Non-secret fields only — the password never leaves the server.
        "config": {
            "smtp_host": smtp_config.get("host"),
            "smtp_port": smtp_config.get("port"),
            "smtp_user": smtp_config.get("user"),
            "from_email": smtp_config.get("from_email"),
            "email_enabled": email_service.EMAIL_ENABLED,
        },
        **result,
    }


# ==================== MAIL ROUTING (read-only diagnostics) ====================


@router.get("/email/routing")
async def get_email_routing(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Show the full sender-resolution chain and per-step routing for this company.

    Read-only. Answers the questions the individual config screens can't: which
    address a given pipeline step actually sends from, why a configured mailbox
    is being ignored, and whether mail is being queued or sent inline.

    Never returns SMTP passwords — neither plaintext nor the stored ciphertext.
    """
    if not current_user.company_id:
        raise HTTPException(status_code=400, detail="Admin is not associated with a company")

    from src.services import email_service
    from src.services.pipeline_notifications import STEP_KEYS, describe_step_dispatch

    chain = email_service.describe_sender_chain(db, current_user.company_id, "candidate")
    steps = [describe_step_dispatch(db, current_user.company_id, k) for k in STEP_KEYS]

    # Delivery mode. redis_available() caches process-wide (src/mailq/queue.py:39),
    # so this reflects the state observed when this worker first checked, not now.
    redis_ok, queue_depth, sync_forced = False, None, False
    try:
        from src.mailq import queue as mailq
        sync_forced = mailq._sync_forced()
        redis_ok = mailq.redis_available()
        if redis_ok:
            r = mailq.get_redis()
            queue_depth = r.llen(f"rq:queue:{mailq._QUEUE_NAME}")
    except Exception as e:
        logger.warning(f"email routing: queue probe failed: {e}")

    queued = redis_ok and not sync_forced
    return {
        "chain": chain,
        "steps": steps,
        "delivery": {
            "email_enabled":   email_service.EMAIL_ENABLED,
            "sync_forced":     sync_forced,
            "redis_available": redis_ok,
            "queue_depth":     queue_depth,
            "mode":            "queued" if queued else "inline",
        },
    }
