"""Seed the 11 builtin JD templates into jd_templates.

Idempotent: rows with the same (name, is_builtin=True, company_id IS NULL) are skipped.

Run from project root: `python scripts/seed_templates.py`
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.database_config_mysql import db_manager
from src.models.models import JDTemplate


def _skills(csv: str) -> str:
    return json.dumps([s.strip() for s in csv.split(",") if s.strip()])


# job_category: technical | non_technical | mixed
TEMPLATES = [
    {
        "name": "Frontend Developer", "category": "Engineering", "job_category": "technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 2,
        "required_skills": "React, JavaScript, TypeScript, HTML, CSS",
        "preferred_skills": "Next.js, Redux, Tailwind CSS, Git, REST APIs",
        "job_description": "Design and implement responsive, high-performance web interfaces using React and TypeScript. Collaborate with designers to translate UI/UX wireframes into functional components. Optimize application performance and ensure cross-browser compatibility. Participate in code reviews and contribute to frontend architecture decisions.",
    },
    {
        "name": "Backend Developer", "category": "Engineering", "job_category": "technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 3,
        "required_skills": "Python, FastAPI, PostgreSQL, REST APIs, Docker",
        "preferred_skills": "Redis, Celery, AWS, Kubernetes, CI/CD",
        "job_description": "Build and maintain scalable backend services and RESTful APIs using Python and FastAPI. Design efficient database schemas and write complex SQL queries. Implement authentication, authorization, and security best practices. Work closely with frontend teams to define API contracts and ensure system reliability.",
    },
    {
        "name": "Full Stack Developer", "category": "Engineering", "job_category": "technical",
        "role_level": "Senior", "employment_type": "Full-time", "min_experience_years": 4,
        "required_skills": "React, Node.js, Python, PostgreSQL, REST APIs",
        "preferred_skills": "TypeScript, Docker, AWS, GraphQL, Redis",
        "job_description": "Develop end-to-end web features spanning frontend UI and backend services. Architect robust full-stack solutions that are scalable and maintainable. Lead technical discussions, conduct code reviews, and mentor junior developers. Collaborate with product and design teams to deliver high-quality features on time.",
    },
    {
        "name": "DevOps Engineer", "category": "Engineering", "job_category": "technical",
        "role_level": "Senior", "employment_type": "Full-time", "min_experience_years": 4,
        "required_skills": "Docker, Kubernetes, CI/CD, Linux, Terraform",
        "preferred_skills": "AWS, GCP, Prometheus, Grafana, Ansible",
        "job_description": "Design, implement, and maintain CI/CD pipelines and cloud infrastructure. Manage containerized deployments using Docker and Kubernetes. Monitor system performance and ensure 99.9% uptime SLAs. Drive infrastructure-as-code practices and improve deployment velocity across engineering teams.",
    },
    {
        "name": "QA Engineer", "category": "Engineering", "job_category": "technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 2,
        "required_skills": "Selenium, Python, Test Automation, REST API Testing, SQL",
        "preferred_skills": "Cypress, Playwright, JIRA, Postman, Performance Testing",
        "job_description": "Design and execute comprehensive test plans covering functional, regression, and performance testing. Build and maintain automated test suites using Selenium and Python. Identify, document, and track defects through to resolution. Partner with developers to embed quality practices throughout the SDLC.",
    },
    {
        "name": "Mobile Developer", "category": "Engineering", "job_category": "technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 3,
        "required_skills": "React Native, JavaScript, TypeScript, iOS, Android",
        "preferred_skills": "Redux, Firebase, Expo, REST APIs, App Store deployment",
        "job_description": "Develop cross-platform mobile applications using React Native for iOS and Android. Implement smooth, native-like UI interactions and optimize app performance. Integrate third-party services, push notifications, and device APIs. Collaborate with the backend team to design efficient mobile API contracts.",
    },
    {
        "name": "Data Scientist", "category": "Data / AI", "job_category": "technical",
        "role_level": "Senior", "employment_type": "Full-time", "min_experience_years": 4,
        "required_skills": "Python, Machine Learning, Pandas, Scikit-learn, SQL",
        "preferred_skills": "TensorFlow, PyTorch, Spark, Tableau, MLflow",
        "job_description": "Build and deploy machine learning models to solve complex business problems. Analyze large datasets to extract actionable insights and drive data-driven decisions. Collaborate with engineering teams to productionize ML pipelines. Communicate findings to stakeholders through clear visualizations and reports.",
    },
    {
        "name": "Data Analyst", "category": "Data / AI", "job_category": "technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 2,
        "required_skills": "SQL, Python, Excel, Tableau, Data Visualization",
        "preferred_skills": "Power BI, Google Analytics, Looker, R, Statistics",
        "job_description": "Collect, clean, and analyze business data to identify trends and opportunities. Build dashboards and reports that empower stakeholders to make informed decisions. Translate business questions into analytical frameworks and present findings clearly. Collaborate with cross-functional teams to define KPIs and metrics.",
    },
    {
        "name": "ML Engineer", "category": "Data / AI", "job_category": "technical",
        "role_level": "Senior", "employment_type": "Full-time", "min_experience_years": 4,
        "required_skills": "Python, TensorFlow, PyTorch, MLOps, Docker",
        "preferred_skills": "Kubernetes, AWS SageMaker, Kafka, Feature Stores, ONNX",
        "job_description": "Design and implement scalable ML infrastructure and model serving pipelines. Work with data scientists to take models from experimentation to production. Build monitoring systems to track model performance and data drift. Champion MLOps best practices including automated retraining and A/B testing frameworks.",
    },
    {
        "name": "Product Manager", "category": "Product / Design", "job_category": "mixed",
        "role_level": "Senior", "employment_type": "Full-time", "min_experience_years": 5,
        "required_skills": "Product Strategy, Roadmapping, Agile, Stakeholder Management, Data Analysis",
        "preferred_skills": "Figma, SQL, A/B Testing, OKRs, JIRA",
        "job_description": "Define product vision, strategy, and roadmap in alignment with business goals. Gather and prioritize requirements from customers, engineering, and leadership. Own the product lifecycle from ideation through launch and iteration. Use data and user research to continuously improve the product experience.",
    },
    {
        "name": "UX / UI Designer", "category": "Product / Design", "job_category": "mixed",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 3,
        "required_skills": "Figma, User Research, Wireframing, Prototyping, Design Systems",
        "preferred_skills": "Adobe XD, Usability Testing, HTML/CSS, Motion Design, Accessibility",
        "job_description": "Create intuitive, visually compelling user experiences from concept to high-fidelity prototypes. Conduct user research and usability testing to validate design decisions. Maintain and evolve the design system ensuring consistency across products. Collaborate closely with product managers and engineers to ship polished features.",
    },
    {
        "name": "Business Analyst", "category": "Business", "job_category": "non_technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 3,
        "required_skills": "Requirements Analysis, SQL, Process Mapping, Stakeholder Management, Documentation",
        "preferred_skills": "JIRA, Tableau, Agile, Confluence, Risk Analysis",
        "job_description": "Elicit, document, and validate business requirements from stakeholders. Map current-state processes and design future-state improvements. Bridge the gap between business needs and technical solutions through clear specifications. Track project progress and manage scope to ensure on-time, on-budget delivery.",
    },
    # A few non-technical defaults so users see relevant options when picking Non-Technical.
    {
        "name": "HR Recruiter", "category": "People", "job_category": "non_technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 2,
        "required_skills": "Sourcing, Screening, Interviewing, ATS, Communication",
        "preferred_skills": "Boolean Search, LinkedIn Recruiter, Negotiation, Employer Branding",
        "job_description": "Own the end-to-end recruitment lifecycle: sourcing, screening, scheduling, and closing candidates. Partner with hiring managers to understand role requirements and define ideal candidate profiles. Maintain a healthy pipeline and provide a great candidate experience. Track funnel metrics and continuously improve sourcing strategies.",
    },
    {
        "name": "Sales Executive", "category": "Sales", "job_category": "non_technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 2,
        "required_skills": "B2B Sales, Lead Generation, CRM, Negotiation, Communication",
        "preferred_skills": "Salesforce, HubSpot, Outbound Prospecting, Account Management",
        "job_description": "Drive new business by prospecting, qualifying, and closing deals with mid-market customers. Build and manage a healthy sales pipeline using CRM tools. Conduct discovery calls, product demos, and negotiate contract terms. Partner with marketing and customer success to ensure smooth handoffs and retention.",
    },
    {
        "name": "Customer Success Manager", "category": "Customer", "job_category": "non_technical",
        "role_level": "Mid-level", "employment_type": "Full-time", "min_experience_years": 3,
        "required_skills": "Customer Relationship Management, Account Management, Communication, Problem-Solving",
        "preferred_skills": "Salesforce, Gainsight, SaaS, Upselling, Onboarding",
        "job_description": "Own a portfolio of customer accounts and drive adoption, retention, and expansion. Conduct quarterly business reviews and translate customer goals into actionable success plans. Be the customer's voice internally and partner with product/engineering on feedback loops. Identify upsell opportunities and reduce churn.",
    },
]


def main():
    session = db_manager.get_db_session()
    inserted = 0
    skipped = 0
    try:
        for t in TEMPLATES:
            existing = (
                session.query(JDTemplate)
                .filter(
                    JDTemplate.name == t["name"],
                    JDTemplate.is_builtin == True,  # noqa: E712
                    JDTemplate.company_id.is_(None),
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            row = JDTemplate(
                company_id=None,
                created_by=None,
                name=t["name"],
                category=t["category"],
                job_category=t["job_category"],
                role_level=t["role_level"],
                employment_type=t["employment_type"],
                min_experience_years=t["min_experience_years"],
                required_skills=_skills(t["required_skills"]),
                preferred_skills=_skills(t["preferred_skills"]),
                job_description=t["job_description"],
                is_builtin=True,
                is_shared=True,
                source="builtin",
            )
            session.add(row)
            inserted += 1

        session.commit()
        total = session.query(JDTemplate).count()
        print(f"[+] Inserted {inserted}, skipped {skipped}. Total jd_templates rows: {total}")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
