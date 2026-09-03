import pytest
from routes.candidate_profile_routes import (
    _certifications_from_section,
    _projects_from_section,
    _job_preferences_from_section,
    _skills_from_section,
    _education_from_section,
    _infer_project_type,
    _match_alias,
    _work_experience_from_section,
    _normalize_month_year,
    _spacy_is_description,
    _keyword_is_description,
    _TECH_ALIASES,
    _ROLE_ALIASES,
)


# --- Phase 1: Title Classifier Hardening ------------------------------------

def test_infinitive_phrase_not_a_title():
    text = """
PROJECTS
Payroll to manage HR data efficiently
Used Python and SQL for backend data processing
Employee Management System
Built an HR module using Django and PostgreSQL
"""
    projs = _projects_from_section(text)
    titles = [p["title"] for p in projs]
    assert "Payroll to manage HR data efficiently" not in titles
    assert any("Employee Management System" in t for t in titles)


def test_description_tail_adverb_not_a_title():
    text = """
PROJECTS
CROP RECOMMENDATION SYSTEM
Predicts optimal crop based on soil and weather parameters efficiently
Financial Research Assistant
Automates financial report summarization accurately
"""
    projs = _projects_from_section(text)
    titles = [p["title"] for p in projs]
    assert "Predicts optimal crop based on soil and weather parameters efficiently" not in titles
    assert "Automates financial report summarization accurately" not in titles
    assert len(projs) == 2


def test_word_count_limit_rejects_long_lines():
    text = """
PROJECTS
Financial Research Assistant
Retrieves and summarizes financial data from various market databases online
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    assert projs[0]["title"] == "Financial Research Assistant"


# --- Phase 2: Alias Mapping for Field Labels ---------------------------------

def test_tech_alias_frameworks_and_libraries():
    text = """
PROJECTS
Employee Management System
Frameworks & Libraries: Django, PostgreSQL, React
Built an HR management tool for payroll and attendance
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    assert projs[0]["technologies_used"] is not None
    assert "Django" in projs[0]["technologies_used"]


def test_tech_alias_toolset():
    text = """
PROJECTS
DocSense AI
Toolset: Python, FAISS, LangChain, Gemini API
Built a multimodal RAG chatbot
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    assert projs[0]["technologies_used"] is not None
    assert "FAISS" in projs[0]["technologies_used"]


def test_role_alias_my_role():
    text = """
PROJECTS
CROP RECOMMENDATION SYSTEM
My Role: ML Engineer
Built ML pipeline for crop prediction
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    assert projs[0]["role"] == "ML Engineer"


def test_role_alias_designation():
    text = """
PROJECTS
Financial Research Assistant
Designation: Data Analyst
Built financial report automation
"""
    projs = _projects_from_section(text)
    assert projs[0]["role"] == "Data Analyst"


def test_date_duration_alias():
    text = """
PROJECTS
Employee Management System
Duration: Jan 2023 to Mar 2023
Built HR management system using Django
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    assert projs[0]["start_date"] is not None


def test_date_ongoing_present():
    text = """
PROJECTS
DocSense AI
Duration: Jan 2024 to Present
Built a RAG-based chatbot
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    assert projs[0]["ongoing"] is True
    assert projs[0]["end_date"] is None


def test_repo_url_detected():
    text = """
PROJECTS
DeepGuard
Repository: https://github.com/user/deepguard
Applied deepfake detection
"""
    projs = _projects_from_section(text)
    assert projs[0]["repository_url"] is not None
    assert "github.com" in projs[0]["repository_url"]


def test_live_url_from_demo_label():
    text = """
PROJECTS
DeepGuard
Demo: https://deepguard.streamlit.app
Applied deepfake detection
"""
    projs = _projects_from_section(text)
    assert projs[0]["project_url"] is not None
    assert "streamlit.app" in projs[0]["project_url"]


# --- Phase 3: project_type Inference -----------------------------------------

def test_infer_project_type_personal():
    assert _infer_project_type("PERSONAL PROJECTS") == "Personal"

def test_infer_project_type_academic():
    assert _infer_project_type("ACADEMIC PROJECTS") == "Academic"
    assert _infer_project_type("College Projects") == "Academic"

def test_infer_project_type_professional():
    assert _infer_project_type("PROFESSIONAL PROJECTS") == "Professional"

def test_infer_project_type_opensource():
    assert _infer_project_type("OPEN SOURCE CONTRIBUTIONS") == "Open-source"

def test_infer_project_type_none_for_bare_projects():
    assert _infer_project_type("PROJECTS") is None
    assert _infer_project_type("") is None

def test_project_type_propagated_to_all_projects():
    text = """
PROJECTS
CROP RECOMMENDATION SYSTEM
Built ML crop prediction pipeline
Employee Management System
Built HR management module
"""
    projs = _projects_from_section(text, section_header="ACADEMIC PROJECTS")
    assert all(p["project_type"] == "Academic" for p in projs)


# --- Phase 5: rapidfuzz Alias Matching --------------------------------------

def test_match_alias_exact():
    assert _match_alias("tech", _TECH_ALIASES) is True
    assert _match_alias("role", _ROLE_ALIASES) is True

def test_match_alias_fuzzy_typo():
    assert _match_alias("Techstack", _TECH_ALIASES) is True

def test_match_alias_non_match():
    assert _match_alias("cgpa", _TECH_ALIASES) is False
    assert _match_alias("university", _TECH_ALIASES) is False


# --- Integration Test (real resume from Image 1) ----------------------------

def test_image_2_real_resume_projects_extraction():
    resume_projects_text = """
    PERSONAL PROJECTS
    Vehicle Price Prediction Using Machine Learning
        * Built an end-to-end ML model to predict used vehicle resale prices using a real-time dataset
        * Preprocessed data using IQR outlier capping, log transformation, and binary encoding of safety features
        * Trained and compared Linear Regression, XGBoost using sklearn Pipeline to prevent data leakage
        * Achieved R2 score of 0.94 with XGBoost, validated with 5-fold cross-validation for consistent generalization
        * Deployed as an interactive web app using Streamlit for real-time price prediction.
    DeepGuard - Multi-Modal Deepfake Fraud Detection
        * Built a deepfake detection system combining deep learning, computer vision, and NLP
        * Applied transfer learning on EfficientNet-B4 for classification with probability scoring
        * Implemented real-time liveness detection using MediaPipe FaceMesh with Eye Aspect Ratio
    DocSense AI - Multimodal RAG Based PDF Question Answering System
        * Built DocSense AI, a Multimodal RAG Chatbot answering PDF-based queries
        * Implemented PDF chunking pipeline using LangChain with all-MiniLM-L6-v2 embeddings

    EXTRA CURRICULAR ACTIVITIES
    KATTELA HARSHITHA
    CGPA: 8.5
    """
    projs = _projects_from_section(resume_projects_text, section_header="PERSONAL PROJECTS")
    assert len(projs) == 3
    assert projs[0]["title"] == "Vehicle Price Prediction Using Machine Learning"
    assert "Built an end-to-end ML model" in (projs[0]["description"] or "")
    assert "Preprocessed data" not in projs[0]["title"]
    assert projs[0]["project_type"] == "Personal"
    assert "DeepGuard" in projs[1]["title"]
    assert projs[1]["project_type"] == "Personal"
    assert "DocSense AI" in projs[2]["title"]
    assert projs[2]["project_type"] == "Personal"


def test_empty_fields_are_none_not_empty_string():
    text = """
PROJECTS
CROP RECOMMENDATION SYSTEM
Built ML pipeline for crop prediction
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    p = projs[0]
    for field in ("role", "technologies_used", "project_url", "repository_url",
                  "start_date", "end_date"):
        assert p[field] is None, f"Field {field!r} must be None, got {p[field]!r}"


def test_missing_projects_section_returns_empty():
    assert _projects_from_section("") == []
    assert _projects_from_section(None) == []


def test_alex_mercer_resume_no_garbage_leaks():
    """Verify that Alex Mercer's resume extracts ONLY HireFlow AI Screening Platform and no garbage."""
    alex_mercer_resume = """
ALEX MERCER
Email: alex.mercer@hireflow.com | Phone: +1 (555) 019-2834 | Gender: Male | DOB: 1995-08-15
Address: 123 Innovation Way, Tech Park, San Francisco, CA 94105, United States
LinkedIn: https://linkedin.com/in/alexmercer-dev | GitHub: https://github.com/alexmercer-dev

PROFESSIONAL SUMMARY
Headline: Senior Full Stack & Python Engineer at TechCorp Solutions
Results-driven Senior Full-Stack & Python Software Engineer with 5+ years of total software development experience
architecting, developing, and deploying high-performance microservices and responsive web applications.

WORK EXPERIENCE
Senior Full-Stack Engineer — TechCorp Solutions (2022-01 – Present)
Full-time | San Francisco, CA
• Architected and deployed 15+ scalable RESTful microservices using FastAPI, Python, and PostgreSQL.
• Engineered real-time analytics dashboard with React, TypeScript, and WebSockets.
Software Engineer — DataScale Systems (2020-06 – 2021-12)
Full-time | Remote
• Built distributed data processing pipelines in Python and Apache Kafka processing 10M+ daily events.

EDUCATION
Bachelor of Technology in Computer Science (2016-08 – 2020-05)
University of California, Berkeley — Specialization: Computer Science & AI

CORE TECHNICAL SKILLS
Programming Languages: Python, JavaScript, TypeScript, SQL, Go, HTML5, CSS3
Frameworks & Libraries: FastAPI, React, Node.js, Express, Django, Flask, Next.js, Redux
Databases & Caching: PostgreSQL, MySQL, Redis, MongoDB, Elasticsearch

CERTIFICATIONS & LICENSES
• AWS Certified Solutions Architect – Associate
• Certified Kubernetes Application Developer (CKAD)

KEY PROJECTS
HireFlow AI Screening Platform
Tech: Python, FastAPI, React, PostgreSQL, Docker | GitHub: https://github.com/alexmercer-dev/hireflow-ai
Developed an automated ATS resume parsing and candidate ranking web application with real-time scoring.

JOB PREFERENCES
Preferred Role: Senior Backend Engineer / Full Stack Lead
Preferred Location: San Francisco, CA / Remote
"""
    from src.services.enhanced_ats_service import detect_sections
    sections = detect_sections(alex_mercer_resume)
    
    # 1. 'CORE TECHNICAL SKILLS' section header must be detected
    assert "skills" in sections, "CORE TECHNICAL SKILLS header must be detected as skills section"

    # 2. 'KEY PROJECTS' section must contain ONLY the project text
    proj_section = sections.get("projects", "")
    assert "HireFlow AI" in proj_section
    assert "San Francisco" not in proj_section, "Address must NOT leak into projects section"
    assert "Software Engineer" not in proj_section, "Experience must NOT leak into projects section"

    # 3. _projects_from_section must extract EXACTLY 1 project (HireFlow AI Screening Platform)
    projs = _projects_from_section(proj_section, section_header="KEY PROJECTS")
    assert len(projs) == 1, f"Expected 1 project, extracted {len(projs)}: {[p['title'] for p in projs]}"
    assert projs[0]["title"] == "HireFlow AI Screening Platform"
    assert "Python, FastAPI" in (projs[0]["technologies_used"] or "")
    assert projs[0]["repository_url"] == "https://github.com/alexmercer-dev/hireflow-ai"


# --- Education Extraction Unit Tests ---------------------------------------

def test_education_single_line_inline_institution():
    """Inline education format: 'B.Tech in CSE from Raghu Engineering College'."""
    text = "B.Tech in CSE from Raghu Engineering College, Visakhapatnam (2016-2020)"
    edus = _education_from_section(text)
    assert len(edus) == 1
    assert "B.Tech in CSE" in edus[0]["degree"]
    assert "Raghu Engineering College" in edus[0]["institution"]


def test_education_multi_line_degree_and_institution():
    """Multi-line education format (Alex Mercer style): Line 1 Degree, Line 2 Institution."""
    text = """
EDUCATION
Bachelor of Technology in Computer Science (2016-08 – 2020-05)
University of California, Berkeley — Specialization: Computer Science & AI
CGPA: 3.9 / 4.0 (Grade: Distinction)
"""
    edus = _education_from_section(text)
    assert len(edus) == 1
    assert "Bachelor of Technology in Computer Science" in edus[0]["degree"]
    assert "University of California, Berkeley" in edus[0]["institution"]
    assert "CGPA" not in edus[0]["institution"]
    assert "2016" not in edus[0]["institution"]


def test_education_intermediate_and_high_school():
    """Intermediate (12th) and SSC (10th) degree extraction."""
    text = """
EDUCATION
Bachelor of Technology (B.Tech)
Raghu Engineering College
Intermediate (MPC)
Sri Chaitanya Junior College
Secondary School Certificate (SSC / 10th)
Zilla Parishad High School
"""
    edus = _education_from_section(text)
    assert len(edus) == 3
    assert "B.Tech" in edus[0]["degree"]
    assert "Raghu Engineering College" in edus[0]["institution"]
    assert "Intermediate" in edus[1]["degree"]
    assert "Sri Chaitanya Junior College" in edus[1]["institution"]
    assert "Secondary School Certificate" in edus[2]["degree"] or "SSC" in edus[2]["degree"] or "10th" in edus[2]["degree"]
    assert "High School" in edus[2]["institution"]


def test_education_cid_font_artifacts_and_wrapped_degree_title():
    """Test CID font artifacts (cid:135), graduation emoji, and wrapped 'Computer Science and' completion."""
    text = """
Education
(cid:135) 🎓 B.Tech in Computer Science and                      2022 - 2026
Simhadri Engineering College, Visakhapatnam
CGPA: 7.89 / 10
"""
    edus = _education_from_section(text)
    assert len(edus) == 1
    assert edus[0]["degree"] == "B.Tech in Computer Science and Engineering"
    assert "Simhadri Engineering College" in edus[0]["institution"]
    assert "(cid:" not in edus[0]["degree"]
    assert "🎓" not in edus[0]["degree"]


# --- Titles that the description-starter list used to swallow ----------------

def test_participle_titles_survive():
    """"Automated ...", "Integrated ...", "Optimized ..." open real project titles."""
    text = """
PROJECTS
Automated Attendance System
Tech: Python, OpenCV
- Recognises faces at the gate and marks attendance

Integrated Payment Gateway
Tech: Node.js, Stripe
- Handles refunds and webhooks
"""
    projs = _projects_from_section(text)
    names = [p["name"] for p in projs]
    assert "Automated Attendance System" in names
    assert "Integrated Payment Gateway" in names


def test_participle_sentence_still_rejected():
    """The same word inside an actual sentence is still a description."""
    text = """
PROJECTS
Smart Resume Parser
Tech: Python, spaCy
Automated the nightly export of parsed candidates.
"""
    projs = _projects_from_section(text)
    assert [p["name"] for p in projs] == ["Smart Resume Parser"]
    assert "Automated the nightly export" in (projs[0]["description"] or "")


def test_title_mentioning_a_school_is_not_an_education_line():
    """"school"/"institute"/"centre" belong in plenty of product names."""
    text = """
PROJECTS
Online Learning Platform for Schools
Tech: React, Firebase
- Live classes and attendance

Health Centre Tracker
Tech: Django
- Tracks stock across rural clinics
"""
    names = [p["name"] for p in _projects_from_section(text)]
    assert "Online Learning Platform for Schools" in names
    assert "Health Centre Tracker" in names


def test_real_education_line_still_rejected_as_a_project():
    text = """
PROJECTS
Bachelor of Technology, Malla Reddy University
"""
    assert _projects_from_section(text) == []


# --- Link lines must not become description text -----------------------------

def test_link_only_line_not_in_description():
    text = """
PROJECTS
Smart Resume Parser
Tech: Python, spaCy
GitHub: https://github.com/someone/resume-parser
- Built an NER pipeline
"""
    projs = _projects_from_section(text)
    assert len(projs) == 1
    assert projs[0]["repository_url"] == "https://github.com/someone/resume-parser"
    assert "github.com" not in (projs[0]["description"] or "")
    assert "Built an NER pipeline" in (projs[0]["description"] or "")


# --- Dates now survive extraction, normalised to fit String(7) ---------------

def test_project_dates_normalised_for_storage():
    text = """
PROJECTS
Smart Resume Parser
Duration: Jan 2020 - Mar 2021
"""
    projs = _projects_from_section(text)
    assert projs[0]["start_date"] == "2020-01"
    assert projs[0]["end_date"] == "2021-03"
    assert projs[0]["ongoing"] is False
    assert len(projs[0]["start_date"]) <= 7


def test_project_ongoing_dates():
    text = """
PROJECTS
Smart Resume Parser
Duration: 2020 - Present
"""
    projs = _projects_from_section(text)
    assert projs[0]["start_date"] == "2020"
    assert projs[0]["end_date"] is None
    assert projs[0]["ongoing"] is True


def test_work_experience_keeps_its_dates():
    text = """
EXPERIENCE
Acme Corp Jan 2019 - Dec 2021
Backend Engineer
"""
    work = _work_experience_from_section(text)
    assert len(work) == 1
    assert work[0]["company"] == "Acme Corp"
    assert work[0]["title"] == "Backend Engineer"
    assert work[0]["start_date"] == "2019-01"
    assert work[0]["end_date"] == "2021-12"
    assert work[0]["currently_working"] is False
    assert work[0]["duration_months"] == 35


def test_work_experience_ongoing_role():
    text = """
EXPERIENCE
Acme Corp 2022 - Present
Staff Engineer
"""
    work = _work_experience_from_section(text)
    assert work[0]["currently_working"] is True
    assert work[0]["end_date"] is None
    assert work[0]["duration_months"] > 0


def test_education_keeps_years_and_grade():
    text = """
EDUCATION
B-Tech Computer Science and Engineering 2018 - 2022 CGPA: 8.6
Malla Reddy University Hyderabad
"""
    edus = _education_from_section(text)
    assert edus[0]["institution"] == "Malla Reddy University"
    assert edus[0]["start_year"] == 2018
    assert edus[0]["end_year"] == 2022
    assert edus[0]["grade"] and "8.6" in edus[0]["grade"]


# --- Date helpers ------------------------------------------------------------

def test_normalize_month_year_fits_the_column():
    assert _normalize_month_year("Jan 2020") == "2020-01"
    assert _normalize_month_year("September 2019") == "2019-09"
    assert _normalize_month_year("2021") == "2021"
    assert _normalize_month_year("sometime") is None


def test_spacy_and_keyword_detectors_are_distinct_functions():
    """Regression: the keyword detector used to shadow the spaCy one outright."""
    assert _spacy_is_description is not _keyword_is_description
    assert _keyword_is_description("Built an NER pipeline.") is True
    assert _keyword_is_description("Smart Resume Parser") is False
