# -*- coding: utf-8 -*-
"""
Migration: Add department-based interviewer assignment
- Adds interviewer_id to job_postings
- Adds job_id and candidate_id to interviews
- Seeds new departments (Data Science, HR Dept, Marketing, Finance)
- Creates InterviewerProfile for john@hireflow.com in Data Science dept
"""
import sys
import uuid
import pymysql

sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

try:
    conn = pymysql.connect(host='localhost', user='root', password='1234', database='recruitment_db')
    cur = conn.cursor()
except Exception as e:
    print(f"[ERROR] DB connection failed: {e}")
    sys.exit(1)

print("=== Step 1: Add columns ===")
cols = [
    ('job_postings', 'interviewer_id', 'VARCHAR(36) NULL'),
    ('interviews', 'job_id', 'VARCHAR(36) NULL'),
    ('interviews', 'candidate_id', 'VARCHAR(36) NULL'),
]
for table, col, coldef in cols:
    cur.execute(
        'SELECT COUNT(*) FROM information_schema.columns '
        'WHERE table_schema=%s AND table_name=%s AND column_name=%s',
        ('recruitment_db', table, col)
    )
    if cur.fetchone()[0] == 0:
        try:
            cur.execute(f'ALTER TABLE `{table}` ADD COLUMN `{col}` {coldef}, ALGORITHM=INSTANT')
            print(f'  Added {table}.{col}')
        except pymysql.err.OperationalError as e:
            if e.args[0] == 1060:  # Duplicate column
                print(f'  Already exists (caught): {table}.{col}')
            else:
                print(f'  [ERROR] {table}.{col}: {e}')
                sys.exit(1)
    else:
        print(f'  Already exists: {table}.{col}')
conn.commit()

print("\n=== Step 2: Add departments ===")
new_depts = [
    ('Data Science', 'Data Analytics, ML and Statistics team'),
    ('HR Department', 'Human Resources and Talent team'),
    ('Marketing', 'Marketing and Sales team'),
    ('Finance', 'Finance and Accounting team'),
]
dept_ids = {}
for name, desc in new_depts:
    cur.execute('SELECT id FROM departments WHERE name=%s', (name,))
    row = cur.fetchone()
    if not row:
        did = str(uuid.uuid4())
        cur.execute(
            'INSERT INTO departments (id, name, description, created_at) VALUES (%s, %s, %s, NOW())',
            (did, name, desc)
        )
        dept_ids[name] = did
        print(f'  Created: {name} -> {did}')
    else:
        dept_ids[name] = row[0]
        print(f'  Exists: {name} -> {row[0]}')
conn.commit()

cur.execute('SELECT id, name FROM departments')
print("\n  All departments:")
all_depts = {}
for row in cur.fetchall():
    all_depts[row[1]] = row[0]
    print(f'    {row[1]} | {row[0]}')

data_dept_id = all_depts.get('Data Science')

print("\n=== Step 3: Create interviewer profile for john@hireflow.com ===")
cur.execute('SELECT id, email FROM users WHERE email=%s', ('john@hireflow.com',))
john = cur.fetchone()
if john:
    print(f'  Found john: {john[0]}')
    cur.execute('SELECT id, department_id FROM interviewer_profiles WHERE user_id=%s', (john[0],))
    existing = cur.fetchone()
    expertise = '["Python", "SQL", "Statistics", "Machine Learning", "Data Visualization", "Pandas", "NumPy"]'
    if not existing:
        pid = str(uuid.uuid4())
        cur.execute(
            'INSERT INTO interviewer_profiles (id, user_id, department_id, employee_id, expertise_areas, years_experience, created_at) '
            'VALUES (%s, %s, %s, %s, %s, %s, NOW())',
            (pid, john[0], data_dept_id, 'INT-001', expertise, 5)
        )
        print(f'  Created profile: {pid} | dept: Data Science ({data_dept_id})')
    else:
        cur.execute(
            'UPDATE interviewer_profiles SET department_id=%s, expertise_areas=%s WHERE user_id=%s',
            (data_dept_id, expertise, john[0])
        )
        print(f'  Updated profile {existing[0]} -> Data Science dept')
    conn.commit()
else:
    print('  john@hireflow.com not found!')

print("\n=== Verification ===")
cur.execute(
    'SELECT ip.id, ip.department_id, u.email, d.name '
    'FROM interviewer_profiles ip '
    'JOIN users u ON ip.user_id=u.id '
    'LEFT JOIN departments d ON ip.department_id=d.id'
)
for row in cur.fetchall():
    print(f'  InterviewerProfile: {row[0][:8]}... | dept: {row[3]} | user: {row[2]}')

cur.execute('DESCRIBE job_postings')
cols_jp = [r[0] for r in cur.fetchall()]
print(f'  job_postings.interviewer_id: {"YES" if "interviewer_id" in cols_jp else "NO"}')

cur.execute('DESCRIBE interviews')
cols_iv = [r[0] for r in cur.fetchall()]
print(f'  interviews.job_id: {"YES" if "job_id" in cols_iv else "NO"}')
print(f'  interviews.candidate_id: {"YES" if "candidate_id" in cols_iv else "NO"}')

conn.close()
print("\nMigration complete!")
sys.exit(0)
