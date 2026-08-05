"""Seed a superadmin, HR, and interviewer.

Run from project root: `python scripts/seed_users.py`
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config.database_config_mysql import db_manager
from src.models.models import User, Company, HRProfile, InterviewerProfile, Department
from src.auth import get_password_hash

DEFAULT_PASSWORD = "Password@123"

SEED = [
    {
        "email": "superadmin@hireflow.local",
        "role": "superadmin",
        "first_name": "Super",
        "last_name": "Admin",
        "phone": "+10000000001",
    },
    {
        "email": "hr@hireflow.local",
        "role": "hr",
        "first_name": "Demo",
        "last_name": "HR",
        "phone": "+10000000002",
    },
    {
        "email": "interviewer@hireflow.local",
        "role": "interviewer",
        "first_name": "Demo",
        "last_name": "Interviewer",
        "phone": "+10000000003",
    },
]


def main():
    session = db_manager.get_db_session()
    try:
        company = session.query(Company).filter(Company.slug == "demo").first()
        if not company:
            company = Company(name="Demo Company", slug="demo")
            session.add(company)
            session.flush()
            print(f"[+] Created company: {company.name} ({company.id})")
        else:
            print(f"[=] Reusing company: {company.name} ({company.id})")

        # The superadmin belongs to the platform's own org (Innomax), not the demo
        # company — and never to a NULL company. Resolve/create it once.
        from src.tenancy.platform_company import ensure_platform_company
        platform_company = ensure_platform_company(session)
        print(f"[=] Platform company: {platform_company.name} ({platform_company.id})")

        department = (
            session.query(Department)
            .filter(Department.company_id == company.id, Department.name == "General")
            .first()
        )
        if not department:
            department = Department(
                company_id=company.id, name="General", description="Default department"
            )
            session.add(department)
            session.flush()
            print(f"[+] Created department: {department.name} ({department.id})")

        password_hash = get_password_hash(DEFAULT_PASSWORD)

        created = []
        for entry in SEED:
            existing = session.query(User).filter(User.email == entry["email"]).first()
            if existing:
                print(f"[=] User already exists: {entry['email']} (role={existing.role})")
                continue

            user = User(
                email=entry["email"],
                password_hash=password_hash,
                role=entry["role"],
                first_name=entry["first_name"],
                last_name=entry["last_name"],
                phone=entry["phone"],
                is_active=True,
                email_verified=False,
                company_id=platform_company.id,
            )
            session.add(user)
            session.flush()

            if entry["role"] == "hr":
                session.add(
                    HRProfile(
                        user_id=user.id,
                        department_id=department.id,
                        employee_id="HR-001",
                    )
                )
            elif entry["role"] == "interviewer":
                session.add(
                    InterviewerProfile(
                        user_id=user.id,
                        department_id=department.id,
                        employee_id="INT-001",
                        expertise_areas="[]",
                        years_experience=3,
                    )
                )

            created.append((entry["role"], entry["email"], user.id))
            print(f"[+] Created {entry['role']}: {entry['email']} (id={user.id})")

        session.commit()

        print("\n=== Summary ===")
        print(f"Password (all accounts): {DEFAULT_PASSWORD}")
        print(f"Company: {company.name} (id={company.id})")
        print("Accounts:")
        for role, email, uid in created:
            print(f"  - {role:<11} {email:<32} id={uid}")
        if not created:
            print("  (no new accounts — all already existed)")
        print("\nNote: email_verified=False. Each user must complete OTP")
        print("verification before logging in (use the resend-OTP endpoint).")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
