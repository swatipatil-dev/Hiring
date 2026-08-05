"""
Comprehensive hybrid ATS test — 16 cases (8 technical + 8 non-technical)
Run: venv/Scripts/python.exe test/test_hybrid_ats.py
"""

import asyncio
from src.services.llm_ats_service import LLMATSService

svc = LLMATSService()

cases = []

# ══════════════════════════════════════════════════════════════════
# TECHNICAL CASES
# ══════════════════════════════════════════════════════════════════

cases.append(dict(
    label="T1 | GenAI Eng — perfect match",
    resume="""
SUMMARY
AI Engineer, 5 years, specialised in LLM applications and RAG pipelines.
SKILLS
Python, LangChain, LangGraph, LLMs, RAG, FAISS, ChromaDB, FastAPI, Docker, AWS, PostgreSQL, Git
EXPERIENCE
2019-2024  AI Engineer at NeuralTech
  Built production RAG pipelines with FAISS and ChromaDB
  Deployed multi-agent systems using LangChain and LangGraph
  FastAPI microservices on AWS ECS
EDUCATION  B.Tech Computer Science 2019
CERTIFICATIONS  AWS Certified Developer 2022
""",
    jd="Hiring GenAI Engineer. Need: LangChain, LangGraph, RAG, LLMs, FAISS, FastAPI, Docker, AWS. 4+ yrs.",
    skills=["langchain","langgraph","rag","llms","faiss","fastapi","docker","aws"],
    exp=4,
))

cases.append(dict(
    label="T2 | Backend Dev — cluster matches (similar stack)",
    resume="""
SKILLS
Python, Django, PostgreSQL, Redis, GitHub Actions, Docker, Prometheus, pytest
EXPERIENCE
2021-2024  Backend Engineer at FinStartup
  REST APIs in Django with PostgreSQL
  Redis caching, containerised with Docker
EDUCATION  BSc Software Engineering 2021
""",
    jd="Backend Engineer: Flask, MySQL, GitLab CI, Kubernetes, Datadog. 3 yrs exp. Bachelor required.",
    skills=["flask","mysql","gitlab ci","kubernetes","datadog"],
    exp=3,
))

cases.append(dict(
    label="T3 | DevOps Eng — strong match",
    resume="""
SKILLS
AWS, Terraform, Kubernetes, Docker, Jenkins, Ansible, Helm, Linux, Python, Bash
EXPERIENCE
2018-2024  DevOps Engineer at CloudCorp
  Provisioned AWS infra with Terraform
  Managed Kubernetes clusters, Helm deployments
  CI/CD with Jenkins and Ansible playbooks
EDUCATION  B.Eng Computer Engineering 2018
CERTIFICATIONS  AWS Solutions Architect, CKA Kubernetes Administrator
""",
    jd="DevOps Engineer: AWS, Terraform, Kubernetes, Docker, Jenkins, Ansible. 5+ yrs. Any degree.",
    skills=["aws","terraform","kubernetes","docker","jenkins","ansible"],
    exp=5,
))

cases.append(dict(
    label="T4 | Data Analyst applying for ML Engineer (wrong role)",
    resume="""
SKILLS
Excel, Power BI, Tableau, SQL, Python basic, Google Sheets
EXPERIENCE
2022-2024  Data Analyst at RetailMart
  Dashboards in Power BI and Tableau
  SQL queries for weekly business reports
EDUCATION  BCom Business Analytics 2022
""",
    jd="ML Engineer: PyTorch, TensorFlow, scikit-learn, Kubernetes, MLflow, AWS. 3+ yrs.",
    skills=["pytorch","tensorflow","scikit-learn","kubernetes","mlflow","aws"],
    exp=3,
))

cases.append(dict(
    label="T5 | Junior Dev applying for Senior role (under-qualified)",
    resume="""
SKILLS
Python, basic HTML, CSS, MySQL
EXPERIENCE
2023-2024  Junior Developer internship at WebAgency
  Built simple web pages
  Fixed bugs in Python scripts
EDUCATION  BSc IT in progress 2024
""",
    jd="Senior Full Stack Engineer: React, Node.js, PostgreSQL, Docker, AWS. 6+ yrs experience.",
    skills=["react","nodejs","postgresql","docker","aws"],
    exp=6,
))

cases.append(dict(
    label="T6 | 10yr Senior applying for Junior role (overqualified)",
    resume="""
SKILLS
Python, React, Node.js, PostgreSQL, AWS, Docker, Kubernetes, Terraform, Microservices
EXPERIENCE
2014-2024  Senior Software Architect at BigTech
  Led 12-person engineering team
  Designed cloud-native microservices on AWS
  React frontend, Node.js APIs, PostgreSQL
EDUCATION  MSc Computer Science 2014
CERTIFICATIONS  AWS Solutions Architect, GCP Professional
""",
    jd="Junior Web Developer: basic Python, HTML, CSS, MySQL. 0-1 yr experience.",
    skills=["python","html","css","mysql"],
    exp=1,
))

cases.append(dict(
    label="T7 | Mobile Dev applying for DevOps (wrong domain)",
    resume="""
SKILLS
Swift, Kotlin, iOS, Android, Flutter, Firebase, Xcode, React Native
EXPERIENCE
2020-2024  Mobile Developer at AppFactory
  Published 5 iOS apps in Swift
  Android apps in Kotlin with Firebase
EDUCATION  B.Tech IT 2020
""",
    jd="DevOps Engineer: AWS, Terraform, Kubernetes, Jenkins, Docker, Linux. 3+ yrs.",
    skills=["aws","terraform","kubernetes","jenkins","docker","linux"],
    exp=3,
))

cases.append(dict(
    label="T8 | Minimal resume (almost empty)",
    resume="John. Knows Python. Some experience.",
    jd="Python developer, FastAPI, PostgreSQL, Docker. 2 yrs.",
    skills=["python","fastapi","postgresql","docker"],
    exp=2,
))

# ══════════════════════════════════════════════════════════════════
# NON-TECHNICAL CASES
# ══════════════════════════════════════════════════════════════════

cases.append(dict(
    label="N1 | Digital Marketing Manager — strong match",
    resume="""
SUMMARY
Digital Marketing Manager, 6 years driving growth via SEO, paid ads, and content strategy.
SKILLS
SEO, Google Ads, Facebook Ads, Content Marketing, Email Marketing, HubSpot,
Google Analytics, Canva, Copywriting, Social Media Management, A/B Testing, Lead Generation
EXPERIENCE
2018-2024  Digital Marketing Manager at GrowthBrand
  Grew organic traffic 180% via SEO and content strategy
  Managed 200k/month Google Ads and Facebook Ads budget
  Email campaigns via HubSpot with 35% open rate
EDUCATION  BBA Marketing 2018
CERTIFICATIONS  Google Ads Certified, HubSpot Inbound Marketing Certified
""",
    jd="Digital Marketing Manager: SEO, Google Ads, Facebook Ads, HubSpot, Google Analytics, Content Marketing, Email Marketing. 5+ yrs.",
    skills=["seo","google ads","facebook ads","hubspot","google analytics","content marketing","email marketing"],
    exp=5,
))

cases.append(dict(
    label="N2 | HR Manager — good match",
    resume="""
SKILLS
Recruitment, Talent Acquisition, HRIS, Payroll Management, Employee Relations,
Performance Management, Onboarding, Labor Law, MS Office, SAP HR
EXPERIENCE
2019-2024  HR Manager at ManufacturingCo
  Full-cycle recruitment for 200+ positions
  Implemented SAP HR, reduced payroll errors 40%
  Conducted performance reviews and training
EDUCATION  MBA Human Resources 2019
CERTIFICATIONS  SHRM-CP Certified
""",
    jd="HR Manager: Recruitment, Talent Acquisition, Performance Management, HRIS, Labor Law, Employee Relations. 4+ yrs. MBA preferred.",
    skills=["recruitment","talent acquisition","performance management","hris","labor law","employee relations"],
    exp=4,
))

cases.append(dict(
    label="N3 | Sales Executive applying for Senior Sales Manager",
    resume="""
SKILLS
B2B Sales, Cold Calling, CRM, Salesforce, Negotiation, Account Management,
Lead Generation, Pipeline Management
EXPERIENCE
2020-2024  Sales Executive at TechSales Inc
  Achieved 140% of annual quota 3 years running
  Managed 50+ enterprise accounts via Salesforce
  Cold outreach generating 30+ leads/month
EDUCATION  BBA Sales and Marketing 2020
""",
    jd="Senior Sales Manager: B2B Sales, CRM, Negotiation, Team Leadership, Revenue Forecasting, Salesforce. 5+ yrs. MBA preferred.",
    skills=["b2b sales","crm","negotiation","team leadership","revenue forecasting","salesforce"],
    exp=5,
))

cases.append(dict(
    label="N4 | Content Writer applying for Product Manager (career switch)",
    resume="""
SKILLS
Content Writing, Blogging, SEO Writing, WordPress, Social Media, Canva, Copyediting, Research
EXPERIENCE
2021-2024  Content Writer at MediaHouse
  Wrote 200+ blog posts and SEO articles
  Managed WordPress CMS
EDUCATION  BA English Literature 2021
""",
    jd="Product Manager: Product Roadmap, Agile, JIRA, Stakeholder Management, User Research, A/B Testing, SQL. 3+ yrs PM experience.",
    skills=["product roadmap","agile","jira","stakeholder management","user research","a/b testing","sql"],
    exp=3,
))

cases.append(dict(
    label="N5 | Senior Accountant applying for Finance Manager",
    resume="""
SKILLS
Financial Reporting, Bookkeeping, QuickBooks, Excel Advanced, Tax Preparation,
Accounts Payable, Accounts Receivable, Budgeting, GAAP, Tally ERP
EXPERIENCE
2018-2024  Senior Accountant at AuditFirm LLP
  Financial statements for 40+ clients
  Payroll and tax filings management
  Excel modelling for budget forecasting
EDUCATION  BCom Accounting 2018
CERTIFICATIONS  CPA Certified 2020
""",
    jd="Finance Manager: Financial Planning, Budgeting, Forecasting, Financial Reporting, Team Leadership, ERP Systems, GAAP. 5+ yrs. CPA preferred.",
    skills=["financial planning","budgeting","forecasting","financial reporting","team leadership","erp systems","gaap"],
    exp=5,
))

cases.append(dict(
    label="N6 | Graphic Designer — strong match",
    resume="""
SKILLS
Adobe Photoshop, Adobe Illustrator, Figma, InDesign, Canva, UI/UX Design,
Branding, Typography, Motion Graphics, After Effects
EXPERIENCE
2019-2024  Senior Graphic Designer at CreativeStudio
  Brand identities for 30+ clients
  UI/UX mockups and prototypes in Figma
  Motion graphics in After Effects
EDUCATION  BFA Graphic Design 2019
""",
    jd="Graphic Designer: Adobe Photoshop, Illustrator, Figma, InDesign, Branding, Typography. 4+ yrs.",
    skills=["adobe photoshop","adobe illustrator","figma","indesign","branding","typography"],
    exp=4,
))

cases.append(dict(
    label="N7 | Head Chef applying for Software Engineer (totally irrelevant)",
    resume="""
SKILLS
French Cuisine, Pastry, Menu Planning, Kitchen Management, Food Safety,
HACCP, Inventory Management, Team Leadership, Catering
EXPERIENCE
2016-2024  Head Chef at GrandHotel
  Led kitchen team of 15
  Designed seasonal menus, maintained HACCP standards
EDUCATION  Culinary Arts Diploma 2016
""",
    jd="Software Engineer: Python, FastAPI, PostgreSQL, Docker, AWS, REST APIs. 3+ yrs.",
    skills=["python","fastapi","postgresql","docker","aws"],
    exp=3,
))

cases.append(dict(
    label="N8 | Project Manager — mixed technical/non-technical (strong match)",
    resume="""
SKILLS
Project Management, Agile, Scrum, JIRA, Confluence, Stakeholder Management,
Risk Management, PMP, MS Project, Budget Management, Team Leadership
EXPERIENCE
2017-2024  Senior Project Manager at ConsultingFirm
  Delivered 20+ software projects on time
  Managed cross-functional teams of 30+ people
  Agile/Scrum delivery with JIRA and Confluence
EDUCATION  MBA Project Management 2017
CERTIFICATIONS  PMP Certified, Scrum Master CSM
""",
    jd="IT Project Manager: Agile, Scrum, JIRA, Stakeholder Management, Risk Management, PMP. 5+ yrs. MBA preferred.",
    skills=["agile","scrum","jira","stakeholder management","risk management","pmp"],
    exp=5,
))


async def run_all():
    results = []
    for i, c in enumerate(cases):
        print(f"  Running {i+1}/{len(cases)}: {c['label']} ...", flush=True)
        r = await svc.hybrid_score_candidate(
            resume_text=c["resume"],
            job_description=c["jd"],
            required_skills=c["skills"],
            required_experience=c["exp"],
        )
        results.append((c["label"], r))
    return results


def print_report(all_results):
    REC_TAG = {
        "Strong Hire": "[HIRE  ]",
        "Phone Screen": "[SCREEN]",
        "Reject":       "[REJECT]",
    }

    print()
    print("=" * 72)
    print("  HYBRID ATS ENGINE — COMPREHENSIVE TEST REPORT")
    print("=" * 72)

    issues = []

    for label, r in all_results:
        bar_n = int(r.ats_score / 5)
        bar = "#" * bar_n + "-" * (20 - bar_n)
        tag = REC_TAG.get(r.llm_recommendation, "[  ?   ]")

        print()
        print(f"  {label}")
        print(f"  Score   : {bar} {r.ats_score:5.1f}/100  {tag}")
        print(f"  Factors : Skills={r.skills_match_score:.0f}  Semantic={r.semantic_score:.0f}  "
              f"Exp={r.experience_score:.0f}  Edu={r.education_score:.0f}  "
              f"Cert={r.certification_score:.0f}  Proj={r.project_score:.0f}")

        if r.matched_skills:
            print(f"  Matched : {r.matched_skills}")
        if r.cluster_matched_skills:
            pairs = [m["required"] + " -> " + m["found"] for m in r.cluster_matched_skills]
            print(f"  Cluster : {pairs}")
        if r.missing_skills:
            print(f"  Missing : {r.missing_skills}")
        if r.inferred_skills:
            print(f"  Inferred: {r.inferred_skills}")
        if r.interview_focus:
            print(f"  Focus   : {r.interview_focus}")
        if r.red_flags:
            print(f"  Flags   : {r.red_flags}")
        print(f"  Summary : {r.llm_summary}")

        # Collect issues for analysis
        if "perfect" in label.lower() or "strong match" in label.lower():
            if r.ats_score < 65:
                issues.append(f"WEAK SCORE on expected strong match: {label} -> {r.ats_score}")
        if "wrong domain" in label.lower() or "irrelevant" in label.lower() or "wrong role" in label.lower():
            if r.ats_score > 45:
                issues.append(f"TOO HIGH on expected weak match: {label} -> {r.ats_score}")
        if "under-qualified" in label.lower():
            if r.ats_score > 50:
                issues.append(f"TOO HIGH on under-qualified: {label} -> {r.ats_score}")

    # ── Summary table ──────────────────────────────────────────
    print()
    print("=" * 72)
    print("  SUMMARY TABLE")
    print("=" * 72)
    print(f"  {'Label':<52} {'Score':>6}  {'Recommendation'}")
    print(f"  {'-'*52} {'-'*6}  {'-'*16}")
    for label, r in all_results:
        flag = ""
        if r.ats_score >= 70:
            flag = "+ GOOD"
        elif r.ats_score >= 45:
            flag = "~ MID"
        else:
            flag = "- LOW"
        print(f"  {label:<52} {r.ats_score:>6.1f}  {r.llm_recommendation:<16}  {flag}")

    # ── Issues ──────────────────────────────────────────────────
    print()
    if issues:
        print("  SCORING ISSUES DETECTED:")
        for issue in issues:
            print(f"    ! {issue}")
    else:
        print("  NO SCORING ISSUES DETECTED — all results within expected ranges.")
    print()


if __name__ == "__main__":
    print("\n  Starting 16-case hybrid ATS test...\n")
    results = asyncio.run(run_all())
    print_report(results)
