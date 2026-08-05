"""
Department Manager — Add or remove departments from the terminal.

Usage:
    python manage_departments.py                  # Interactive menu
    python manage_departments.py list             # List all departments
    python manage_departments.py add "Finance"    # Quick add
    python manage_departments.py add "HR" "Human Resources department"
    python manage_departments.py remove "Finance" # Quick remove by name
"""

import sys
import uuid
from datetime import datetime, timezone

from src.config.database_config_mysql import db_manager
from src.models.models import Department


def get_session():
    return db_manager.get_db_session()


def list_departments():
    db = get_session()
    try:
        departments = db.query(Department).order_by(Department.name).all()
        if not departments:
            print("\n  No departments found.\n")
            return
        print(f"\n  {'#':<4} {'Name':<30} {'Description':<40} {'Created'}")
        print("  " + "-" * 100)
        for i, d in enumerate(departments, 1):
            created = d.created_at.strftime("%Y-%m-%d %H:%M") if d.created_at else "N/A"
            desc = (d.description or "")[:38]
            print(f"  {i:<4} {d.name:<30} {desc:<40} {created}")
        print(f"\n  Total: {len(departments)} department(s)\n")
    finally:
        db.close()


def add_department(name, description=None):
    db = get_session()
    try:
        existing = db.query(Department).filter(Department.name == name).first()
        if existing:
            print(f"\n  Department '{name}' already exists.\n")
            return False

        dept = Department(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            created_at=datetime.now(timezone.utc),
        )
        db.add(dept)
        db.commit()
        print(f"\n  Added department: {name}")
        if description:
            print(f"  Description: {description}")
        print(f"  ID: {dept.id}\n")
        return True
    except Exception as e:
        db.rollback()
        print(f"\n  Error: {e}\n")
        return False
    finally:
        db.close()


def remove_department(name):
    db = get_session()
    try:
        dept = db.query(Department).filter(Department.name == name).first()
        if not dept:
            print(f"\n  Department '{name}' not found.\n")
            return False

        # Check for linked records
        linked = []
        if dept.job_postings:
            linked.append(f"{len(dept.job_postings)} job posting(s)")
        if dept.hr_profiles:
            linked.append(f"{len(dept.hr_profiles)} HR profile(s)")
        if dept.interviewer_profiles:
            linked.append(f"{len(dept.interviewer_profiles)} interviewer profile(s)")

        if linked:
            print(f"\n  Warning: '{name}' has linked records: {', '.join(linked)}")
            confirm = input("  Delete anyway? (yes/no): ").strip().lower()
            if confirm != "yes":
                print("  Cancelled.\n")
                return False

        db.delete(dept)
        db.commit()
        print(f"\n  Removed department: {name}\n")
        return True
    except Exception as e:
        db.rollback()
        print(f"\n  Error: {e}\n")
        return False
    finally:
        db.close()


def interactive_menu():
    print("\n" + "=" * 50)
    print("       Department Manager")
    print("=" * 50)

    while True:
        print("\n  1. List departments")
        print("  2. Add department")
        print("  3. Remove department")
        print("  4. Exit")
        choice = input("\n  Choose (1-4): ").strip()

        if choice == "1":
            list_departments()

        elif choice == "2":
            name = input("  Department name: ").strip()
            if not name:
                print("  Name cannot be empty.")
                continue
            desc = input("  Description (optional, press Enter to skip): ").strip() or None
            add_department(name, desc)

        elif choice == "3":
            list_departments()
            name = input("  Department name to remove: ").strip()
            if not name:
                print("  Name cannot be empty.")
                continue
            remove_department(name)

        elif choice == "4":
            print("\n  Bye!\n")
            break

        else:
            print("  Invalid choice.")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        interactive_menu()
    elif args[0] == "list":
        list_departments()
    elif args[0] == "add" and len(args) >= 2:
        desc = args[2] if len(args) >= 3 else None
        add_department(args[1], desc)
    elif args[0] == "remove" and len(args) >= 2:
        remove_department(args[1])
    else:
        print(__doc__)
