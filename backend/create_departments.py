"""
Create departments in the recruitment database.
Usage: python create_departments.py
"""

import uuid
import pymysql
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": os.getenv("DB_PASSWORD", "1234"),
    "database": "recruitment_db",
}

DEPARTMENTS = [
    {"name": "Engineering", "description": "Software development, architecture, and technical operations"},
    {"name": "Data Science", "description": "Data analytics, machine learning, and AI research"},
    {"name": "Product", "description": "Product management, strategy, and roadmap planning"},
    {"name": "Design", "description": "UI/UX design, visual design, and user research"},
    {"name": "Marketing", "description": "Digital marketing, brand management, and growth"},
    {"name": "Sales", "description": "Sales operations, business development, and partnerships"},
    {"name": "Human Resources", "description": "Talent acquisition, employee relations, and HR operations"},
    {"name": "Finance", "description": "Accounting, financial planning, and budgeting"},
    {"name": "Operations", "description": "Business operations, logistics, and process optimization"},
    {"name": "Quality Assurance", "description": "Software testing, QA automation, and quality control"},
    {"name": "DevOps", "description": "Cloud infrastructure, CI/CD, and site reliability"},
    {"name": "Customer Support", "description": "Customer service, technical support, and success"},
]


def create_departments():
    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor()

    created = 0
    skipped = 0

    for dept in DEPARTMENTS:
        # Skip if department with same name already exists
        cursor.execute("SELECT COUNT(*) FROM departments WHERE name = %s", (dept["name"],))
        if cursor.fetchone()[0] > 0:
            print(f"  Skipped: {dept['name']} (already exists)")
            skipped += 1
            continue

        dept_id = str(uuid.uuid4())
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "INSERT INTO departments (id, name, description, created_at) VALUES (%s, %s, %s, %s)",
            (dept_id, dept["name"], dept["description"], now),
        )
        print(f"  Created: {dept['name']}")
        created += 1

    conn.commit()
    conn.close()
    print(f"\nDone. Created: {created}, Skipped: {skipped}, Total in list: {len(DEPARTMENTS)}")


if __name__ == "__main__":
    create_departments()
