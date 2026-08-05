"""
Create all database tables from SQLAlchemy ORM models.
Run this after dropping old mismatched tables.
"""
import sys
sys.path.insert(0, '.')

import pymysql
from sqlalchemy import text

# Drop old tables first, then recreate via ORM
conn = pymysql.connect(host='localhost', user='root', password='root', database='recruitment_db')
cur = conn.cursor()
cur.execute('SET FOREIGN_KEY_CHECKS=0')
tables = [
    'interview_questions', 'interview_feedback', 'interviews',
    'ats_results', 'offers', 'candidate_stage_history',
    'hr_tasks', 'assessment_rounds', 'candidates', 'job_postings',
    'system_config', 'ats_interviews', 'ats_system',
    'followup_questions', 'question_evaluations'
]
for t in tables:
    cur.execute(f'DROP TABLE IF EXISTS {t}')
    print(f'  Dropped: {t}')
cur.execute('SET FOREIGN_KEY_CHECKS=1')
conn.commit()
cur.execute('SHOW TABLES')
remaining = [r[0] for r in cur.fetchall()]
print(f'Remaining: {remaining}')
conn.close()

# Now create all tables via ORM models
from src.config.database_config_mysql import db_manager
from src.models.models import Base
from src.models.question_models import Question, QuestionType, DifficultyLevel

print('\nCreating tables from ORM models...')
Base.metadata.create_all(db_manager.engine)
print('Done! Tables created:')

conn2 = pymysql.connect(host='localhost', user='root', password='root', database='recruitment_db')
cur2 = conn2.cursor()
cur2.execute('SHOW TABLES')
all_tables = [r[0] for r in cur2.fetchall()]
print(all_tables)
conn2.close()
