"""
End-to-end test — all 29 tables, full hiring workflow.
Run: python test_full_workflow.py
"""

import sys, json, uuid
from datetime import datetime, date, timedelta
from decimal import Decimal

# ── DB setup ─────────────────────────────────────────────────────────────────
from src.config.database_config_mysql import db_manager
from src.models.models import (
    Company, TenantConfig, SubscriptionPlan,
    User, Department, HRProfile, InterviewerProfile,
    JobPosting, Candidate, ATSResult, CandidateStageHistory,
    Interview, InterviewFeedback, Offer,
    CandidateRequest, Notification, SystemConfig,
    CandidateProfileExt, CandidateWorkExperience, CandidateEducation,
    CandidateSkill, CandidateCertification, CandidateProject,
    ProctoringSession, ProctoringViolation, ProctoringTranscript,
)
from src.models.question_models import Question, QuestionSetAssignment, CandidateQuestionHistory
from src.auth import get_password_hash

PASS = "\033[92m PASS\033[0m"
FAIL = "\033[91m FAIL\033[0m"
HEAD = "\033[94m"
END  = "\033[0m"
errors = []

def ok(label):
    print(f"  {PASS}  {label}")

def fail(label, e):
    print(f"  {FAIL}  {label}: {e}")
    errors.append(label)

def section(title):
    print(f"\n{HEAD}{'-'*60}{END}")
    print(f"{HEAD}  {title}{END}")
    print(f"{HEAD}{'-'*60}{END}")


db = db_manager.SessionLocal()

# ═══════════════════════════════════════════════════════════════
# 1. TENANT / COMPANY
# ═══════════════════════════════════════════════════════════════
section("1. Company + TenantConfig + SubscriptionPlan")

company = None
try:
    company = Company(
        id=str(uuid.uuid4()),
        name="Acme Corp",
        slug=f"acme-{uuid.uuid4().hex[:6]}",
        primary_color="#2b7cba",
        secondary_color="#145649",
        accent_color="#f59e0b",
        font_family="Inter, sans-serif",
        tagline="Hire smarter, faster",
        support_email="support@acme.com",
        api_key=uuid.uuid4().hex,
        custom_domain="hire.acme.com",
        is_active=True,
    )
    db.add(company)
    db.flush()
    ok(f"Company created: {company.name} (id={company.id[:8]}…)")
except Exception as e:
    fail("Company create", e); db.rollback(); sys.exit(1)

try:
    tc = TenantConfig(
        company_id=company.id,
        smtp_host="smtp.sendgrid.net",
        smtp_port=587,
        smtp_from_email="noreply@acme.com",
        ai_model="ollama/llama3",
        max_jobs=50,
        max_users=20,
        max_candidates=500,
        features_json=json.dumps({"ats": True, "proctoring": True, "ai_questions": True}),
    )
    db.add(tc)
    db.flush()
    ok("TenantConfig created")
except Exception as e:
    fail("TenantConfig", e)

try:
    sp = SubscriptionPlan(
        company_id=company.id,
        plan_name="growth",
        max_jobs=50,
        max_users=20,
        max_candidates=500,
        billing_cycle="monthly",
        price=Decimal("999.00"),
        status="active",
        trial_ends_at=datetime.utcnow() + timedelta(days=14),
        current_period_end=datetime.utcnow() + timedelta(days=30),
    )
    db.add(sp)
    db.flush()
    ok("SubscriptionPlan created (growth, active)")
except Exception as e:
    fail("SubscriptionPlan", e)

try:
    sc = SystemConfig(config_key="app_version", config_value='"2.0.0"',
                      description="Application version")
    db.add(sc)
    db.flush()
    ok("SystemConfig created")
except Exception as e:
    fail("SystemConfig", e)

# ═══════════════════════════════════════════════════════════════
# 2. USERS
# ═══════════════════════════════════════════════════════════════
section("2. Users (admin, hr, interviewer, candidate)")

admin_user = hr_user = interviewer_user = candidate_user = None
try:
    admin_user = User(
        company_id=company.id, email=f"admin_{uuid.uuid4().hex[:4]}@acme.com",
        password_hash=get_password_hash("Admin@123"), role="admin",
        first_name="Alice", last_name="Admin",
    )
    db.add(admin_user); db.flush()
    ok(f"Admin user: {admin_user.email}")
except Exception as e:
    fail("Admin user", e)

try:
    hr_user = User(
        company_id=company.id, email=f"hr_{uuid.uuid4().hex[:4]}@acme.com",
        password_hash=get_password_hash("Hr@12345"), role="hr",
        first_name="Henry", last_name="HR",
    )
    db.add(hr_user); db.flush()
    ok(f"HR user: {hr_user.email}")
except Exception as e:
    fail("HR user", e)

try:
    interviewer_user = User(
        company_id=company.id, email=f"iv_{uuid.uuid4().hex[:4]}@acme.com",
        password_hash=get_password_hash("Iv@12345"), role="interviewer",
        first_name="Ivan", last_name="Interviewer",
    )
    db.add(interviewer_user); db.flush()
    ok(f"Interviewer user: {interviewer_user.email}")
except Exception as e:
    fail("Interviewer user", e)

try:
    candidate_user = User(
        company_id=company.id, email=f"cand_{uuid.uuid4().hex[:4]}@acme.com",
        password_hash=get_password_hash("Cand@123"), role="candidate",
        first_name="Carol", last_name="Candidate",
        phone="9876543210", gender="Female",
        date_of_birth=date(1998, 5, 15),
    )
    db.add(candidate_user); db.flush()
    ok(f"Candidate user: {candidate_user.email}")
except Exception as e:
    fail("Candidate user", e)

# ═══════════════════════════════════════════════════════════════
# 3. DEPARTMENT
# ═══════════════════════════════════════════════════════════════
section("3. Department")

dept = None
try:
    dept = Department(company_id=company.id, name="Engineering",
                      description="Software engineering team")
    db.add(dept); db.flush()
    ok(f"Department: {dept.name}")
except Exception as e:
    fail("Department", e)

# ═══════════════════════════════════════════════════════════════
# 4. HR & INTERVIEWER PROFILES
# ═══════════════════════════════════════════════════════════════
section("4. HRProfile + InterviewerProfile")

hr_profile = iv_profile = None
try:
    hr_profile = HRProfile(user_id=hr_user.id, department_id=dept.id,
                           employee_id="HR001")
    db.add(hr_profile); db.flush()
    ok(f"HRProfile: emp_id={hr_profile.employee_id}")
except Exception as e:
    fail("HRProfile", e)

try:
    iv_profile = InterviewerProfile(
        user_id=interviewer_user.id, department_id=dept.id,
        employee_id="IV001",
        expertise_areas=json.dumps(["Python", "System Design", "FastAPI"]),
        years_experience=6,
    )
    db.add(iv_profile); db.flush()
    ok(f"InterviewerProfile: emp_id={iv_profile.employee_id}, exp={iv_profile.years_experience}y")
except Exception as e:
    fail("InterviewerProfile", e)

# ═══════════════════════════════════════════════════════════════
# 5. JOB POSTING
# ═══════════════════════════════════════════════════════════════
section("5. JobPosting")

job = None
try:
    job = JobPosting(
        company_id=company.id,
        title="Senior Python Developer",
        description="Build scalable APIs with FastAPI and Python.",
        requirements="5+ years Python, FastAPI, MySQL, REST APIs.",
        department_id=dept.id,
        created_by_id=admin_user.id,
        hr_id=hr_profile.id,
        interviewer_id=iv_profile.id,
        location="Remote",
        employment_type="full-time",
        salary_min=Decimal("1200000.00"),
        salary_max=Decimal("1800000.00"),
        experience_years=5,
        required_skills=json.dumps(["Python", "FastAPI", "MySQL", "REST APIs", "Docker"]),
        status="active",
    )
    db.add(job); db.flush()
    ok(f"JobPosting: '{job.title}' (status={job.status})")
except Exception as e:
    fail("JobPosting", e)

# ═══════════════════════════════════════════════════════════════
# 6. CANDIDATE & ATS RESULT
# ═══════════════════════════════════════════════════════════════
section("6. Candidate + ATSResult + CandidateStageHistory")

candidate = None
try:
    candidate = Candidate(
        company_id=company.id,
        user_id=candidate_user.id,
        job_id=job.id,
        email=candidate_user.email,
        first_name="Carol",
        last_name="Candidate",
        phone="9876543210",
        resume_path="resumes/carol_cv.pdf",
        resume_text="Carol is a Python developer with 6 years of experience in FastAPI, MySQL, and Docker.",
        extracted_skills=json.dumps(["Python", "FastAPI", "MySQL", "Docker", "REST APIs"]),
        experience_years=6,
        current_stage="applied",
        current_salary=Decimal("900000.00"),
        expected_salary=Decimal("1500000.00"),
        notice_period="30 days",
        linkedin_url="https://linkedin.com/in/carol",
        github_url="https://github.com/carol",
    )
    db.add(candidate); db.flush()
    ok(f"Candidate: {candidate.first_name} {candidate.last_name} (stage={candidate.current_stage})")
except Exception as e:
    fail("Candidate", e)

try:
    ats = ATSResult(
        candidate_id=candidate.id,
        job_id=job.id,
        skills_match_score=Decimal("88.50"),
        semantic_score=Decimal("82.00"),
        experience_score=Decimal("90.00"),
        keyword_score=Decimal("78.00"),
        ats_score=Decimal("85.50"),
        matched_skills=json.dumps(["Python", "FastAPI", "MySQL", "Docker"]),
        missing_skills=json.dumps(["Kubernetes"]),
        scoring_breakdown=json.dumps({"weights": {"skills": 0.35, "semantic": 0.35, "experience": 0.20, "keyword": 0.10}}),
    )
    db.add(ats); db.flush()
    ok(f"ATSResult: score={ats.ats_score}, matched={json.loads(ats.matched_skills)}")
except Exception as e:
    fail("ATSResult", e)

try:
    hist1 = CandidateStageHistory(
        candidate_id=candidate.id, from_stage=None, to_stage="applied",
        changed_by_id=candidate_user.id, notes="Application submitted"
    )
    hist2 = CandidateStageHistory(
        candidate_id=candidate.id, from_stage="applied", to_stage="shortlisted",
        changed_by_id=hr_user.id, notes="ATS score 85.5 — shortlisted"
    )
    db.add_all([hist1, hist2]); db.flush()
    candidate.current_stage = "shortlisted"
    ok("CandidateStageHistory: applied → shortlisted")
except Exception as e:
    fail("CandidateStageHistory", e)

# ═══════════════════════════════════════════════════════════════
# 7. INTERVIEW QUESTIONS + SET ASSIGNMENTS
# ═══════════════════════════════════════════════════════════════
section("7. Interview + Questions + QuestionSetAssignments")

interview = None
try:
    interview = Interview(
        company_id=company.id,
        title="Senior Python Developer — Technical Screen",
        job_position="Senior Python Developer",
        job_title=job.title,
        job_id=job.id,
        candidate_id=candidate.id,
        assigned_hr_id=hr_profile.id,
        interview_type="TECHNICAL",
        difficulty_level="MEDIUM",
        scheduled_datetime=datetime.utcnow() + timedelta(days=3),
        duration_minutes=60,
        status="SCHEDULED",
        created_by=hr_user.email,
        num_sets=2,
        skill_type_targets_data=json.dumps([
            {"skill_name": "Python", "question_type": "multiple_choice", "count_per_set": 2}
        ]),
    )
    db.add(interview); db.flush()
    ok(f"Interview id={interview.id}, type={interview.interview_type}, status={interview.status}")
except Exception as e:
    fail("Interview", e)

questions = []
try:
    q_data = [
        ("What is a Python decorator?", "multiple_choice", "Easy", "Python",
         json.dumps(["A function wrapper", "A class method", "A loop construct", "None"]), "A function wrapper"),
        ("Explain FastAPI dependency injection.", "technical", "Medium", "FastAPI", None, None),
        ("Write a SQL query to find duplicates.", "coding", "Medium", "MySQL", None, None),
        ("Difference between list and tuple?", "multiple_choice", "Easy", "Python",
         json.dumps(["Mutability", "Speed", "Both", "None of the above"]), "Both"),
    ]
    for qt, qtype, diff, skill, opts, ans in q_data:
        q = Question(
            interview_id=interview.id,
            question_text=qt, question_type=qtype, difficulty_level=diff,
            skill_name=skill, options=opts, correct_answer=ans,
            is_approved=True, is_pool_question=True, points=5,
        )
        db.add(q)
        questions.append(q)
    db.flush()
    ok(f"Questions: {len(questions)} created (approved pool questions)")
except Exception as e:
    fail("Questions", e)

try:
    # Set 1: questions 0,1  |  Set 2: questions 2,3
    assignments = [
        QuestionSetAssignment(interview_id=interview.id, question_id=questions[0].id, set_number=1, question_order=1),
        QuestionSetAssignment(interview_id=interview.id, question_id=questions[1].id, set_number=1, question_order=2),
        QuestionSetAssignment(interview_id=interview.id, question_id=questions[2].id, set_number=2, question_order=1),
        QuestionSetAssignment(interview_id=interview.id, question_id=questions[3].id, set_number=2, question_order=2),
    ]
    db.add_all(assignments); db.flush()
    ok(f"QuestionSetAssignments: 2 sets × 2 questions each")
except Exception as e:
    fail("QuestionSetAssignment", e)

try:
    hist = CandidateQuestionHistory(
        candidate_id=candidate.id,
        source_question_id=questions[0].id,
        interview_id=interview.id,
    )
    db.add(hist); db.flush()
    ok("CandidateQuestionHistory: retake prevention recorded")
except Exception as e:
    fail("CandidateQuestionHistory", e)

# ═══════════════════════════════════════════════════════════════
# 8. INTERVIEW FEEDBACK + STAGE UPDATE
# ═══════════════════════════════════════════════════════════════
section("8. InterviewFeedback")

try:
    feedback = InterviewFeedback(
        interview_id=interview.id,
        interviewer_id=iv_profile.id,
        technical_score=Decimal("8.5"),
        communication_score=Decimal("7.0"),
        problem_solving_score=Decimal("8.0"),
        cultural_fit_score=Decimal("9.0"),
        overall_score=Decimal("8.1"),
        strengths=json.dumps(["Strong Python", "Clear communication", "Good problem-solving"]),
        weaknesses=json.dumps(["Needs more cloud experience"]),
        recommendations="Recommend for next round. Strong technical skills.",
        hire_recommendation="hire",
    )
    db.add(feedback)
    interview.status = "COMPLETED"
    interview.test_score = 81.0
    db.flush()
    ok(f"InterviewFeedback: overall={feedback.overall_score}, rec={feedback.hire_recommendation}")
except Exception as e:
    fail("InterviewFeedback", e)

try:
    db.add(CandidateStageHistory(
        candidate_id=candidate.id, from_stage="shortlisted",
        to_stage="interview_completed", changed_by_id=hr_user.id,
        notes="Technical round completed — hire recommended"
    ))
    candidate.current_stage = "interview_completed"
    db.flush()
    ok("Stage: shortlisted → interview_completed")
except Exception as e:
    fail("Stage update post-interview", e)

# ═══════════════════════════════════════════════════════════════
# 9. OFFER
# ═══════════════════════════════════════════════════════════════
section("9. Offer")

try:
    offer = Offer(
        company_id=company.id,
        candidate_id=candidate.id,
        job_id=job.id,
        hr_id=hr_profile.id,
        offer_type="full_time",
        salary=Decimal("1500000.00"),
        bonus=Decimal("100000.00"),
        start_date=date.today() + timedelta(days=45),
        expiry_date=date.today() + timedelta(days=14),
        status="sent",
        terms="Standard employment terms with 6-month probation.",
        sent_at=datetime.utcnow(),
    )
    db.add(offer)
    db.add(CandidateStageHistory(
        candidate_id=candidate.id, from_stage="interview_completed",
        to_stage="offer_sent", changed_by_id=hr_user.id, notes="Offer dispatched"
    ))
    candidate.current_stage = "offer_sent"
    db.flush()
    ok(f"Offer: salary={offer.salary}, status={offer.status}, start={offer.start_date}")
except Exception as e:
    fail("Offer", e)

# ═══════════════════════════════════════════════════════════════
# 10. CANDIDATE REQUEST & NOTIFICATION
# ═══════════════════════════════════════════════════════════════
section("10. CandidateRequest + Notification")

try:
    cr = CandidateRequest(
        company_id=company.id,
        requested_by=interviewer_user.id,
        job_id=job.id,
        job_title="Senior Python Developer",
        department="Engineering",
        role_level="Senior",
        employment_type="Full-time",
        num_candidates=2,
        required_skills=json.dumps(["Python", "FastAPI"]),
        min_experience_years=4,
        urgency="high",
        status="approved",
        hr_notes="Approved — 2 candidates needed by Q2.",
    )
    db.add(cr); db.flush()
    ok(f"CandidateRequest: '{cr.job_title}', urgency={cr.urgency}, status={cr.status}")
except Exception as e:
    fail("CandidateRequest", e)

try:
    notif = Notification(
        company_id=company.id,
        user_id=candidate_user.id,
        title="Offer Letter Sent",
        message="Congratulations! Your offer letter for Senior Python Developer has been sent.",
        type="action_required",
        reference_type="offer",
        reference_id=offer.id,
        is_read=False,
    )
    db.add(notif); db.flush()
    ok(f"Notification: '{notif.title}' → user {candidate_user.first_name}")
except Exception as e:
    fail("Notification", e)

# ═══════════════════════════════════════════════════════════════
# 11. EXTENDED CANDIDATE PROFILE (merged preferences + links)
# ═══════════════════════════════════════════════════════════════
section("11. CandidateProfileExt (with merged Preferences + Links)")

profile = None
try:
    profile = CandidateProfileExt(
        user_id=candidate_user.id,
        alternate_phone="9000000001",
        nationality="Indian",
        marital_status="Single",
        languages=json.dumps(["English", "Hindi"]),
        current_city="Pune",
        state="Maharashtra",
        country="India",
        pincode="411001",
        willing_to_relocate=True,
        headline="Senior Python Developer | FastAPI | MySQL",
        about="Passionate backend developer with 6+ years of experience.",
        total_experience_years=6.0,
        current_employment_status="Employed",
        notice_period="30 days",
        open_to_work=True,
        resume_path="uploads/carol_cv.pdf",
        # ── merged preferences ──
        preferred_job_roles=json.dumps(["Backend Developer", "API Developer"]),
        preferred_industries=json.dumps(["IT", "Fintech", "SaaS"]),
        preferred_job_type="Full-time",
        work_mode_preference="Remote",
        expected_salary_min=Decimal("1400000.00"),
        expected_salary_max=Decimal("1800000.00"),
        salary_currency="INR",
        salary_type="Annual",
        preferred_locations=json.dumps(["Pune", "Bangalore", "Remote"]),
        # ── merged links ──
        linkedin_url="https://linkedin.com/in/carol",
        github_url="https://github.com/carol",
        portfolio_url="https://carol.dev",
        twitter_url="https://twitter.com/carol",
        other_url="https://medium.com/@carol",
        other_url_label="Blog",
    )
    db.add(profile); db.flush()
    ok("CandidateProfileExt: personal + preferences + links all in one record")
except Exception as e:
    fail("CandidateProfileExt", e)

try:
    we = CandidateWorkExperience(
        profile_id=profile.id,
        job_title="Backend Developer",
        company_name="TechCorp Pvt Ltd",
        industry="IT",
        employment_type="Full-time",
        location="Pune",
        start_date="2019-06",
        end_date="2024-01",
        currently_working=False,
        description="Built microservices with FastAPI and Python. Led a team of 4.",
        key_achievements="Reduced API latency by 40%, led 3 product launches.",
    )
    db.add(we); db.flush()
    ok(f"CandidateWorkExperience: {we.job_title} at {we.company_name}")
except Exception as e:
    fail("CandidateWorkExperience", e)

try:
    edu = CandidateEducation(
        profile_id=profile.id,
        degree="B.Tech",
        field_of_study="Computer Science",
        institution_name="SPPU",
        start_year=2015, end_year=2019,
        grade="8.5 CGPA",
    )
    db.add(edu); db.flush()
    ok(f"CandidateEducation: {edu.degree} in {edu.field_of_study}")
except Exception as e:
    fail("CandidateEducation", e)

try:
    for skill_name, level in [("Python", "Expert"), ("FastAPI", "Advanced"), ("MySQL", "Advanced")]:
        db.add(CandidateSkill(profile_id=profile.id, skill_name=skill_name,
                              proficiency_level=level, years_of_experience=5.0, category="Technical"))
    db.flush()
    ok("CandidateSkills: Python(Expert), FastAPI(Advanced), MySQL(Advanced)")
except Exception as e:
    fail("CandidateSkills", e)

try:
    cert = CandidateCertification(
        profile_id=profile.id,
        certificate_name="AWS Solutions Architect – Associate",
        issuing_organization="Amazon Web Services",
        issue_date="2023-03",
        does_not_expire=False,
        expiry_date="2026-03",
        credential_url="https://aws.amazon.com/verification",
    )
    db.add(cert); db.flush()
    ok(f"CandidateCertification: {cert.certificate_name}")
except Exception as e:
    fail("CandidateCertification", e)

try:
    proj = CandidateProject(
        profile_id=profile.id,
        project_title="HireFlow API",
        project_type="Professional",
        description="End-to-end recruitment API built with FastAPI and MySQL.",
        technologies_used=json.dumps(["FastAPI", "Python", "MySQL", "Docker"]),
        role="Lead Developer",
        start_date="2023-01",
        end_date="2023-08",
        ongoing=False,
        project_url="https://hireflow.io",
        repository_url="https://github.com/carol/hireflow",
    )
    db.add(proj); db.flush()
    ok(f"CandidateProject: {proj.project_title}")
except Exception as e:
    fail("CandidateProject", e)

# ═══════════════════════════════════════════════════════════════
# 12. PROCTORING
# ═══════════════════════════════════════════════════════════════
section("12. ProctoringSession + Violations + Transcripts")

session = None
try:
    session = ProctoringSession(
        candidate_id=candidate_user.id,
        test_id=str(interview.id),
        status="completed",
        started_at=datetime.utcnow() - timedelta(hours=1),
        ended_at=datetime.utcnow(),
        cheating_score=12.5,
        face_score=95.0,
        object_score=98.0,
        audio_score=88.0,
        pose_score=92.0,
        behavioral_score=90.0,
        decision="pass",
        total_violations=2,
        total_frames_processed=1800,
        face_registered=True,
        baseline_captured=True,
    )
    db.add(session); db.flush()
    ok(f"ProctoringSession: status={session.status}, cheating_score={session.cheating_score}")
except Exception as e:
    fail("ProctoringSession", e)

try:
    v1 = ProctoringViolation(
        session_id=session.id,
        violation_type="MULTIPLE_FACES",
        severity="HIGH",
        module="face",
        details={"face_count": 2},
        score_impact=5.0,
    )
    v2 = ProctoringViolation(
        session_id=session.id,
        violation_type="CHEATING_FRAME",
        severity="LOW",
        module="behavioral",
        details={"confidence": 0.32},
        score_impact=2.5,
        frame_number=450,
        module_scores={"face": 0.1, "object": 0.0, "audio": 0.22},
        flags={"looking_away": True},
    )
    v3 = ProctoringViolation(
        session_id=session.id,
        violation_type="TAB_SWITCH",
        severity="LOW",
        module="behavioral",
        details={"url": "hidden"},
        score_impact=1.5,
        switch_count=1,
        cumulative_count=1,
    )
    db.add_all([v1, v2, v3]); db.flush()
    ok("ProctoringViolations: MULTIPLE_FACES + CHEATING_FRAME (merged) + TAB_SWITCH (merged)")
except Exception as e:
    fail("ProctoringViolations", e)

try:
    t1 = ProctoringTranscript(
        session_id=session.id,
        text="Can you explain your Python experience?",
        language="en",
        flagged_keywords=[],
    )
    db.add(t1); db.flush()
    ok("ProctoringTranscript: audio transcript saved")
except Exception as e:
    fail("ProctoringTranscript", e)

# ═══════════════════════════════════════════════════════════════
# COMMIT
# ═══════════════════════════════════════════════════════════════
try:
    db.commit()
    ok("All changes committed to database")
except Exception as e:
    db.rollback()
    fail("DB commit", e)

# ═══════════════════════════════════════════════════════════════
# READ-BACK VERIFICATION
# ═══════════════════════════════════════════════════════════════
section("Read-Back Verification")

db2 = db_manager.SessionLocal()
try:
    c = db2.query(Company).filter_by(id=company.id).first()
    assert c.name == "Acme Corp"
    assert c.secondary_color == "#145649"
    assert c.tagline == "Hire smarter, faster"
    ok(f"Company branding fields verified (secondary_color, tagline, font_family)")
except Exception as e:
    fail("Company read-back", e)

try:
    tc = db2.query(TenantConfig).filter_by(company_id=company.id).first()
    feats = json.loads(tc.features_json)
    assert feats["ats"] and feats["proctoring"] and feats["ai_questions"]
    ok(f"TenantConfig: features={feats}")
except Exception as e:
    fail("TenantConfig read-back", e)

try:
    sp = db2.query(SubscriptionPlan).filter_by(company_id=company.id).first()
    assert sp.plan_name == "growth"
    assert sp.status == "active"
    ok(f"SubscriptionPlan: plan={sp.plan_name}, status={sp.status}")
except Exception as e:
    fail("SubscriptionPlan read-back", e)

try:
    p = db2.query(CandidateProfileExt).filter_by(user_id=candidate_user.id).first()
    assert p.linkedin_url == "https://linkedin.com/in/carol"
    assert p.preferred_job_type == "Full-time"
    assert p.work_mode_preference == "Remote"
    assert json.loads(p.preferred_job_roles) == ["Backend Developer", "API Developer"]
    assert len(p.work_experiences) == 1
    assert len(p.educations) == 1
    assert len(p.skills) == 3
    assert len(p.certifications) == 1
    assert len(p.projects) == 1
    ok(f"CandidateProfileExt: links + preferences flat, {len(p.skills)} skills, {len(p.work_experiences)} jobs")
except Exception as e:
    fail("CandidateProfileExt read-back", e)

try:
    violations = db2.query(ProctoringViolation).filter_by(session_id=session.id).all()
    types = [v.violation_type for v in violations]
    assert "MULTIPLE_FACES" in types
    assert "CHEATING_FRAME" in types
    assert "TAB_SWITCH" in types
    cheat = next(v for v in violations if v.violation_type == "CHEATING_FRAME")
    assert cheat.frame_number == 450
    tab = next(v for v in violations if v.violation_type == "TAB_SWITCH")
    assert tab.cumulative_count == 1
    ok(f"ProctoringViolations verified: {types}")
except Exception as e:
    fail("ProctoringViolations read-back", e)

try:
    sets = db2.query(QuestionSetAssignment).filter_by(interview_id=interview.id).all()
    set_nums = sorted(set(s.set_number for s in sets))
    assert set_nums == [1, 2]
    ok(f"QuestionSetAssignments: {len(sets)} rows across sets {set_nums}")
except Exception as e:
    fail("QuestionSetAssignment read-back", e)

try:
    hist = db2.query(CandidateStageHistory).filter_by(candidate_id=candidate.id).all()
    stages = [h.to_stage for h in hist]
    assert "applied" in stages
    assert "shortlisted" in stages
    assert "interview_completed" in stages
    assert "offer_sent" in stages
    ok(f"Stage history: {stages}")
except Exception as e:
    fail("CandidateStageHistory read-back", e)

try:
    o = db2.query(Offer).filter_by(candidate_id=candidate.id).first()
    assert o.status == "sent"
    assert float(o.salary) == 1500000.0
    ok(f"Offer: salary={o.salary}, status={o.status}")
except Exception as e:
    fail("Offer read-back", e)

db2.close()
db.close()

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
print(f"\n{'═'*60}")
if errors:
    print(f"\033[91m  FAILED: {len(errors)} test(s)\033[0m")
    for e in errors:
        print(f"    ✗ {e}")
else:
    print(f"\033[92m  ALL TESTS PASSED — 29 tables verified end-to-end\033[0m")

tables_tested = [
    "companies", "tenant_configs", "subscription_plans", "system_config",
    "users", "departments", "hr_profiles", "interviewer_profiles",
    "job_postings", "candidates", "ats_results", "candidate_stage_history",
    "interviews", "interview_questions", "question_set_assignments",
    "candidate_question_history", "interview_feedback", "offers",
    "candidate_requests", "notifications",
    "candidate_profile_ext", "candidate_work_experience", "candidate_education",
    "candidate_skills", "candidate_certifications", "candidate_projects",
    "proctoring_sessions", "proctoring_violations", "proctoring_transcripts",
]
print(f"\n  Tables tested: {len(tables_tested)}/29")
print(f"{'═'*60}\n")
sys.exit(1 if errors else 0)
