"""
DATABASE MODELS — consolidated, minimal schema (29 tables)
Removed: InviteToken, HRTask, AssessmentRound, CandidatePreferences,
         CandidateLinks, CheatingLog, TabSwitchLog, ATSUser, ATSInterview
Merged:  CandidatePreferences + CandidateLinks → CandidateProfileExt
Added:   TenantConfig, SubscriptionPlan
"""

from sqlalchemy import (Column, Integer, String, Float, DateTime, Boolean,
                         Text, ForeignKey, JSON, DECIMAL, Date, UniqueConstraint)
from sqlalchemy.orm import relationship, declarative_base
from ..config.database_config_mysql import db_manager
from ..tenancy import TenantScoped

Base = declarative_base()
import uuid
from datetime import datetime


def _uuid():
    return str(uuid.uuid4())


# ═══════════════════════════════════════════════════════
# TENANT / COMPANY
# ═══════════════════════════════════════════════════════

class Company(Base):
    __tablename__ = "companies"

    id             = Column(String(36), primary_key=True, default=_uuid)
    name           = Column(String(255), nullable=False)
    slug           = Column(String(100), unique=True, nullable=False, index=True)
    logo_url       = Column(String(500), nullable=True)
    favicon_url    = Column(String(500), nullable=True)
    primary_color  = Column(String(20), default="#2b7cba")
    secondary_color= Column(String(20), nullable=True)
    accent_color   = Column(String(20), nullable=True)
    font_family    = Column(String(100), nullable=True)
    tagline        = Column(String(255), nullable=True)
    support_email  = Column(String(255), nullable=True)
    api_key        = Column(String(64), unique=True, nullable=True, index=True)
    custom_domain        = Column(String(255), nullable=True, index=True)
    domain_verified      = Column(Boolean, default=False)
    domain_pending       = Column(String(255), nullable=True)   # domain submitted but not yet verified
    domain_verify_token  = Column(String(64),  nullable=True)   # TXT record value for DNS verification
    is_active      = Column(Boolean, default=True)

    # Branding extension
    logo_dark_url  = Column(String(500), nullable=True)

    # Legal & tax identity
    legal_name                       = Column(String(255), nullable=True)
    brand_name                       = Column(String(255), nullable=True)
    company_type                     = Column(String(50),  nullable=True)  # pvt_ltd | llp | public | proprietorship | partnership | other
    cin                              = Column(String(50),  nullable=True)
    incorporation_date               = Column(Date,        nullable=True)
    authorized_signatory_name        = Column(String(255), nullable=True)
    authorized_signatory_designation = Column(String(100), nullable=True)

    # Role-based contacts
    primary_contact_name        = Column(String(255), nullable=True)
    primary_contact_email       = Column(String(255), nullable=True)
    primary_contact_phone       = Column(String(40),  nullable=True)
    primary_contact_designation = Column(String(100), nullable=True)

    billing_contact_name        = Column(String(255), nullable=True)
    billing_contact_email       = Column(String(255), nullable=True)
    billing_contact_phone       = Column(String(40),  nullable=True)
    billing_contact_designation = Column(String(100), nullable=True)

    technical_contact_name        = Column(String(255), nullable=True)
    technical_contact_email       = Column(String(255), nullable=True)
    technical_contact_phone       = Column(String(40),  nullable=True)
    technical_contact_designation = Column(String(100), nullable=True)

    dpo_contact_name        = Column(String(255), nullable=True)
    dpo_contact_email       = Column(String(255), nullable=True)
    dpo_contact_phone       = Column(String(40),  nullable=True)
    dpo_contact_designation = Column(String(100), nullable=True)

    escalation_contact_name        = Column(String(255), nullable=True)
    escalation_contact_email       = Column(String(255), nullable=True)
    escalation_contact_phone       = Column(String(40),  nullable=True)
    escalation_contact_designation = Column(String(100), nullable=True)

    # Registered address
    reg_address_line1 = Column(String(255), nullable=True)
    reg_address_line2 = Column(String(255), nullable=True)
    reg_city          = Column(String(100), nullable=True)
    reg_state         = Column(String(100), nullable=True)
    reg_country       = Column(String(100), nullable=True)
    reg_postal_code   = Column(String(20),  nullable=True)

    # Billing address
    billing_same_as_registered = Column(Boolean,      default=True)
    billing_address_line1      = Column(String(255), nullable=True)
    billing_address_line2      = Column(String(255), nullable=True)
    billing_city               = Column(String(100), nullable=True)
    billing_state              = Column(String(100), nullable=True)
    billing_country            = Column(String(100), nullable=True)
    billing_postal_code        = Column(String(20),  nullable=True)

    # Business profile
    industry           = Column(String(100), nullable=True)
    company_size_band  = Column(String(30),  nullable=True)  # 1-10 | 11-50 | 51-200 | 201-1000 | 1000+
    years_in_business  = Column(Integer,     nullable=True)
    website_url        = Column(String(500), nullable=True)
    description        = Column(Text,        nullable=True)

    # Hiring footprint
    expected_hires_per_year = Column(Integer,     nullable=True)
    recruiter_count         = Column(Integer,     nullable=True)
    hiring_locations        = Column(Text,        nullable=True)  # comma-separated cities/countries
    existing_ats            = Column(String(255), nullable=True)
    role_categories         = Column(Text,        nullable=True)  # comma-separated

    created_at     = Column(DateTime, default=datetime.utcnow)

    users         = relationship("User", back_populates="company")
    departments   = relationship("Department", back_populates="company")
    job_postings  = relationship("JobPosting", back_populates="company")
    tenant_config = relationship("TenantConfig", back_populates="company", uselist=False)
    subscription  = relationship("SubscriptionPlan", back_populates="company", uselist=False)


class TenantConfig(Base):
    __tablename__ = "tenant_configs"

    id              = Column(String(36), primary_key=True, default=_uuid)
    company_id      = Column(String(36), ForeignKey("companies.id"), unique=True, nullable=False)
    smtp_host       = Column(String(255), nullable=True)
    smtp_port       = Column(Integer, default=587)
    smtp_user       = Column(String(255), nullable=True)
    smtp_pass       = Column(String(255), nullable=True)
    smtp_from_email = Column(String(255), nullable=True)
    ai_model        = Column(String(100), nullable=True)
    max_jobs        = Column(Integer, default=50)
    max_users       = Column(Integer, default=20)
    max_candidates  = Column(Integer, default=500)
    features_json   = Column(Text, nullable=True)   # {"ats":true,"proctoring":true,"ai_questions":true}
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = relationship("Company", back_populates="tenant_config")


class CompanyMailbox(Base):
    """A named sender mailbox an Admin configures for the company.

    HR selects one of these per pipeline step (by id) instead of typing SMTP
    credentials. Mirrors ``TenantConfig``'s SMTP fields but is many-per-company
    and named (e.g. "HR", "Offers", "Interviews"). ``smtp_pass`` is encrypted at
    rest (src/security.py). The single ``TenantConfig`` sender remains the
    "Company default" used when a step has no mailbox selected.
    """
    __tablename__ = "company_mailboxes"

    id              = Column(String(36), primary_key=True, default=_uuid)
    company_id      = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    name            = Column(String(100), nullable=False)
    smtp_host       = Column(String(255), nullable=True)
    smtp_port       = Column(Integer, default=587)
    smtp_user       = Column(String(255), nullable=True)
    smtp_pass       = Column(String(255), nullable=True)   # encrypted
    smtp_from_email = Column(String(255), nullable=True)
    hourly_limit    = Column(Integer, nullable=True)   # max sends/hour; NULL = EMAIL_HOURLY_LIMIT default
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_mailbox_company_name"),)


class PipelineStepConfig(Base):
    """Per-company, per-step notification config.

    Lets HR add extra recipients (typed addresses + roles) and select an
    Admin-defined sender mailbox (``mailbox_id`` → CompanyMailbox) for each
    hiring-pipeline email step. A missing row means "default behaviour": only
    the primary recipient, company/Innomax sender. ``extra_emails`` and
    ``notify_roles`` are JSON-encoded lists (same convention as ``features_json``).
    ``mailbox_id`` NULL = use the Company default sender.
    """
    __tablename__ = "pipeline_step_configs"

    id              = Column(String(36), primary_key=True, default=_uuid)
    company_id      = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    step_key        = Column(String(50), nullable=False)   # one of pipeline_notifications.STEP_KEYS
    enabled         = Column(Boolean, default=True)
    extra_emails    = Column(Text, nullable=True)          # JSON list of typed addresses
    notify_roles    = Column(Text, nullable=True)          # JSON list e.g. ["hr","interviewer","admin"]
    as_cc           = Column(Boolean, default=True)        # CC on the same mail vs. (future) separate copy
    mailbox_id      = Column(String(36), ForeignKey("company_mailboxes.id"), nullable=True)  # NULL = company default
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("company_id", "step_key", name="uq_pipeline_step_company"),)


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id                  = Column(String(36), primary_key=True, default=_uuid)
    company_id          = Column(String(36), ForeignKey("companies.id"), unique=True, nullable=False)
    plan_name           = Column(String(50), default="starter")   # starter | growth | enterprise
    max_jobs            = Column(Integer, default=10)
    max_users           = Column(Integer, default=5)
    max_candidates      = Column(Integer, default=100)
    billing_cycle       = Column(String(20), default="monthly")   # monthly | annual
    price               = Column(DECIMAL(10, 2), default=0)
    currency            = Column(String(3),  default="INR")        # INR | USD | EUR | ...
    payment_method      = Column(String(30), nullable=True)        # bank_transfer | card | upi | other
    po_required         = Column(Boolean,    default=False)
    po_number           = Column(String(100), nullable=True)
    status              = Column(String(20), default="trial")     # trial | active | expired | cancelled
    trial_ends_at       = Column(DateTime, nullable=True)
    current_period_end  = Column(DateTime, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company = relationship("Company", back_populates="subscription")


# ═══════════════════════════════════════════════════════
# USERS & PROFILES
# ═══════════════════════════════════════════════════════

class User(Base, TenantScoped):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("company_id", "email", name="uq_users_company_email"),
    )

    id            = Column(String(36), primary_key=True, default=_uuid)
    company_id    = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    email         = Column(String(255), index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role          = Column(String(50), nullable=False)   # superadmin | admin | hr | interviewer | candidate
    first_name    = Column(String(100), nullable=False)
    last_name     = Column(String(100), nullable=False)
    phone         = Column(String(20), nullable=True)
    gender        = Column(String(20), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    is_active       = Column(Boolean, default=True)
    email_verified  = Column(Boolean, default=False)
    otp_code        = Column(String(6), nullable=True)
    otp_expires_at  = Column(DateTime, nullable=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company              = relationship("Company", back_populates="users")
    hr_profile           = relationship("HRProfile", back_populates="user", uselist=False)
    interviewer_profile  = relationship("InterviewerProfile", back_populates="user", uselist=False)
    job_postings         = relationship("JobPosting", back_populates="created_by")
    candidates           = relationship("Candidate", back_populates="user")


class Department(Base, TenantScoped):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_dept_company_name"),)

    id          = Column(String(36), primary_key=True, default=_uuid)
    company_id  = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    name        = Column(String(100), nullable=False)
    description = Column(Text)
    # Named *_url, not *_path, so scripts/verify_storage_keys.py picks it up — that
    # checker allow-lists storage-key columns by name and walks Base.metadata.
    logo_url    = Column(String(500), nullable=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    company              = relationship("Company", back_populates="departments")
    job_postings         = relationship("JobPosting", back_populates="department")
    hr_profiles          = relationship("HRProfile", back_populates="department")
    interviewer_profiles = relationship("InterviewerProfile", back_populates="department")


class HRProfile(Base, TenantScoped):
    __tablename__ = "hr_profiles"

    id            = Column(String(36), primary_key=True, default=_uuid)
    company_id    = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    user_id       = Column(String(36), ForeignKey("users.id"), unique=True)
    department_id = Column(String(36), ForeignKey("departments.id"))
    employee_id   = Column(String(50), nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow)

    user         = relationship("User", back_populates="hr_profile")
    department   = relationship("Department", back_populates="hr_profiles")
    job_postings = relationship("JobPosting", back_populates="hr")
    offers       = relationship("Offer", back_populates="hr")


class InterviewerProfile(Base, TenantScoped):
    __tablename__ = "interviewer_profiles"

    id               = Column(String(36), primary_key=True, default=_uuid)
    company_id       = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    user_id          = Column(String(36), ForeignKey("users.id"), unique=True)
    department_id    = Column(String(36), ForeignKey("departments.id"))
    employee_id      = Column(String(50), nullable=False)
    expertise_areas  = Column(Text)    # JSON string
    years_experience = Column(Integer)
    created_at       = Column(DateTime, default=datetime.utcnow)

    user       = relationship("User", back_populates="interviewer_profile")
    department = relationship("Department", back_populates="interviewer_profiles")


# ═══════════════════════════════════════════════════════
# JOBS
# ═══════════════════════════════════════════════════════

class JobPosting(Base, TenantScoped):
    __tablename__ = "job_postings"

    id              = Column(String(36), primary_key=True, default=_uuid)
    company_id      = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    title           = Column(String(255), nullable=False)
    description     = Column(Text, nullable=False)
    requirements    = Column(Text, nullable=False)
    department_id   = Column(String(36), ForeignKey("departments.id"))
    created_by_id   = Column(String(36), ForeignKey("users.id"))
    hr_id           = Column(String(36), ForeignKey("hr_profiles.id"))
    interviewer_id  = Column(String(36), ForeignKey("interviewer_profiles.id"), nullable=True)
    location        = Column(String(255))
    employment_type = Column(String(50))
    salary_min      = Column(DECIMAL(12, 2))
    salary_max      = Column(DECIMAL(12, 2))
    experience_years= Column(Integer)
    required_skills = Column(Text)    # JSON
    jd_embedding    = Column(Text)    # JSON
    job_category    = Column(String(20), default="technical")  # technical | non_technical | mixed
    status          = Column(String(20), default="draft")   # draft | pending_approval | active | closed
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company     = relationship("Company", back_populates="job_postings")
    department  = relationship("Department", back_populates="job_postings")
    created_by  = relationship("User", back_populates="job_postings")
    hr          = relationship("HRProfile", back_populates="job_postings")
    interviewer = relationship("InterviewerProfile", foreign_keys=[interviewer_id],
                               backref="assigned_jobs")
    candidates  = relationship("Candidate", back_populates="job")
    ats_results = relationship("ATSResult", back_populates="job")
    offers      = relationship("Offer", back_populates="job")


# ═══════════════════════════════════════════════════════
# CANDIDATES
# ═══════════════════════════════════════════════════════

class Candidate(Base, TenantScoped):
    __tablename__ = "candidates"

    id               = Column(String(36), primary_key=True, default=_uuid)
    company_id       = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    user_id          = Column(String(36), ForeignKey("users.id"))
    job_id           = Column(String(36), ForeignKey("job_postings.id"))
    # Indexed because the talent pool matches account-less candidates to a pool row
    # by email. The utf8mb4_unicode_ci collation compares case-insensitively, so a
    # plain == is both correct and index-friendly — never wrap this in LOWER().
    email            = Column(String(255), nullable=False, index=True)
    first_name       = Column(String(255), nullable=False)
    last_name        = Column(String(255), nullable=False)
    phone            = Column(String(50))
    resume_path      = Column(String(500))
    resume_text      = Column(Text)
    extracted_skills = Column(Text)     # JSON
    experience_years = Column(Integer)
    current_stage    = Column(String(50), default="applied")
    ats_score        = Column(DECIMAL(5, 2))
    current_salary   = Column(DECIMAL(12, 2))
    expected_salary  = Column(DECIMAL(12, 2))
    notice_period    = Column(String(50))
    linkedin_url     = Column(String(500))
    github_url       = Column(String(500))
    portfolio_url    = Column(String(500))
    additional_info  = Column(Text)
    photo_path       = Column(String(500), nullable=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user          = relationship("User", back_populates="candidates")
    job           = relationship("JobPosting", back_populates="candidates")
    ats_result    = relationship("ATSResult", back_populates="candidate", uselist=False)
    stage_history = relationship("CandidateStageHistory", back_populates="candidate")
    offers        = relationship("Offer", back_populates="candidate")


class ATSResult(Base, TenantScoped):
    __tablename__ = "ats_results"
    __table_args__ = (UniqueConstraint('candidate_id', 'job_id', name='uq_atsresult_candidate_job'),)

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    candidate_id       = Column(String(36), ForeignKey("candidates.id"))
    job_id             = Column(String(36), ForeignKey("job_postings.id"))
    skills_match_score = Column(DECIMAL(5, 2))
    semantic_score     = Column(DECIMAL(5, 2))
    experience_score   = Column(DECIMAL(5, 2))
    keyword_score      = Column(DECIMAL(5, 2))
    ats_score          = Column(DECIMAL(5, 2))
    matched_skills     = Column(Text)     # JSON
    missing_skills     = Column(Text)     # JSON
    scoring_breakdown  = Column(Text)     # JSON
    created_at         = Column(DateTime, default=datetime.utcnow)

    candidate = relationship("Candidate", back_populates="ats_result")
    job       = relationship("JobPosting", back_populates="ats_results")


class ResumePoolEntry(Base, TenantScoped):
    """A resume in the HR Resume Matcher pool.

    Unlike a Candidate, a pool entry is not tied to a job or a user account — it is
    a stored resume plus the profile we parsed out of it and the ranked list of jobs
    it was scored against. HR promotes one into a real Candidate ("Move to ATS"),
    after which `moved_candidate_id` links the two.

    Rows arrive two ways, told apart by `source`:
      * "upload"     — HR dropped a file on the Resume Matcher.
      * "registered" — imported from someone who registered on the candidate portal.
                       These point at a resume the CANDIDATE owns (or none at all),
                       so the file must never be deleted with the pool row.
    """
    __tablename__ = "resume_pool"
    __table_args__ = (
        # One pool row per registered person per company. This — not content_hash —
        # is what makes the import idempotent, because someone who registered but
        # never uploaded anything has no text to hash. MySQL allows unlimited NULLs
        # in a unique index, so HR uploads (source_user_id IS NULL) are unaffected.
        UniqueConstraint("company_id", "source_user_id", name="uq_resume_pool_company_source_user"),
        # Same guard for people with no user account, who are keyed on the Candidate
        # row they were imported from instead.
        UniqueConstraint("company_id", "source_candidate_id",
                         name="uq_resume_pool_company_source_candidate"),
    )

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    uploaded_by_id     = Column(String(36), ForeignKey("users.id"))

    filename           = Column(String(500))              # original upload name
    resume_path        = Column(String(500))              # storage key: resumes/pool/<uuid>.pdf
    resume_text        = Column(Text)
    content_hash       = Column(String(64), index=True)   # sha256 — re-upload dedupe

    # Parsed profile. All nullable: the parser returns None rather than guessing,
    # and HR corrects anything wrong in the Move-to-ATS modal.
    full_name          = Column(String(255))
    email              = Column(String(255), index=True)
    phone              = Column(String(50))
    college            = Column(String(255), index=True)
    year_of_passing    = Column(Integer, index=True)
    job_role           = Column(String(255), index=True)  # most recent / target role
    highest_degree     = Column(String(100))
    extracted_skills   = Column(Text)                     # JSON
    experience_years   = Column(DECIMAL(4, 2))

    # Match outcome
    best_job_id        = Column(String(36), ForeignKey("job_postings.id"), nullable=True)
    best_score         = Column(DECIMAL(5, 2), index=True)
    match_results      = Column(Text)                     # JSON: ranked jobs + ATS sub-scores

    # Applications whose job posting has since been hard-deleted. JSON list shaped like
    # PoolApplication: {candidate_id, job_id, job_title, stage, applied_at, ats_score}.
    #
    # The only field on this row deliberately allowed to go stale. Live applications are
    # read from `candidates` joined to `job_postings` on every request precisely so a
    # stored copy cannot drift — but a hard delete destroys both sides of that join, so
    # "which job did they apply for" has nowhere else to survive. A foreign key is not an
    # option: it would either be NULLed with best_job_id or block the delete (MySQL 1451).
    archived_applications = Column(Text)

    status             = Column(String(20), default="new")  # new | moved | archived
    # The Candidate this entry PRODUCED via Move to ATS. Kept distinct from
    # source_candidate_id below: this one also guards the stored file from deletion.
    moved_candidate_id = Column(String(36), ForeignKey("candidates.id"), nullable=True)

    # Provenance. source_candidate_id is the Candidate this entry was imported FROM,
    # which is why it must not be conflated with moved_candidate_id — an imported row
    # starts life un-promoted so HR can still move it onto a different job.
    source             = Column(String(20), default="upload", index=True)  # upload | registered | applied | manual
    source_user_id     = Column(String(36), ForeignKey("users.id"), nullable=True)
    source_candidate_id = Column(String(36), ForeignKey("candidates.id"), nullable=True)
    imported_at        = Column(DateTime, nullable=True)

    # ── Sourcing CRM ───────────────────────────────────────────────────────────
    # HR's own triage lane, moved by hand. Deliberately separate from the derived
    # `outcome` (hired / rejected / in process), which is factual ATS history and is
    # not HR's to set: someone rejected for one role can still sit in `ongoing` here
    # because they are worth keeping warm.
    stage              = Column(String(20), default="new", index=True)  # new | ongoing | archived
    notes              = Column(Text, nullable=True)
    last_contact_at    = Column(DateTime, nullable=True, index=True)
    # Where this person came from in the sourcing sense — LinkedIn, a referral, a job
    # board. Distinct from `source`, which records how the row entered the system.
    source_channel     = Column(String(100), nullable=True, index=True)

    # For imported rows this is when the person REGISTERED, so the existing date
    # filters and the desc(uploaded_at) tiebreak read as "how old is this candidate".
    uploaded_at        = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    uploaded_by      = relationship("User", foreign_keys=[uploaded_by_id])
    best_job         = relationship("JobPosting", foreign_keys=[best_job_id])
    moved_candidate  = relationship("Candidate", foreign_keys=[moved_candidate_id])
    source_user      = relationship("User", foreign_keys=[source_user_id])
    source_candidate = relationship("Candidate", foreign_keys=[source_candidate_id])
    attachments      = relationship("ResumePoolAttachment", back_populates="entry",
                                    cascade="all, delete-orphan")


class ResumePoolAttachment(Base, TenantScoped):
    """A file on a talent-pool profile — CV, portfolio, a photographed resume.

    Separate from `ResumePoolEntry.resume_path`, which stays the single *scored*
    resume. A profile can carry several documents; only one of them drives matching.
    """
    __tablename__ = "resume_pool_attachments"

    id            = Column(String(36), primary_key=True, default=_uuid)
    company_id    = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    entry_id      = Column(String(36), ForeignKey("resume_pool.id"), nullable=False, index=True)

    filename      = Column(String(500))          # original name, shown to HR
    file_path     = Column(String(500))          # storage key under resumes/pool
    content_type  = Column(String(100))
    size_bytes    = Column(Integer)
    # Images attach and display but cannot be ATS-scored without OCR.
    is_scoreable  = Column(Boolean, default=True)

    uploaded_by_id = Column(String(36), ForeignKey("users.id"), nullable=True)
    uploaded_at    = Column(DateTime, default=datetime.utcnow)

    entry       = relationship("ResumePoolEntry", back_populates="attachments")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])


class CandidateStageHistory(Base, TenantScoped):
    __tablename__ = "candidate_stage_history"

    id             = Column(String(36), primary_key=True, default=_uuid)
    company_id     = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    candidate_id   = Column(String(36), ForeignKey("candidates.id"))
    from_stage     = Column(String(50))
    to_stage       = Column(String(50), nullable=False)
    changed_by_id  = Column(String(36), ForeignKey("users.id"))
    notes          = Column(Text)
    created_at     = Column(DateTime, default=datetime.utcnow)

    candidate  = relationship("Candidate", back_populates="stage_history")
    changed_by = relationship("User")


# ═══════════════════════════════════════════════════════
# INTERVIEWS & OFFERS
# ═══════════════════════════════════════════════════════

class Interview(Base, TenantScoped):
    __tablename__ = "interviews"

    id                      = Column(Integer, primary_key=True, autoincrement=True)
    company_id              = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    title                   = Column(String(255), nullable=True)
    description             = Column(Text, nullable=True)
    job_position            = Column(String(255), nullable=True)
    job_title               = Column(String(255), nullable=True)
    job_level               = Column(String(50), nullable=True)
    role_type               = Column(String(50), nullable=True)
    job_details_data        = Column(Text, nullable=True)
    interview_type          = Column(String(50), nullable=True)
    difficulty_level        = Column(String(20), nullable=True)
    scheduled_datetime      = Column(DateTime, nullable=True)
    duration_minutes        = Column(Integer, nullable=True)
    interview_config_data   = Column(Text, nullable=True)
    skills_config_data      = Column(Text, nullable=True)
    question_format_data    = Column(Text, nullable=True)
    evaluation_rules_data   = Column(Text, nullable=True)
    ai_generation_rules_data= Column(Text, nullable=True)
    security_settings_data  = Column(Text, nullable=True)
    interviewer_name        = Column(String(255), nullable=True)
    interviewer_email       = Column(String(255), nullable=True)
    # Panel interview roster (JSON list of {"id","name","email"}); interviewer_name/
    # email hold the lead (panel[0]) so existing single-interviewer reads keep working.
    panel_members           = Column(Text, nullable=True)
    meeting_link            = Column(String(500), nullable=True)
    interviewer_details_data= Column(Text, nullable=True)
    status                  = Column(String(20), default="DRAFT")
    notes                   = Column(Text, nullable=True)
    created_by              = Column(String(255), nullable=True)
    job_id                  = Column(String(36), ForeignKey("job_postings.id"), nullable=True)
    candidate_id            = Column(String(36), ForeignKey("candidates.id"), nullable=True)
    assigned_hr_id          = Column(String(36), ForeignKey("hr_profiles.id"), nullable=True)
    published_at            = Column(DateTime, nullable=True)
    test_score              = Column(Float, nullable=True)
    total_marks             = Column(Integer, nullable=True)
    answers_json            = Column(Text, nullable=True)
    num_sets                = Column(Integer, default=1)
    skill_type_targets_data  = Column(Text, nullable=True)
    rating                   = Column(Integer, nullable=True)
    is_scheduled_interview   = Column(Boolean, default=False, nullable=False)
    # Two-round test support: a per-candidate test belongs to round 1 or 2.
    # round_type is "mcq" (round 1) / "coding" (round 2); NULL = combined single-round test.
    test_round               = Column(Integer, default=1, nullable=True)
    round_type               = Column(String(20), nullable=True)
    created_at               = Column(DateTime, default=datetime.utcnow)
    updated_at               = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    interview_feedback = relationship("InterviewFeedback", back_populates="interview", uselist=False)
    job        = relationship("JobPosting", foreign_keys=[job_id])
    candidate  = relationship("Candidate", foreign_keys=[candidate_id])
    assigned_hr= relationship("HRProfile", foreign_keys=[assigned_hr_id])


class InterviewFeedback(Base, TenantScoped):
    __tablename__ = "interview_feedback"

    id                    = Column(String(36), primary_key=True, default=_uuid)
    company_id            = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    interview_id          = Column(Integer, ForeignKey("interviews.id"), unique=True)
    interviewer_id        = Column(String(36), ForeignKey("interviewer_profiles.id"))
    technical_score       = Column(DECIMAL(5, 2))
    communication_score   = Column(DECIMAL(5, 2))
    problem_solving_score = Column(DECIMAL(5, 2))
    cultural_fit_score    = Column(DECIMAL(5, 2))
    overall_score         = Column(DECIMAL(5, 2))
    strengths             = Column(Text)     # JSON
    weaknesses            = Column(Text)     # JSON
    recommendations       = Column(Text)
    hire_recommendation   = Column(String(50))
    additional_notes      = Column(Text)
    created_at            = Column(DateTime, default=datetime.utcnow)

    interview   = relationship("Interview", back_populates="interview_feedback")
    interviewer = relationship("InterviewerProfile")


class Offer(Base, TenantScoped):
    __tablename__ = "offers"

    id           = Column(String(36), primary_key=True, default=_uuid)
    company_id   = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    candidate_id = Column(String(36), ForeignKey("candidates.id"))
    job_id       = Column(String(36), ForeignKey("job_postings.id"))
    hr_id        = Column(String(36), ForeignKey("hr_profiles.id"))
    offer_type   = Column(String(50))
    salary       = Column(DECIMAL(12, 2), nullable=False)
    bonus        = Column(DECIMAL(12, 2))
    start_date   = Column(Date, nullable=False)
    expiry_date  = Column(Date, nullable=False)
    status       = Column(String(20), default="draft")
    terms        = Column(Text)
    created_at   = Column(DateTime, default=datetime.utcnow)
    sent_at      = Column(DateTime)
    responded_at = Column(DateTime)

    candidate = relationship("Candidate", back_populates="offers")
    job       = relationship("JobPosting", back_populates="offers")
    hr        = relationship("HRProfile", back_populates="offers")


# ═══════════════════════════════════════════════════════
# REQUESTS & NOTIFICATIONS
# ═══════════════════════════════════════════════════════

class CandidateRequest(Base, TenantScoped):
    __tablename__ = "candidate_requests"

    id                    = Column(String(36), primary_key=True, default=_uuid)
    company_id            = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    requested_by          = Column(String(36), ForeignKey("users.id"), nullable=False)
    job_id                = Column(String(36), ForeignKey("job_postings.id"), nullable=True)
    job_title             = Column(String(255), nullable=False)
    department            = Column(String(255), nullable=True)
    role_level            = Column(String(50), nullable=True)
    employment_type       = Column(String(50), nullable=True)
    num_candidates        = Column(Integer, default=1)
    required_skills       = Column(Text, nullable=True)     # JSON
    preferred_skills      = Column(Text, nullable=True)     # JSON
    min_experience_years  = Column(Integer, default=0)
    max_experience_years  = Column(Integer, nullable=True)
    education_requirement = Column(String(255), nullable=True)
    job_description       = Column(Text, nullable=True)
    job_category          = Column(String(20), default="technical")  # technical | non_technical | mixed
    urgency               = Column(String(20), default="normal")
    notes                 = Column(Text, nullable=True)
    status                = Column(String(20), default="pending")
    hr_notes              = Column(Text, nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    requester = relationship("User", foreign_keys=[requested_by])


class JDTemplate(Base, TenantScoped):
    __tablename__ = "jd_templates"

    id                    = Column(String(36), primary_key=True, default=_uuid)
    company_id            = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    created_by            = Column(String(36), ForeignKey("users.id"), nullable=True)
    name                  = Column(String(255), nullable=False)
    category              = Column(String(100), nullable=True)
    job_category          = Column(String(20), default="technical")  # technical | non_technical | mixed
    role_level            = Column(String(50), nullable=True)
    employment_type       = Column(String(50), nullable=True)
    min_experience_years  = Column(Integer, default=0)
    required_skills       = Column(Text, nullable=True)   # JSON
    preferred_skills      = Column(Text, nullable=True)   # JSON
    job_description       = Column(Text, nullable=True)
    is_builtin            = Column(Boolean, default=False)
    is_shared             = Column(Boolean, default=True)
    source                = Column(String(20), default="manual")   # manual | ai | builtin
    created_at            = Column(DateTime, default=datetime.utcnow)
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", foreign_keys=[created_by])


class MailTemplate(Base, TenantScoped):
    """Reusable subject/body templates for HR's outgoing mail.

    Previously these lived in each browser's localStorage, so a company's templates
    were invisible to every other HR user and lost when the browser was cleared.
    """
    __tablename__ = "mail_templates"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_mail_tpl_company_name"),
    )

    id         = Column(String(36), primary_key=True, default=_uuid)
    company_id = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    created_by = Column(String(36), ForeignKey("users.id"), nullable=True)
    # offer | rejection | interview_invite | welcome | custom — matches the stage
    # filter the HR dashboard already renders.
    stage      = Column(String(50), default="custom")
    name       = Column(String(255), nullable=False)
    subject    = Column(String(500), nullable=True)
    body       = Column(Text, nullable=True)
    # Seeded starter template rather than one HR wrote. Seeded per company, not as
    # company_id=NULL: the tenant read filter compares company_id to the current
    # company, which excludes NULL, so NULL-tenant rows would be invisible.
    is_builtin = Column(Boolean, default=False)
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    creator = relationship("User", foreign_keys=[created_by])


class Notification(Base, TenantScoped):
    __tablename__ = "notifications"

    id             = Column(String(36), primary_key=True, default=_uuid)
    company_id     = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    user_id        = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    title          = Column(String(255), nullable=False)
    message        = Column(Text, nullable=False)
    type           = Column(String(50), default="info")
    reference_type = Column(String(50), nullable=True)
    reference_id   = Column(String(36), nullable=True)
    is_read        = Column(Boolean, default=False)
    created_at     = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")


class SupportRequest(Base, TenantScoped):
    """Client admin → superadmin help/support ticket."""
    __tablename__ = "support_requests"

    id           = Column(String(36), primary_key=True, default=_uuid)
    company_id   = Column(String(36), ForeignKey("companies.id"), nullable=False, index=True)
    created_by   = Column(String(36), ForeignKey("users.id"), nullable=False)   # client admin
    assigned_to  = Column(String(36), ForeignKey("users.id"), nullable=True)    # superadmin who claimed
    subject      = Column(String(255), nullable=False)
    category     = Column(String(50), default="general")    # general|billing|technical|feature|other
    priority     = Column(String(20), default="normal")     # low|normal|high|urgent
    status       = Column(String(20), default="open", index=True)   # open|in_progress|resolved|closed
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    resolved_at  = Column(DateTime, nullable=True)

    company  = relationship("Company")
    creator  = relationship("User", foreign_keys=[created_by])
    assignee = relationship("User", foreign_keys=[assigned_to])
    messages = relationship(
        "SupportMessage",
        back_populates="request",
        order_by="SupportMessage.created_at",
        cascade="all, delete-orphan",
    )


class SupportMessage(Base, TenantScoped):
    """One message in a SupportRequest thread (admin or superadmin)."""
    __tablename__ = "support_messages"

    id          = Column(String(36), primary_key=True, default=_uuid)
    company_id  = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    request_id  = Column(String(36), ForeignKey("support_requests.id"), nullable=False, index=True)
    sender_id   = Column(String(36), ForeignKey("users.id"), nullable=False)
    sender_role = Column(String(20), nullable=False)    # snapshot of role at send time
    body        = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)

    request = relationship("SupportRequest", back_populates="messages")
    sender  = relationship("User")


# ═══════════════════════════════════════════════════════
# EXTENDED CANDIDATE PROFILE
# Preferences and Links columns merged in (no child tables for 1-to-1 data)
# ═══════════════════════════════════════════════════════

class CandidateProfileExt(Base, TenantScoped):
    __tablename__ = "candidate_profile_ext"

    id                        = Column(String(36), primary_key=True, default=_uuid)
    company_id                = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    user_id                   = Column(String(36), ForeignKey("users.id"), unique=True, nullable=False)

    # Personal
    alternate_phone           = Column(String(20), nullable=True)
    nationality               = Column(String(100), nullable=True)
    marital_status            = Column(String(50), nullable=True)
    languages                 = Column(Text, nullable=True)       # JSON
    profile_photo_path        = Column(String(500), nullable=True)

    # Location — Current Address
    current_address_line      = Column(String(255), nullable=True)
    current_city              = Column(String(100), nullable=True)
    state                     = Column(String(100), nullable=True)   # current state
    country                   = Column(String(100), nullable=True)   # current country
    pincode                   = Column(String(20), nullable=True)    # current pincode
    # Location — Permanent Address
    permanent_address_line    = Column(String(255), nullable=True)
    permanent_city            = Column(String(100), nullable=True)
    permanent_state           = Column(String(100), nullable=True)
    permanent_country         = Column(String(100), nullable=True)
    permanent_pincode         = Column(String(20), nullable=True)
    permanent_same_as_current = Column(Boolean, default=False)
    willing_to_relocate       = Column(Boolean, default=False)
    preferred_locations       = Column(Text, nullable=True)       # JSON (also used as preferences)

    # Professional summary
    headline                  = Column(String(120), nullable=True)
    about                     = Column(Text, nullable=True)
    total_experience_years    = Column(Float, nullable=True)
    current_employment_status = Column(String(50), nullable=True)
    notice_period             = Column(String(50), nullable=True)
    open_to_work              = Column(Boolean, default=False)

    # Documents
    resume_path               = Column(String(500), nullable=True)
    cover_letter_path         = Column(String(500), nullable=True)
    portfolio_file_path       = Column(String(500), nullable=True)

    # ── Merged from CandidatePreferences ──
    preferred_job_roles       = Column(Text, nullable=True)       # JSON
    preferred_industries      = Column(Text, nullable=True)       # JSON
    preferred_job_type        = Column(String(50), nullable=True)
    work_mode_preference      = Column(String(50), nullable=True)
    expected_salary_min       = Column(DECIMAL(12, 2), nullable=True)
    expected_salary_max       = Column(DECIMAL(12, 2), nullable=True)
    salary_currency           = Column(String(10), default="INR")
    salary_type               = Column(String(20), default="Annual")

    # ── Merged from CandidateLinks ──
    linkedin_url              = Column(String(500), nullable=True)
    github_url                = Column(String(500), nullable=True)
    portfolio_url             = Column(String(500), nullable=True)
    twitter_url               = Column(String(500), nullable=True)
    behance_url               = Column(String(500), nullable=True)
    dribbble_url              = Column(String(500), nullable=True)
    other_url                 = Column(String(500), nullable=True)
    other_url_label           = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user             = relationship("User", backref="profile_ext")
    work_experiences = relationship("CandidateWorkExperience", back_populates="profile",
                                    cascade="all, delete-orphan")
    educations       = relationship("CandidateEducation", back_populates="profile",
                                    cascade="all, delete-orphan")
    skills           = relationship("CandidateSkill", back_populates="profile",
                                    cascade="all, delete-orphan")
    certifications   = relationship("CandidateCertification", back_populates="profile",
                                    cascade="all, delete-orphan")
    projects         = relationship("CandidateProject", back_populates="profile",
                                    cascade="all, delete-orphan")


class CandidateWorkExperience(Base, TenantScoped):
    __tablename__ = "candidate_work_experience"

    id                = Column(String(36), primary_key=True, default=_uuid)
    company_id        = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    profile_id        = Column(String(36), ForeignKey("candidate_profile_ext.id"), nullable=False)
    job_title         = Column(String(255), nullable=False)
    company_name      = Column(String(255), nullable=False)
    industry          = Column(String(100), nullable=True)
    employment_type   = Column(String(50), nullable=True)
    location          = Column(String(100), nullable=True)
    start_date        = Column(String(7), nullable=True)
    end_date          = Column(String(7), nullable=True)
    currently_working = Column(Boolean, default=False)
    description       = Column(Text, nullable=True)
    key_achievements  = Column(Text, nullable=True)
    created_at        = Column(DateTime, default=datetime.utcnow)

    profile = relationship("CandidateProfileExt", back_populates="work_experiences")


class CandidateEducation(Base, TenantScoped):
    __tablename__ = "candidate_education"

    id                  = Column(String(36), primary_key=True, default=_uuid)
    company_id          = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    profile_id          = Column(String(36), ForeignKey("candidate_profile_ext.id"), nullable=False)
    education_level     = Column(String(50), nullable=True)
    degree              = Column(String(100), nullable=True)
    field_of_study      = Column(String(100), nullable=True)
    institution_name    = Column(String(255), nullable=False)
    university_or_board = Column(String(255), nullable=True)
    start_year          = Column(Integer, nullable=True)
    end_year            = Column(Integer, nullable=True)
    currently_pursuing  = Column(Boolean, default=False)
    grade               = Column(String(50), nullable=True)
    description         = Column(Text, nullable=True)
    created_at          = Column(DateTime, default=datetime.utcnow)

    profile = relationship("CandidateProfileExt", back_populates="educations")


class CandidateSkill(Base, TenantScoped):
    __tablename__ = "candidate_skills"

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    profile_id         = Column(String(36), ForeignKey("candidate_profile_ext.id"), nullable=False)
    skill_name         = Column(String(100), nullable=False)
    proficiency_level  = Column(String(50), nullable=True)
    years_of_experience= Column(Float, nullable=True)
    category           = Column(String(50), nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)

    profile = relationship("CandidateProfileExt", back_populates="skills")


class CandidateCertification(Base, TenantScoped):
    __tablename__ = "candidate_certifications"

    id                    = Column(String(36), primary_key=True, default=_uuid)
    company_id            = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    profile_id            = Column(String(36), ForeignKey("candidate_profile_ext.id"), nullable=False)
    certificate_name      = Column(String(255), nullable=False)
    issuing_organization  = Column(String(255), nullable=True)
    issue_date            = Column(String(7), nullable=True)
    expiry_date           = Column(String(7), nullable=True)
    does_not_expire       = Column(Boolean, default=False)
    credential_id         = Column(String(255), nullable=True)
    credential_url        = Column(String(500), nullable=True)
    certificate_file_path = Column(String(500), nullable=True)
    created_at            = Column(DateTime, default=datetime.utcnow)

    profile = relationship("CandidateProfileExt", back_populates="certifications")


class CandidateProject(Base, TenantScoped):
    __tablename__ = "candidate_projects"

    id                 = Column(String(36), primary_key=True, default=_uuid)
    company_id         = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    profile_id         = Column(String(36), ForeignKey("candidate_profile_ext.id"), nullable=False)
    project_title      = Column(String(255), nullable=False)
    project_type       = Column(String(50), nullable=True)
    description        = Column(Text, nullable=True)
    technologies_used  = Column(Text, nullable=True)   # JSON
    role               = Column(String(100), nullable=True)
    start_date         = Column(String(7), nullable=True)
    end_date           = Column(String(7), nullable=True)
    ongoing            = Column(Boolean, default=False)
    project_url        = Column(String(500), nullable=True)
    repository_url     = Column(String(500), nullable=True)
    created_at         = Column(DateTime, default=datetime.utcnow)

    profile = relationship("CandidateProfileExt", back_populates="projects")


# ═══════════════════════════════════════════════════════
# PROCTORING
# CheatingLog and TabSwitchLog removed — proctoring pipeline
# writes all events into proctoring_violations with typed violation_type
# ═══════════════════════════════════════════════════════

class ProctoringSession(Base, TenantScoped):
    __tablename__ = "proctoring_sessions"

    id                    = Column(String(36), primary_key=True, default=_uuid)
    company_id            = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    candidate_id          = Column(String(36), ForeignKey("users.id"), nullable=False)
    test_id               = Column(Integer, ForeignKey("interviews.id"), nullable=False)
    status                = Column(String(20), default="active")
    started_at            = Column(DateTime, default=datetime.utcnow)
    ended_at              = Column(DateTime, nullable=True)
    cheating_score        = Column(Float, default=0.0)
    face_score            = Column(Float, default=0.0)
    object_score          = Column(Float, default=0.0)
    audio_score           = Column(Float, default=0.0)
    pose_score            = Column(Float, default=0.0)
    behavioral_score      = Column(Float, default=0.0)
    decision              = Column(String(20), nullable=True)
    max_cheating_score    = Column(Float, nullable=True)
    total_violations      = Column(Integer, nullable=True)
    total_frames_processed= Column(Integer, nullable=True)
    face_registered       = Column(Boolean, default=False)
    face_photo_path       = Column(String(500), nullable=True)
    baseline_captured     = Column(Boolean, default=False)

    violations  = relationship("ProctoringViolation", back_populates="session",
                               cascade="all, delete-orphan")
    transcripts = relationship("ProctoringTranscript", back_populates="session",
                               cascade="all, delete-orphan")


class ProctoringViolation(Base, TenantScoped):
    __tablename__ = "proctoring_violations"

    id             = Column(String(36), primary_key=True, default=_uuid)
    company_id     = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    session_id     = Column(String(36), ForeignKey("proctoring_sessions.id"), nullable=False)
    timestamp      = Column(DateTime, default=datetime.utcnow)
    violation_type = Column(String(50), nullable=False)
    severity       = Column(String(20), nullable=False)
    module         = Column(String(30), nullable=False)
    details        = Column(JSON, nullable=True)
    evidence_path  = Column(String(500), nullable=True)
    score_impact   = Column(Float, default=0.0)
    # Extra fields for cheating-frame and tab-switch events
    frame_number   = Column(Integer, nullable=True)
    module_scores  = Column(JSON, nullable=True)
    flags          = Column(JSON, nullable=True)
    switch_count   = Column(Integer, nullable=True)
    cumulative_count= Column(Integer, nullable=True)

    session = relationship("ProctoringSession", back_populates="violations")


class ProctoringTranscript(Base, TenantScoped):
    __tablename__ = "proctoring_transcripts"

    id               = Column(String(36), primary_key=True, default=_uuid)
    company_id       = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    session_id       = Column(String(36), ForeignKey("proctoring_sessions.id"), nullable=False)
    timestamp        = Column(DateTime, default=datetime.utcnow)
    text             = Column(Text, nullable=False)
    language         = Column(String(10), default="en")
    flagged_keywords = Column(JSON, nullable=True)

    session = relationship("ProctoringSession", back_populates="transcripts")


# ═══════════════════════════════════════════════════════
# SYSTEM
# ═══════════════════════════════════════════════════════

class SystemConfig(Base):
    __tablename__ = "system_config"

    id           = Column(String(36), primary_key=True, default=_uuid)
    config_key   = Column(String(255), unique=True, nullable=False)
    config_value = Column(Text, nullable=False)
    description  = Column(Text)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ═══════════════════════════════════════════════════════
# CODING-JUDGE MODELS (coding_problems, problem_test_cases,
# code_submissions, submission_results)
#
# Imported here so they register on this module's ``Base`` before
# ``Base.metadata.create_all()`` runs in init_db(), and so alembic
# autogenerate picks them up. Placed at the bottom to avoid any
# import cycle (Base / _uuid / TenantScoped are all defined above).
# ═══════════════════════════════════════════════════════
from .judge_models import (  # noqa: E402,F401
    CodingProblem,
    ProblemTestCase,
    CodeSubmission,
    SubmissionTestResult,
    InterviewCodeSubmission,
    JudgeRuntimeState,
)
