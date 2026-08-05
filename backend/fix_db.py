import pymysql

conn = pymysql.connect(host='localhost', user='root', password='1234', database='recruitment_db')
cur = conn.cursor()
cur.execute('SET FOREIGN_KEY_CHECKS=0')
tables = ['ats_interviews', 'followup_questions', 'question_evaluations',
          'candidate_stage_history', 'candidates', 'job_postings', 'offers',
          'system_config', 'ats_system', 'hr_tasks', 'assessment_rounds',
          'interviews', 'interview_questions', 'interview_feedback']
for t in tables:
    cur.execute(f'DROP TABLE IF EXISTS {t}')
    print('Dropped:', t)
cur.execute('SET FOREIGN_KEY_CHECKS=1')
conn.commit()
cur.execute('SHOW TABLES')
print('Remaining tables:', [r[0] for r in cur.fetchall()])
conn.close()
print('Done!')
