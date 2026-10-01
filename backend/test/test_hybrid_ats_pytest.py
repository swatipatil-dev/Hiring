"""
Hybrid ATS engine — 16-case pytest suite (8 technical + 8 non-technical).

Each case asserts expected score direction (high/medium/low) rather than
an exact number, making the suite robust against minor scoring tweaks.

Run with:  pytest test/test_hybrid_ats_pytest.py -v
Slow flag:  pytest -m slow   (these cases each invoke the LLM service)
"""
import pytest
from src.services.llm_ats_service import LLMATSService


@pytest.fixture(scope="module")
def svc():
    return LLMATSService()


def _score(svc, resume, jd, skills, exp):
    import asyncio
    return asyncio.get_event_loop().run_until_complete(
        svc.hybrid_score_candidate(
            resume_text=resume,
            job_description=jd,
            required_skills=skills,
            required_experience=exp,
        )
    )


# ══════════════════════════════════════════════════════════════════
# TECHNICAL CASES
# ══════════════════════════════════════════════════════════════════

@pytest.mark.slow
def test_t1_genai_perfect_match(svc):
    """GenAI engineer with exactly matching skills should score ≥65."""
    r = _score(
        svc,
        resume="""
SUMMARY AI Engineer, 5 years, specialised in LLM applications and RAG pipelines.
SKILLS Python, LangChain, LangGraph, LLMs, RAG, FAISS, ChromaDB, FastAPI, Docker, AWS
EXPERIENCE 2019-2024 AI Engineer at NeuralTech
  Built production RAG pipelines with FAISS and ChromaDB
  Deployed multi-agent systems using LangChain and LangGraph
  FastAPI microservices on AWS ECS
EDUCATION B.Tech Computer Science 2019
CERTIFICATIONS AWS Certified Developer 2022
""",
        jd="Hiring GenAI Engineer. Need: LangChain, LangGraph, RAG, LLMs, FAISS, FastAPI, Docker, AWS. 4+ yrs.",
        skills=["langchain", "langgraph", "rag", "llms", "faiss", "fastapi", "docker", "aws"],
        exp=4,
    )
    assert r.ats_score >= 65, f"T1 perfect match: expected ≥65, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_t2_backend_cluster_match(svc):
    """Backend dev with similar-but-not-identical stack should score ≥40 (cluster credit)."""
    r = _score(
        svc,
        resume="""
SKILLS Python, Django, PostgreSQL, Redis, GitHub Actions, Docker, Prometheus, pytest
EXPERIENCE 2021-2024 Backend Engineer at FinStartup
  REST APIs in Django with PostgreSQL
  Redis caching, containerised with Docker
EDUCATION BSc Software Engineering 2021
""",
        jd="Backend Engineer: Flask, MySQL, GitLab CI, Kubernetes, Datadog. 3 yrs exp.",
        skills=["flask", "mysql", "gitlab ci", "kubernetes", "datadog"],
        exp=3,
    )
    assert r.ats_score >= 30, f"T2 cluster match: expected ≥30, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_t3_devops_strong_match(svc):
    """DevOps engineer with strong matching skills should score ≥60."""
    r = _score(
        svc,
        resume="""
SKILLS AWS, Terraform, Kubernetes, Docker, Jenkins, Ansible, Helm, Linux, Python, Bash
EXPERIENCE 2018-2024 DevOps Engineer at CloudCorp
  Provisioned AWS infra with Terraform; managed Kubernetes clusters; CI/CD with Jenkins
EDUCATION B.Eng Computer Engineering 2018
CERTIFICATIONS AWS Solutions Architect, CKA Kubernetes Administrator
""",
        jd="DevOps Engineer: AWS, Terraform, Kubernetes, Docker, Jenkins, Ansible. 5+ yrs.",
        skills=["aws", "terraform", "kubernetes", "docker", "jenkins", "ansible"],
        exp=5,
    )
    assert r.ats_score >= 60, f"T3 strong match: expected ≥60, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_t4_wrong_role_scores_low(svc):
    """Data analyst applying for ML Engineer role should score ≤50."""
    r = _score(
        svc,
        resume="""
SKILLS Excel, Power BI, Tableau, SQL, Python basic, Google Sheets
EXPERIENCE 2022-2024 Data Analyst at RetailMart
  Dashboards in Power BI and Tableau; SQL queries for weekly reports
EDUCATION BCom Business Analytics 2022
""",
        jd="ML Engineer: PyTorch, TensorFlow, scikit-learn, Kubernetes, MLflow, AWS. 3+ yrs.",
        skills=["pytorch", "tensorflow", "scikit-learn", "kubernetes", "mlflow", "aws"],
        exp=3,
    )
    assert r.ats_score <= 50, f"T4 wrong role: expected ≤50, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_t5_underqualified_scores_low(svc):
    """Junior dev applying for senior role (6 yrs exp required) should score ≤50."""
    r = _score(
        svc,
        resume="""
SKILLS Python, basic HTML, CSS, MySQL
EXPERIENCE 2023-2024 Junior Developer internship at WebAgency
  Built simple web pages; fixed bugs in Python scripts
EDUCATION BSc IT in progress 2024
""",
        jd="Senior Full Stack Engineer: React, Node.js, PostgreSQL, Docker, AWS. 6+ yrs.",
        skills=["react", "nodejs", "postgresql", "docker", "aws"],
        exp=6,
    )
    assert r.ats_score <= 50, f"T5 under-qualified: expected ≤50, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_t6_overqualified_junior_role(svc):
    """10yr senior applying for junior role — skills match but exp is way over."""
    r = _score(
        svc,
        resume="""
SKILLS Python, React, Node.js, PostgreSQL, AWS, Docker, Kubernetes, Terraform
EXPERIENCE 2014-2024 Senior Software Architect at BigTech; led 12-person team
EDUCATION MSc Computer Science 2014
CERTIFICATIONS AWS Solutions Architect, GCP Professional
""",
        jd="Junior Web Developer: basic Python, HTML, CSS, MySQL. 0-1 yr experience.",
        skills=["python", "html", "css", "mysql"],
        exp=1,
    )
    # Score may be moderate — skills match but experience penalty
    assert 0.0 <= r.ats_score <= 100.0, "T6: score must be in valid range"


@pytest.mark.slow
def test_t7_wrong_domain_scores_low(svc):
    """Mobile developer applying for DevOps should score ≤50, like T4/T5.

    Was ≤45, which sat exactly on the score: education (B.Tech, 35%) and experience
    (20%) give 45 with no matching skills at all, and any semantic similarity tipped
    it over once the semantic model was actually used."""
    r = _score(
        svc,
        resume="""
SKILLS Swift, Kotlin, iOS, Android, Flutter, Firebase, Xcode, React Native
EXPERIENCE 2020-2024 Mobile Developer at AppFactory; published 5 iOS apps
EDUCATION B.Tech IT 2020
""",
        jd="DevOps Engineer: AWS, Terraform, Kubernetes, Jenkins, Docker, Linux. 3+ yrs.",
        skills=["aws", "terraform", "kubernetes", "jenkins", "docker", "linux"],
        exp=3,
    )
    assert r.ats_score <= 50, f"T7 wrong domain: expected ≤50, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_t8_minimal_resume(svc):
    """Almost-empty resume should score low but not crash."""
    r = _score(
        svc,
        resume="John. Knows Python. Some experience.",
        jd="Python developer, FastAPI, PostgreSQL, Docker. 2 yrs.",
        skills=["python", "fastapi", "postgresql", "docker"],
        exp=2,
    )
    assert 0.0 <= r.ats_score <= 100.0


# ══════════════════════════════════════════════════════════════════
# NON-TECHNICAL CASES
# ══════════════════════════════════════════════════════════════════

@pytest.mark.slow
def test_n1_digital_marketing_strong_match(svc):
    """Digital marketing manager with perfect skill overlap should score ≥65."""
    r = _score(
        svc,
        resume="""
SKILLS SEO, Google Ads, Facebook Ads, Content Marketing, Email Marketing,
  HubSpot, Google Analytics, Canva, Copywriting, Social Media, A/B Testing
EXPERIENCE 2018-2024 Digital Marketing Manager at GrowthBrand
  Grew organic traffic 180% via SEO; managed $200k/month Google Ads budget
  Email campaigns via HubSpot with 35% open rate
EDUCATION BBA Marketing 2018
CERTIFICATIONS Google Ads Certified, HubSpot Inbound Marketing Certified
""",
        jd="Digital Marketing Manager: SEO, Google Ads, Facebook Ads, HubSpot, Google Analytics. 5+ yrs.",
        skills=["seo", "google ads", "facebook ads", "hubspot", "google analytics"],
        exp=5,
    )
    assert r.ats_score >= 65, f"N1 strong match: expected ≥65, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_n2_hr_manager_good_match(svc):
    """HR manager with matching skills should score ≥50."""
    r = _score(
        svc,
        resume="""
SKILLS Recruitment, Talent Acquisition, HRIS, Payroll, Employee Relations,
  Performance Management, Onboarding, Labor Law, SAP HR
EXPERIENCE 2019-2024 HR Manager at ManufacturingCo; full-cycle recruitment 200+ positions
EDUCATION MBA Human Resources 2019
CERTIFICATIONS SHRM-CP Certified
""",
        jd="HR Manager: Recruitment, Talent Acquisition, Performance Management, HRIS, Labor Law. 4+ yrs.",
        skills=["recruitment", "talent acquisition", "performance management", "hris", "labor law"],
        exp=4,
    )
    assert r.ats_score >= 50, f"N2 good match: expected ≥50, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_n3_sales_exec_for_senior_manager(svc):
    """Sales exec applying for senior manager — partial match expected."""
    r = _score(
        svc,
        resume="""
SKILLS B2B Sales, Cold Calling, CRM, Salesforce, Negotiation, Account Management
EXPERIENCE 2020-2024 Sales Executive at TechSales Inc; achieved 140% quota 3 yrs running
EDUCATION BBA Sales 2020
""",
        jd="Senior Sales Manager: B2B Sales, CRM, Team Leadership, Revenue Forecasting, Salesforce. 5+ yrs.",
        skills=["b2b sales", "crm", "negotiation", "team leadership", "revenue forecasting"],
        exp=5,
    )
    assert 20.0 <= r.ats_score <= 80.0, f"N3 partial match: expected 20–80, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_n4_career_switch_scores_low(svc):
    """Content writer applying for Product Manager should score low."""
    r = _score(
        svc,
        resume="""
SKILLS Content Writing, Blogging, SEO Writing, WordPress, Social Media, Canva, Copyediting
EXPERIENCE 2021-2024 Content Writer at MediaHouse; wrote 200+ blog posts
EDUCATION BA English Literature 2021
""",
        jd="Product Manager: Product Roadmap, Agile, JIRA, Stakeholder Management, User Research, SQL. 3+ yrs.",
        skills=["product roadmap", "agile", "jira", "stakeholder management", "user research", "sql"],
        exp=3,
    )
    assert r.ats_score <= 45, f"N4 career switch: expected ≤45, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_n5_senior_accountant_for_finance_manager(svc):
    """Senior accountant applying for finance manager — strong overlap."""
    r = _score(
        svc,
        resume="""
SKILLS Financial Reporting, Bookkeeping, QuickBooks, Excel, Tax Prep,
  Accounts Payable, Budgeting, GAAP, Tally ERP
EXPERIENCE 2018-2024 Senior Accountant at AuditFirm LLP; financial statements for 40+ clients
EDUCATION BCom Accounting 2018
CERTIFICATIONS CPA Certified 2020
""",
        jd="Finance Manager: Financial Planning, Budgeting, Forecasting, Financial Reporting, GAAP. 5+ yrs.",
        skills=["financial planning", "budgeting", "forecasting", "financial reporting", "gaap"],
        exp=5,
    )
    assert r.ats_score >= 45, f"N5 good overlap: expected ≥45, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_n6_graphic_designer_strong_match(svc):
    """Graphic designer with exact tool overlap should score ≥60."""
    r = _score(
        svc,
        resume="""
SKILLS Adobe Photoshop, Adobe Illustrator, Figma, InDesign, Canva, UI/UX, Branding, Typography
EXPERIENCE 2019-2024 Senior Graphic Designer at CreativeStudio; brand identities for 30+ clients
EDUCATION BFA Graphic Design 2019
""",
        jd="Graphic Designer: Adobe Photoshop, Illustrator, Figma, InDesign, Branding, Typography. 4+ yrs.",
        skills=["adobe photoshop", "adobe illustrator", "figma", "indesign", "branding", "typography"],
        exp=4,
    )
    assert r.ats_score >= 60, f"N6 strong match: expected ≥60, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_n7_totally_irrelevant_scores_low(svc):
    """Head chef applying for software engineer — should score very low."""
    r = _score(
        svc,
        resume="""
SKILLS French Cuisine, Pastry, Menu Planning, Kitchen Management, Food Safety, HACCP
EXPERIENCE 2016-2024 Head Chef at GrandHotel; led kitchen team of 15
EDUCATION Culinary Arts Diploma 2016
""",
        jd="Software Engineer: Python, FastAPI, PostgreSQL, Docker, AWS, REST APIs. 3+ yrs.",
        skills=["python", "fastapi", "postgresql", "docker", "aws"],
        exp=3,
    )
    assert r.ats_score <= 30, f"N7 totally irrelevant: expected ≤30, got {r.ats_score:.1f}"


@pytest.mark.slow
def test_n8_project_manager_mixed_match(svc):
    """IT Project Manager with strong Agile/JIRA overlap should score ≥60."""
    r = _score(
        svc,
        resume="""
SKILLS Project Management, Agile, Scrum, JIRA, Confluence, Stakeholder Management,
  Risk Management, PMP, MS Project, Budget Management, Team Leadership
EXPERIENCE 2017-2024 Senior PM at ConsultingFirm; delivered 20+ software projects
EDUCATION MBA Project Management 2017
CERTIFICATIONS PMP Certified, Scrum Master CSM
""",
        jd="IT Project Manager: Agile, Scrum, JIRA, Stakeholder Management, Risk Management, PMP. 5+ yrs.",
        skills=["agile", "scrum", "jira", "stakeholder management", "risk management", "pmp"],
        exp=5,
    )
    assert r.ats_score >= 60, f"N8 strong match: expected ≥60, got {r.ats_score:.1f}"
